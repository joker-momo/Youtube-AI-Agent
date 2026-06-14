"""Retry helpers extracted from short_builder."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from video_agent.shorts import llm_history

MAX_PROVIDER_RETRIES_PER_CALL = 3


def record_retry_event(
    recorder: llm_history.LLMHistoryRecorder,
    reason: str,
    scope: str,
    attempt: int,
    max_attempts: int,
    hard_fail: bool,
    source_stage: str,
    details: dict | None = None,
) -> None:
    payload = {
        "retry_reason": reason,
        "retry_scope": scope,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "hard_fail": hard_fail,
        "source_stage": source_stage,
    }
    if details:
        payload.update(details)
    recorder.record_event(
        "deterministic",
        "retry_classification",
        payload,
        ok=True,
    )


def wrap_llm_with_provider_retries(
    original_llm_fn: Callable[..., str], recorder: llm_history.LLMHistoryRecorder, provider: str
) -> Callable[..., str]:
    def wrapped(*args: Any, **kwargs: Any) -> str:
        from video_agent.shorts.llm_history import _guess_kind
        from video_agent.shorts.short_scene_builder import (
            ChatGPTProviderError,
            is_provider_error_text,
        )

        prompt = ""
        if len(args) == 2:
            prompt = str(args[1])
        elif len(args) == 1:
            prompt = str(args[0])

        stage_name = _guess_kind(provider, prompt)

        last_error = None
        for attempt in range(1, MAX_PROVIDER_RETRIES_PER_CALL + 1):
            try:
                res = original_llm_fn(*args, **kwargs)
                if is_provider_error_text(res):
                    raise ChatGPTProviderError(
                        "Provider error text returned by LLM", snippet=res[:200]
                    )
                return res
            except Exception as exc:
                last_error = exc
                record_retry_event(
                    recorder,
                    reason="provider_error",
                    scope="provider_only",
                    attempt=attempt,
                    max_attempts=MAX_PROVIDER_RETRIES_PER_CALL,
                    hard_fail=True,
                    source_stage=stage_name,
                    details={"error": str(exc)},
                )
                if attempt == MAX_PROVIDER_RETRIES_PER_CALL:
                    raise exc
        raise last_error or RuntimeError("Provider retries exhausted")

    return wrapped
