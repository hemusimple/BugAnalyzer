"""
api/main.py — FastAPI app: REST endpoints + WebSocket + Jira webhook
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from loguru import logger

from config import settings
from agent.jira_client import (
    get_jira_client,
    verify_webhook_signature,
    extract_issue_key,
    extract_issue_summary,
    extract_issue_description,
)
from agent.repo_manager import add_repo, startup_index_repos, load_all_existing
from agent.orchestrator import get_agent, AgentStep
from agent.log_parser import parse_log_file
from agent.code_indexer import registry
from agent.llm_client import get_llm_client, get_session_usage, reset_session_usage
from agent.architecture_analyzer import get_arch_map, analyze_repos

app = FastAPI(title="Android Log Analyzer", version="1.0")

# Serve UI
UI_PATH = Path(__file__).parent.parent / "ui" / "index.html"


@app.on_event("startup")
async def on_startup():
    logger.info("Starting up — loading existing indexes...")
    await startup_index_repos()
    logger.info("Startup complete.")


# ============================================================ UI
@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    if UI_PATH.exists():
        return HTMLResponse(UI_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>UI not found</h1>")


# ============================================================ Jira webhook
@app.post("/webhook/jira")
async def jira_webhook(request: Request):
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature", "")
    if not verify_webhook_signature(body, sig):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("webhookEvent", "")
    logger.info(f"Jira webhook: {event}")

    # Handle issue created / updated
    if event in ("jira:issue_created", "jira:issue_updated"):
        issue_key = extract_issue_key(payload)
        if not issue_key:
            return JSONResponse({"status": "no issue key"})

        summary = extract_issue_summary(payload)
        description = extract_issue_description(payload)

        # Fire-and-forget analysis
        asyncio.create_task(
            _auto_analyze_jira_issue(issue_key, summary, description)
        )
        return JSONResponse({"status": "accepted", "issue": issue_key})

    return JSONResponse({"status": "ignored"})


async def _auto_analyze_jira_issue(issue_key: str, summary: str, description: str):
    """Background task: download log attachments + run analysis + post comment."""
    try:
        jira = get_jira_client()
        log_paths = await jira.download_all_attachments(issue_key)
        if not log_paths:
            logger.info(f"No log attachments for {issue_key}")
            return

        agent = get_agent()
        for log_path in log_paths:
            result = await agent.analyze(
                log_path=log_path,
                issue_summary=summary,
                issue_description=description,
            )
            comment = _format_jira_comment(result)
            await jira.post_analysis_comment(issue_key, comment)

    except Exception as e:
        logger.exception(f"Auto-analysis failed for {issue_key}: {e}")


def _format_jira_comment(result) -> str:
    verdict_emoji = {"APP BUG": "🔴", "EXTERNAL BUG": "🟡", "INCONCLUSIVE": "⚪"}.get(result.verdict, "⚪")
    team_line = f"Recommended Team: {result.recommended_team}\n" if result.recommended_team else ""
    return (
        f"{verdict_emoji} Verdict: {result.verdict}\n"
        f"Blame Layer: {result.blame_layer or 'Unknown'}\n"
        f"{team_line}"
        f"\n{result.final_answer}\n\n"
        f"--- Token usage: {result.token_usage} ---"
    )


# ============================================================ Repo management
@app.post("/api/repos/add")
async def add_repo_endpoint(request: Request):
    data = await request.json()
    url = data.get("url", "").strip()
    if not url:
        raise HTTPException(400, "Missing 'url'")
    result = await add_repo(url)
    return JSONResponse(result)


@app.get("/api/repos")
async def list_repos():
    return JSONResponse({"repos": registry.stats()})


@app.post("/api/repos/rebuild")
async def rebuild_arch():
    arch = analyze_repos()
    return JSONResponse({"components": len(arch.components), "layers": list(arch.layers.keys())})


# ============================================================ Log upload
@app.post("/api/analyze")
async def analyze_log(
    log_file: UploadFile = File(None),
    issue_summary: str = Form(""),
    issue_description: str = Form(""),
    extra_context: str = Form(""),
):
    log_path = None
    if log_file:
        dest = settings.LOGS_DIR / log_file.filename
        dest.write_bytes(await log_file.read())
        log_path = dest

    agent = get_agent()
    result = await agent.analyze(
        log_path=log_path,
        issue_summary=issue_summary,
        issue_description=issue_description,
        extra_context=extra_context,
    )
    return JSONResponse({
        "verdict": result.verdict,
        "blame_layer": result.blame_layer,
        "recommended_team": result.recommended_team,
        "final_answer": result.final_answer,
        "llm_raw": result.llm_raw,
        "steps": [{"type": s.type, "content": s.content} for s in result.steps],
        "token_usage": result.token_usage,
    })


# ============================================================ WebSocket streaming
@app.websocket("/ws/analyze")
async def ws_analyze(websocket: WebSocket):
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        mode = data.get("mode", "analyze")

        if mode == "analyze":
            log_filename = data.get("log_filename", "")
            log_path = settings.LOGS_DIR / log_filename if log_filename else None
            issue_summary = data.get("issue_summary", "")
            issue_description = data.get("issue_description", "")
            extra_context = data.get("extra_context", "")

            agent = get_agent()

            async def on_step(step: AgentStep):
                await websocket.send_json({
                    "type": "step",
                    "step_type": step.type,
                    "content": step.content,
                    "tool": step.tool_name,
                })

            result = await agent.analyze(
                log_path=log_path,
                issue_summary=issue_summary,
                issue_description=issue_description,
                extra_context=extra_context,
                on_step=on_step,
            )

            await websocket.send_json({
                "type": "done",
                "verdict": result.verdict,
                "blame_layer": result.blame_layer,
                "recommended_team": result.recommended_team,
                "final_answer": result.final_answer,
                "llm_raw": result.llm_raw,
                "token_usage": result.token_usage,
                "steps": [{"type": s.type, "content": s.content} for s in result.steps],
            })

        elif mode == "chat":
            history = data.get("history", [])
            message = data.get("message", "")
            log_filename = data.get("log_filename", "")
            log_path = settings.LOGS_DIR / log_filename if log_filename else None

            agent = get_agent()
            full_response = []
            async for token in agent.chat_followup(history, message, log_path):
                full_response.append(token)
                await websocket.send_json({"type": "token", "content": token})

            await websocket.send_json({
                "type": "chat_done",
                "full": "".join(full_response),
                "token_usage": get_session_usage().to_dict(),
            })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


# ============================================================ Status endpoints
@app.get("/api/status")
async def status():
    llm = get_llm_client()
    available = await llm.is_available()
    models = await llm.list_models() if available else []
    return JSONResponse({
        "llm_available": available,
        "llm_model": settings.OLLAMA_MODEL,
        "available_models": models,
        "repos_indexed": len(registry._indexes),
        "token_usage": get_session_usage().to_dict(),
    })


@app.post("/api/token_usage/reset")
async def reset_tokens():
    reset_session_usage()
    return JSONResponse({"status": "reset"})


@app.get("/api/logs")
async def list_logs():
    logs = [f.name for f in settings.LOGS_DIR.iterdir() if f.is_file()]
    return JSONResponse({"logs": logs})


@app.delete("/api/logs/{filename}")
async def delete_log(filename: str):
    p = settings.LOGS_DIR / filename
    if p.exists():
        p.unlink()
    return JSONResponse({"deleted": filename})


if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level=settings.LOG_LEVEL,
        reload=False,
    )
