"""Shorts AI role wiring (spec v6 §2 + §3).

Every Shorts LLM call uses a temporary conversation: a fresh ChatGPT/Gemini
chat per task, no shared history. This module centralizes provider routing,
the temp-chat contract, and structured logging metadata.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any, Callable


# Spec v6 §3.1 — every call uses a fresh temp conversation.
TEMPORARY_CONVERSATIONS = True


def uses_temporary_conversations() -> bool:
    return TEMPORARY_CONVERSATIONS


# Sender signature: a coroutine ``async (prompt: str) -> str``.
SyncSender = Callable[[str], str]


@dataclass
class LLMCallLog:
    """Structured log row for an LLM call (spec v6 §3.4)."""

    task: str
    provider: str
    short_id: str
    attempt: int
    input_artifacts: list[str]
    output_artifact: str
    temporary_conversation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "provider": self.provider,
            "temporary_conversation": self.temporary_conversation,
            "short_id": self.short_id,
            "attempt": self.attempt,
            "input_artifacts": list(self.input_artifacts),
            "output_artifact": self.output_artifact,
        }


def log_llm_call(entry: LLMCallLog) -> None:
    """Emit a one-line JSON record. No keys, no prompt body."""
    try:
        print("[shorts.llm]", json.dumps(entry.to_dict(), ensure_ascii=False), file=sys.stderr)
    except Exception:
        pass


# --- async ↔ sync bridge so the builder layer stays synchronous --------------

def make_sync_sender(async_send: Callable[[str], "asyncio.Future[str]"]) -> SyncSender:
    """Wrap an ``async (prompt)->str`` so callers can use a plain ``fn(prompt)``.

    Every invocation creates a fresh event loop (or reuses the running one via
    ``asyncio.run_coroutine_threadsafe`` when called from a thread). This is
    what ``BrowserClient.chatgpt_send`` / ``gemini_send`` expect."""

    def _send(prompt: str) -> str:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(async_send(prompt))
        fut = asyncio.run_coroutine_threadsafe(async_send(prompt), loop)
        return fut.result()

    return _send
