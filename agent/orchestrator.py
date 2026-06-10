"""
orchestrator.py — ReAct agent loop.
Think → Act (tool call) → Observe → Repeat → Final Answer
Uses XML tags for structured output parsing.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Optional

from loguru import logger

from agent.llm_client import get_llm_client, get_session_usage
from agent.tools import TOOLS, call_tool, tools_prompt, AnalysisContext
from agent.log_parser import LogSession, parse_log_file, extract_relevant_window, detect_layers_from_tags
from agent.architecture_analyzer import get_arch_map


SYSTEM_PROMPT = """You are an expert Android/mobile log analyzer agent. Your goal is to diagnose bugs by analyzing log files against the app's source code and architecture.

## Your Role
- Determine whether a bug is from the **app side** or an **external dependency** (backend service, system, OS, hardware)
- Identify **which architectural layer** is responsible (UI, ViewModel, Repository, Service, HAL, Android Framework, System UI, HAL/BSP, Backend/Server)
- Provide specific evidence: exact log lines, code file locations, class names
- Explain data flows: e.g. "Service returned null → Repository timed out → ViewModel showed error"
- Always recommend **which team** should investigate based on the blame layer:
  - APP BUG + UI/Compose → App UI Team
  - APP BUG + ViewModel/UseCase → App Logic Team
  - APP BUG + Repository/DataSource → App Data Team
  - APP BUG + Service/Manager → App Service Team
  - EXTERNAL BUG + Android system tags (ActivityManager, WindowManager, SystemUI, etc.) → Android Framework Team
  - EXTERNAL BUG + system UI tags (StatusBar, NavigationBar, etc.) → System UI Team
  - EXTERNAL BUG + HAL/VHAL/CarService → HAL/BSP Team
  - EXTERNAL BUG + network/backend errors → Backend/Server Team
  - EXTERNAL BUG + OEM/vendor tags → OEM Platform Team

## Architecture Understanding
Apps typically follow layered architecture:
- **UI/View** → **ViewModel** → **UseCase/Repository** → **Service/DataSource** → **Backend/HAL**
- Data flows down; events/callbacks flow up
- Observers/LiveData/Flow are notified when data changes
- A failure at any layer propagates upward

## ReAct Loop
Think through the problem step by step. Use XML tags:

<think>Your reasoning and plan</think>
<action>
{
  "tool": "tool_name",
  "args": { ... }
}
</action>

After each tool result, continue thinking and acting. When you have enough information:

<final_answer>
## Verdict
[APP BUG / EXTERNAL BUG / INCONCLUSIVE]

## Root Cause
...

## Evidence
...

## Blame Layer
[e.g. UI / ViewModel / Repository / Service / Android Framework / System UI / HAL/BSP / Backend/Server]

## Recommended Team
[e.g. App UI Team / Android Framework Team / System UI Team / HAL/BSP Team / Backend/Server Team]

## Recommendation
...
</final_answer>

## Available Tools
{tools}

## Rules
- Always start by calling `get_log_summary` and `get_errors_and_exceptions`
- Then look up specific tags with `lookup_log_tag` to find source files
- Use `search_code` to understand the code logic around those tags
- Use `blame_analysis` to map log tags to architecture layers
- Be specific — cite log line content and file names in your answer
- If extra context is provided by the user, incorporate it
- Max 12 tool calls per analysis
"""

# Parse tool calls from LLM output
ACTION_RE = re.compile(r"<action>\s*(\{.*?\})\s*</action>", re.DOTALL)
THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
FINAL_RE = re.compile(r"<final_answer>(.*?)</final_answer>", re.DOTALL)


@dataclass
class AgentStep:
    type: str          # "think" | "action" | "observation" | "final"
    content: str
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)


@dataclass
class AgentResult:
    steps: list[AgentStep] = field(default_factory=list)
    final_answer: str = ""
    llm_raw: str = ""
    token_usage: dict = field(default_factory=dict)
    verdict: str = ""     # APP BUG / EXTERNAL BUG / INCONCLUSIVE
    blame_layer: str = ""
    recommended_team: str = ""


class LogAnalyzerAgent:

    def __init__(self):
        self.llm = get_llm_client()

    async def analyze(
        self,
        log_path: Path | None,
        issue_summary: str = "",
        issue_description: str = "",
        extra_context: str = "",
        on_step: Optional[callable] = None,   # callback(AgentStep) for streaming
    ) -> AgentResult:
        """
        Run full ReAct analysis on a log file.
        on_step: optional async callback called after each step.
        """
        result = AgentResult()

        # 1. Parse log file
        log_session: Optional[LogSession] = None
        if log_path and log_path.exists():
            log_session = parse_log_file(log_path)
            logger.info(f"Parsed log: {log_path.name} — {len(log_session.entries)} lines")

        ctx = AnalysisContext(log_session=log_session, log_path=log_path)
        ctx.extra_context = extra_context

        # 2. Build initial user message
        log_info = ""
        if log_session:
            summary = log_session.summary()
            top_errors = "\n".join(summary["error_lines"][:10])
            log_info = f"""
## Log File: {log_path.name if log_path else 'unknown'}
Total lines: {summary['total_lines']}
Errors: {summary['error_count']} | Exceptions: {summary['exception_count']} | Timeouts: {summary['timeout_count']}
Tags seen: {', '.join(summary['unique_tags'][:20])}

### Sample Error Lines:
{top_errors}
"""

        user_msg = f"""## Issue to Analyze

**Summary**: {issue_summary or 'No summary provided'}

**Description**: {issue_description or 'No description provided'}

{log_info}

{('**Extra context from user**: ' + extra_context) if extra_context else ''}

Please analyze this issue step by step using the available tools.
"""

        # 3. ReAct loop
        messages = [{"role": "user", "content": user_msg}]
        system = SYSTEM_PROMPT.replace("{tools}", tools_prompt())
        full_llm_output = []
        max_steps = 8

        for step_num in range(max_steps):
            # Stream LLM response
            chunk_buf = []
            async for token in await self.llm.chat(messages, system=system, stream=True):
                chunk_buf.append(token)

            response_text = "".join(chunk_buf)
            full_llm_output.append(response_text)

            # Parse thoughts
            for m in THINK_RE.finditer(response_text):
                step = AgentStep(type="think", content=m.group(1).strip())
                result.steps.append(step)
                if on_step:
                    await on_step(step)

            # Parse tool calls
            action_matches = list(ACTION_RE.finditer(response_text))

            if not action_matches:
                # No more actions — check for final answer
                fa = FINAL_RE.search(response_text)
                if fa:
                    step = AgentStep(type="final", content=fa.group(1).strip())
                    result.steps.append(step)
                    result.final_answer = fa.group(1).strip()
                    if on_step:
                        await on_step(step)
                    break
                # If no final_answer tag either, treat whole response as final
                result.final_answer = response_text
                break

            # Execute tool calls
            observations = []
            for action_match in action_matches:
                try:
                    action_data = json.loads(action_match.group(1))
                except json.JSONDecodeError as e:
                    observations.append(f"ERROR parsing action JSON: {e}")
                    continue

                tool_name = action_data.get("tool", "")
                tool_args = action_data.get("args", {})

                step = AgentStep(
                    type="action",
                    content=f"Calling {tool_name}({json.dumps(tool_args)})",
                    tool_name=tool_name,
                    tool_args=tool_args,
                )
                result.steps.append(step)
                if on_step:
                    await on_step(step)

                observation = await call_tool(tool_name, tool_args, ctx)
                obs_step = AgentStep(
                    type="observation",
                    content=f"[{tool_name}] → {observation[:600]}",
                )
                result.steps.append(obs_step)
                # Truncate each observation to avoid context bloat
                observations.append(f"[{tool_name} result]\n{observation[:1200]}")
                if on_step:
                    await on_step(obs_step)

            # On the last two steps, stop accepting tool calls and demand a final answer
            wrap_up = step_num >= max_steps - 2
            next_user_content = "\n\n".join(observations)
            if wrap_up:
                next_user_content += (
                    "\n\nYou MUST now write your <final_answer>. "
                    "Do NOT call any more tools. "
                    "Include: Verdict, Root Cause, Evidence, Blame Layer, Recommended Team, Recommendation."
                )
            else:
                next_user_content += "\n\nContinue your analysis."

            # Append to conversation; keep only last 6 messages to cap context growth
            messages.append({"role": "assistant", "content": response_text[:2000]})
            messages.append({"role": "user", "content": next_user_content})
            if len(messages) > 6:
                messages = [messages[0]] + messages[-5:]

        # 4. If loop exhausted without a final answer, force one synthesis call
        if not result.final_answer:
            messages.append({
                "role": "user",
                "content": (
                    "Based on all the evidence gathered above, write your <final_answer> now. "
                    "Include Verdict (APP BUG / EXTERNAL BUG / INCONCLUSIVE), Root Cause, "
                    "Evidence, Blame Layer, Recommended Team, and Recommendation."
                ),
            })
            chunk_buf = []
            async for token in await self.llm.chat(messages, system=system, stream=True):
                chunk_buf.append(token)
            forced = "".join(chunk_buf)
            full_llm_output.append(forced)
            fa = FINAL_RE.search(forced)
            result.final_answer = fa.group(1).strip() if fa else forced

        # 5. Extract structured verdict from final answer
        result.llm_raw = "\n\n---\n\n".join(full_llm_output)
        result.token_usage = get_session_usage().to_dict()
        result.verdict = _extract_verdict(result.final_answer)
        result.blame_layer = _extract_blame_layer(result.final_answer)
        result.recommended_team = _extract_recommended_team(result.final_answer)

        return result

    async def chat_followup(
        self,
        history: list[dict],
        user_message: str,
        log_path: Path | None = None,
    ) -> AsyncIterator[str]:
        """
        Continue the analysis with follow-up questions.
        Streams tokens.
        """
        log_session = None
        if log_path and log_path.exists():
            log_session = parse_log_file(log_path)

        ctx = AnalysisContext(log_session=log_session, log_path=log_path)

        messages = history + [{"role": "user", "content": user_message}]
        system = SYSTEM_PROMPT.replace("{tools}", tools_prompt())

        async for token in await self.llm.chat(messages, system=system, stream=True):
            yield token


def _extract_verdict(text: str) -> str:
    for line in text.splitlines():
        ll = line.upper()
        if "APP BUG" in ll:
            return "APP BUG"
        if "EXTERNAL BUG" in ll or "NOT APP" in ll or "SERVICE BUG" in ll:
            return "EXTERNAL BUG"
        if "INCONCLUSIVE" in ll:
            return "INCONCLUSIVE"
    return "INCONCLUSIVE"


def _extract_blame_layer(text: str) -> str:
    layers = [
        "System UI", "Android Framework", "HAL/BSP", "Backend/Server",
        "UI", "ViewModel", "UseCase", "Repository", "DataSource",
        "Service", "HAL", "Backend", "Network", "External",
    ]
    for layer in layers:
        if (
            f"**{layer}**" in text
            or f"## {layer}" in text
            or f"Blame Layer: {layer}" in text
            or f"Blame Layer:**{layer}**" in text
            or layer.upper() in text.upper().split("BLAME LAYER")[-1][:80]
        ):
            return layer
    return "Unknown"


def _extract_recommended_team(text: str) -> str:
    teams = [
        "App UI Team", "App Logic Team", "App Data Team", "App Service Team",
        "Android Framework Team", "System UI Team", "HAL/BSP Team",
        "Backend/Server Team", "OEM Platform Team",
    ]
    upper = text.upper()
    for team in teams:
        if team.upper() in upper:
            return team
    return ""


# Singleton
_agent: Optional[LogAnalyzerAgent] = None


def get_agent() -> LogAnalyzerAgent:
    global _agent
    if _agent is None:
        _agent = LogAnalyzerAgent()
    return _agent
