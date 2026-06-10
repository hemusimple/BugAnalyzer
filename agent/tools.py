"""
tools.py — All tools available to the ReAct orchestrator.
Each tool is an async function returning a string result.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from loguru import logger

from config import settings
from agent.code_indexer import registry
from agent.log_parser import LogSession, extract_relevant_window, detect_layers_from_tags
from agent.architecture_analyzer import get_arch_map, build_blame_context


# ---------------------------------------------------------------- Tool registry

TOOLS: dict[str, dict] = {}


def tool(name: str, description: str):
    """Decorator to register a tool."""
    def decorator(fn: Callable):
        TOOLS[name] = {"fn": fn, "description": description, "name": name}
        return fn
    return decorator


def tools_prompt() -> str:
    """Return tool descriptions for the system prompt."""
    lines = []
    for name, meta in TOOLS.items():
        lines.append(f"- **{name}**: {meta['description']}")
    return "\n".join(lines)


async def call_tool(name: str, args: dict, context: "AnalysisContext") -> str:
    if name not in TOOLS:
        return f"ERROR: Unknown tool '{name}'"
    try:
        result = await TOOLS[name]["fn"](args, context)
        return str(result)
    except Exception as e:
        logger.exception(f"Tool {name} failed: {e}")
        return f"ERROR in {name}: {e}"


# ---------------------------------------------------------------- Context

class AnalysisContext:
    """Shared state passed to all tools during an analysis run."""

    def __init__(self, log_session: LogSession | None = None, log_path: Path | None = None):
        self.log_session = log_session
        self.log_path = log_path
        self.extra_context: str = ""   # user-provided extra info via chat
        self.visited_files: set[str] = set()
        self.notes: list[str] = []     # agent's working notes


# ---------------------------------------------------------------- Tools

@tool("search_code",
      "Search the indexed codebase for relevant code. Args: {\"query\": \"...\", \"top_k\": 5}")
async def search_code(args: dict, ctx: AnalysisContext) -> str:
    query = args.get("query", "")
    top_k = int(args.get("top_k", 6))
    results = registry.search_all(query, top_k=top_k)
    if not results:
        return "No code results found."
    parts = []
    for r in results:
        parts.append(
            f"--- FILE: {r['file']} (lines {r['start_line']}-{r['end_line']}, score={r['score']:.2f}) ---\n{r['text'][:800]}"
        )
    return "\n\n".join(parts)


@tool("read_file",
      "Read a specific file from the indexed repos. Args: {\"file\": \"relative/path/to/File.kt\"}")
async def read_file(args: dict, ctx: AnalysisContext) -> str:
    file_rel = args.get("file", "")
    # Search in all repos
    for idx in registry.all_indexes():
        repo_dir = settings.REPOS_DIR / idx.repo_name.replace("/", "__")
        candidate = repo_dir / file_rel
        if candidate.exists():
            ctx.visited_files.add(file_rel)
            return candidate.read_text(encoding="utf-8", errors="replace")[:4000]
    return f"File not found: {file_rel}"


@tool("lookup_log_tag",
      "Find source files that define or use a specific Android log tag. Args: {\"tag\": \"MyTag\"}")
async def lookup_log_tag(args: dict, ctx: AnalysisContext) -> str:
    tag = args.get("tag", "")
    files = registry.lookup_tag_all(tag)
    if not files:
        return f"No files found for log tag: {tag}"
    return f"Files for tag '{tag}':\n" + "\n".join(f"  - {f}" for f in files[:10])


@tool("search_logs",
      "Search the current log file for lines matching a query. Args: {\"query\": \"timeout\", \"context_lines\": 10}")
async def search_logs(args: dict, ctx: AnalysisContext) -> str:
    if not ctx.log_session:
        return "No log file loaded."
    query = args.get("query", "").lower()
    context_lines = int(args.get("context_lines", 5))
    hits = [e for e in ctx.log_session.entries if query in (e.raw or "").lower()]
    if not hits:
        return f"No log lines matching: {query}"
    result_lines = []
    entry_nos = {e.line_no for e in hits}
    for e in ctx.log_session.entries:
        in_window = any(abs(e.line_no - h) <= context_lines for h in entry_nos)
        if e.line_no in entry_nos or in_window:
            result_lines.append(e.raw)
        if len(result_lines) > 200:
            break
    return "\n".join(result_lines)


@tool("get_log_summary",
      "Get a summary of the loaded log file: error counts, tags seen, exceptions. Args: {}")
async def get_log_summary(args: dict, ctx: AnalysisContext) -> str:
    if not ctx.log_session:
        return "No log file loaded."
    s = ctx.log_session.summary()
    layer_map = detect_layers_from_tags(s["unique_tags"])
    return json.dumps({**s, "layer_distribution": layer_map}, indent=2)


@tool("get_architecture_map",
      "Get the indexed app's architecture: layers, components, data flows. Args: {}")
async def get_architecture_map(args: dict, ctx: AnalysisContext) -> str:
    arch = get_arch_map()
    return arch.summary_text()


@tool("blame_analysis",
      "Given a set of tags from the log, identify which architectural layer is responsible. Args: {\"tags\": [\"ServiceA\", \"RepoB\"]}")
async def blame_analysis(args: dict, ctx: AnalysisContext) -> str:
    tags = args.get("tags", [])
    if not tags and ctx.log_session:
        tags = list(ctx.log_session.tags_seen)[:30]
    arch = get_arch_map()
    return build_blame_context(arch, tags)


@tool("get_errors_and_exceptions",
      "Return all error/exception lines from the loaded log file. Args: {}")
async def get_errors_and_exceptions(args: dict, ctx: AnalysisContext) -> str:
    if not ctx.log_session:
        return "No log file loaded."
    lines = []
    for e in ctx.log_session.errors[:50]:
        lines.append(e.raw)
    for e in ctx.log_session.exceptions[:30]:
        lines.append(e.raw)
    for e in ctx.log_session.timeouts[:20]:
        lines.append(e.raw)
    if not lines:
        return "No errors, exceptions or timeouts found."
    return "\n".join(dict.fromkeys(lines))  # dedup preserving order


@tool("note",
      "Save a reasoning note during analysis. Args: {\"text\": \"my observation\"}")
async def note(args: dict, ctx: AnalysisContext) -> str:
    text = args.get("text", "")
    ctx.notes.append(text)
    return f"Note saved: {text}"


@tool("index_stats",
      "Show how many files/chunks are indexed per repo. Args: {}")
async def index_stats(args: dict, ctx: AnalysisContext) -> str:
    stats = registry.stats()
    if not stats:
        return "No repos indexed yet."
    lines = ["Indexed repos:"]
    for repo, s in stats.items():
        lines.append(f"  {repo}: {s['chunks']} chunks, {s['log_tags']} log tags")
    return "\n".join(lines)
