"""Reason-aware call/retry budget summary for a single Short build.

Derived deterministically from the LLM history records (see ``llm_history``).
Separates transient provider/browser failures from quality-loop retries so a
flaky provider does not get blamed on the generation logic.
"""
from __future__ import annotations

from typing import Any

DEFAULT_BUDGET = {
    "target_total_calls": 30,
    "warn_total_calls": 35,
    "warn_failed_calls": 3,
    "warn_non_provider_failed_calls": 2,
}

_REASONS = (
    "provider_error",
    "qa_retry",
    "schema_error",
    "scene_validation_fail",
    "audio_fit_fail",
    "renderer_contract_fail",
    "unknown",
)

# Substrings that mark a transient provider/browser/network failure.
_PROVIDER_HINTS = (
    "something went wrong",
    "timeout",
    "timed out",
    "network",
    "connection",
    "provider",
    "browser",
    "rate limit",
    "429",
    "503",
    "please try again",
)

_RETRY_STAGE_KINDS = {
    "scene_generation": ("scenes", "scene_builder"),
    "qa_script": ("qa_script",),
    "qa_scenes": ("qa_scenes", "qa"),
    "seo": ("seo",),
    "thumbnail": ("thumbnail",),
    "audio": ("audio", "audio_fit", "audio_tail_repair", "audio_sync_summary"),
}


def _classify(record: dict[str, Any]) -> str:
    """Map one failed/FAIL record to a budget reason."""
    explicit = str(record.get("reason") or "").strip()
    if explicit in _REASONS:
        return explicit

    error = str(record.get("error") or "").lower()
    kind = str(record.get("kind") or record.get("event") or "").lower()

    if error and any(h in error for h in _PROVIDER_HINTS):
        return "provider_error"
    if "audio_fit" in kind or "audio_fit" in error:
        return "audio_fit_fail"
    if "scene_validation" in kind or "scene_structure" in kind or "scene_validation" in error:
        return "scene_validation_fail"
    if "render" in kind or "renderer" in error or "remotion" in error:
        return "renderer_contract_fail"
    if "schema" in error or "json" in error or "invalid" in error:
        return "schema_error"
    if kind.startswith("qa") or "qa" in kind:
        return "qa_retry"
    return "unknown"


def build_call_budget_summary(
    history: list[dict[str, Any]],
    *,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    budget = {**DEFAULT_BUDGET, **(budget or {})}

    total_calls = len(history)
    by_provider: dict[str, int] = {}
    by_reason: dict[str, int] = {r: 0 for r in _REASONS}
    retry_counts: dict[str, int] = {k: 0 for k in _RETRY_STAGE_KINDS}

    failed = 0
    for rec in history:
        provider = str(rec.get("provider") or "unknown")
        by_provider[provider] = by_provider.get(provider, 0) + 1

        kind = str(rec.get("kind") or rec.get("event") or "").lower()
        for stage, kinds in _RETRY_STAGE_KINDS.items():
            if kind in kinds:
                retry_counts[stage] += 1
                break

        ok = rec.get("ok", True)
        verdict = str(rec.get("verdict") or (rec.get("payload") or {}).get("verdict") or "").upper()
        if ok is False or verdict == "FAIL":
            failed += 1
            by_reason[_classify(rec)] += 1

    non_provider_failed = failed - by_reason["provider_error"]

    # Spec v4 §2.3: PASS requires staying at/under the target call count; the
    # higher warn_total_calls value only marks the upper edge of the WARN band.
    verdict = "PASS"
    if (
        total_calls > budget["target_total_calls"]
        or failed > budget["warn_failed_calls"]
        or non_provider_failed > budget["warn_non_provider_failed_calls"]
    ):
        verdict = "WARN"

    return {
        "stage": "call_budget_summary",
        "status": "completed",
        "total_calls": total_calls,
        "failed_calls": failed,
        "non_provider_failed_calls": non_provider_failed,
        "by_provider": by_provider,
        "by_reason": by_reason,
        "retry_counts": retry_counts,
        "budget": budget,
        "verdict": verdict,
    }
