"""Shorts AI role wiring (spec v6 §2 + §3).

Every Shorts LLM call uses a temporary conversation: a fresh ChatGPT/Gemini
chat per task, no shared history. This module centralizes provider routing,
the temp-chat contract, and structured logging metadata.
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Spec v6 §3.1 — every call uses a fresh temp conversation.
TEMPORARY_CONVERSATIONS = True

# Provider-error recovery (robustness spec §4): how many times to clear cookies +
# re-send the same ChatGPT prompt before giving up with a provider error.
MAX_CHATGPT_PROVIDER_RETRIES = 2


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

def make_sync_sender(async_send: Callable[[str], asyncio.Future[str]]) -> SyncSender:
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


# --- ChatGPT provider-error recovery (robustness spec §3 + §4) ---------------

async def chatgpt_send_with_recovery(
    client: Any,
    prompt: str,
    *,
    response_timeout_ms: int = 300_000,
    max_provider_retries: int = MAX_CHATGPT_PROVIDER_RETRIES,
) -> str:
    """Send a ChatGPT prompt, recovering from provider-error responses.

    Primary recovery (per call when the response is provider-error text):
    ``client.auth_clear_cookies("chatgpt")`` then re-send — the next send
    re-navigates to a fresh temporary chat, so this is the cookie-reset + fresh
    temp-chat path. Full temporary-profile reset is only attempted as a fallback
    after the retry budget is exhausted (or if cookie clear fails), and only if
    the client exposes ``reset_browser_profile`` (otherwise it is a no-op and the
    caller surfaces a provider error).

    Returns the final response text. If it is still provider-error text, the
    caller (``build_short_scenes``) raises ``ChatGPTProviderError`` so the build
    loop treats it as a provider failure, not a creative scene-QA failure."""
    from video_agent.shorts.short_scene_builder import is_provider_error_text

    text = await _send_absorbing_503(client, prompt, response_timeout_ms)
    if not is_provider_error_text(text):
        return text

    for attempt in range(1, max_provider_retries + 1):
        _log_provider_error(attempt, text, action="clear_cookies_and_retry")
        cleared = await _safe_clear_cookies(client)
        if not cleared:
            # Cookie clear failed -> escalate straight to profile-reset fallback.
            await _safe_profile_reset(client)
        text = await _send_absorbing_503(client, prompt, response_timeout_ms)
        if not is_provider_error_text(text):
            return text

    # Retry budget exhausted: last-resort full temporary-profile reset, then one
    # final send. If it still fails, return the provider text for the caller to
    # raise ChatGPTProviderError on.
    _log_provider_error(max_provider_retries + 1, text, action="profile_reset_fallback")
    if await _safe_profile_reset(client):
        text = await _send_absorbing_503(client, prompt, response_timeout_ms)
    return text


# Backoff schedule (seconds) for a 503 from the browser-worker: the runtime is
# booting (bug-497 made CDP attach fail FAST with a structured 503), so the
# client absorbs the boot window instead of failing the whole render job in
# seconds (bug-507, run-6: worker started while Chromium was still booting).
_HTTP_503_BACKOFF_SEC = (5.0, 10.0, 20.0, 30.0, 45.0)


async def _send_absorbing_503(client: Any, prompt: str, response_timeout_ms: int) -> str:
    """chatgpt_send, retrying with backoff while the browser-worker returns 503."""
    last_exc: Exception | None = None
    for delay in (*_HTTP_503_BACKOFF_SEC, None):
        try:
            return await client.chatgpt_send(prompt, response_timeout_ms=response_timeout_ms)
        except Exception as exc:
            if getattr(exc, "status_code", None) != 503 or delay is None:
                raise
            last_exc = exc
            print(
                f"[shorts.llm] browser-worker 503 (runtime booting?); retrying in {delay:.0f}s",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(delay)
    raise last_exc  # pragma: no cover — loop always raises or returns


async def _safe_clear_cookies(client: Any) -> bool:
    fn = getattr(client, "auth_clear_cookies", None)
    if fn is None:
        return False
    try:
        await fn("chatgpt")
        return True
    except Exception:
        return False


async def _safe_profile_reset(client: Any) -> bool:
    """Best-effort full temporary-profile reset. Only runs if the client exposes
    ``reset_browser_profile`` (no such worker endpoint by default) — otherwise a
    logged no-op so we never touch a real user profile."""
    fn = getattr(client, "reset_browser_profile", None)
    if fn is None:
        return False
    try:
        await fn("chatgpt")
        return True
    except Exception:
        return False


def _log_provider_error(attempt: int, text: str, *, action: str) -> None:
    snippet = (text or "").strip().splitlines()[0][:120] if (text or "").strip() else ""
    try:
        print(
            "[shorts.llm]",
            json.dumps(
                {
                    "event": "chatgpt_provider_error",
                    "stage": "scene_generation",
                    "action": action,
                    "attempt": attempt,
                    "error_snippet": snippet,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
    except Exception:
        pass
