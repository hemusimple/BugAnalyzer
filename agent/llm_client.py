"""
llm_client.py — Ollama/Mistral client with full token tracking.
"""

from __future__ import annotations
import asyncio, json, time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional
import httpx
from loguru import logger
from config import settings


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    requests: int = 0
    total_duration_ms: float = 0.0

    def add(self, prompt: int, completion: int, duration_ms: float = 0):
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += prompt + completion
        self.requests += 1
        self.total_duration_ms += duration_ms

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "requests": self.requests,
            "total_duration_ms": round(self.total_duration_ms, 1),
            "avg_latency_ms": round(self.total_duration_ms / max(self.requests, 1), 1),
        }


# Global session token counter
_session_usage = TokenUsage()


def get_session_usage() -> TokenUsage:
    return _session_usage


def reset_session_usage():
    global _session_usage
    _session_usage = TokenUsage()


class OllamaClient:
    def __init__(self):
        self.base_url = settings.OLLAMA_BASE_URL
        self.model = settings.OLLAMA_MODEL
        self.temperature = settings.OLLAMA_TEMPERATURE
        self.context_window = settings.OLLAMA_CONTEXT_WINDOW

    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        stream: bool = False,
    ) -> str | AsyncIterator[str]:
        """
        Send a chat completion. Returns full string or async generator for streaming.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.context_window,
                "num_predict": 2048,  # always reserve space for generation
            },
        }
        if system:
            payload["messages"] = [{"role": "system", "content": system}] + messages

        if stream:
            return self._stream_chat(payload)
        else:
            return await self._blocking_chat(payload)

    async def _blocking_chat(self, payload: dict) -> str:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        elapsed = (time.time() - t0) * 1000
        # Ollama returns eval_count (completion) and prompt_eval_count (prompt)
        prompt_tok = data.get("prompt_eval_count", 0)
        comp_tok = data.get("eval_count", 0)
        _session_usage.add(prompt_tok, comp_tok, elapsed)
        logger.debug(f"LLM tokens: prompt={prompt_tok} completion={comp_tok} latency={elapsed:.0f}ms")

        return data["message"]["content"]

    async def _stream_chat(self, payload: dict) -> AsyncIterator[str]:
        t0 = time.time()
        prompt_tok = 0
        comp_tok = 0
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("message", {}).get("content"):
                        yield chunk["message"]["content"]
                    if chunk.get("done"):
                        prompt_tok = chunk.get("prompt_eval_count", 0)
                        comp_tok = chunk.get("eval_count", 0)
                        break

        elapsed = (time.time() - t0) * 1000
        _session_usage.add(prompt_tok, comp_tok, elapsed)
        logger.debug(f"LLM stream done: prompt={prompt_tok} completion={comp_tok} latency={elapsed:.0f}ms")

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                data = r.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []


# Singleton
_client: OllamaClient | None = None


def get_llm_client() -> OllamaClient:
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client
