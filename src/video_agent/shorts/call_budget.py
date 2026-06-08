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
    "qa_soft_warn",
    "qa_hard_fail",
    "qa_retry",
    "schema_error",
    "scene_validation_fail",
    "audio_fit_fail",
    "renderer_contract_fail",
    "wrong_context_suppressed",
    "noncanonical_count_inference",
    "retention_grammar_repair",
    "retry_collapse",
    "duration_normalized",
    "deterministic_repair",
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
    "script_generation": ("script", "script_builder"),
    "scene_generation": ("scenes", "scene_builder"),
    "qa_script": ("qa_script",),
    "qa_scenes": ("qa_scenes", "qa"),
    "seo": ("seo",),
    "audio": ("audio", "audio_fit", "audio_tail_repair", "audio_sync_summary"),
    "render": ("render", "renderer"),
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
    if "weak_hook_motion" in kind or "weak_hook_motion" in error or explicit == "weak_hook_motion":
        return "deterministic_repair"
    if "duration_pacing" in kind or "duration_pacing" in error or explicit == "duration_pacing":
        return "qa_soft_warn"
    if "total_duration_normalized" in kind or "total_duration_normalized" in error or explicit == "total_duration_normalized" or explicit == "duration_normalized":
        return "duration_normalized"
    if "scene_validation" in kind or "scene_structure" in kind or "scene_validation" in error:
        return "scene_validation_fail"
    if "render" in kind or "renderer" in error or "remotion" in error:
        return "renderer_contract_fail"
    if "schema" in error or "json" in error or "invalid" in error:
        return "schema_error"
    if "wrong_context_suppressed" in kind or "wrong_context_suppressed" in error:
        return "wrong_context_suppressed"
    if "noncanonical_count_inference" in kind or "noncanonical_count_inference" in error:
        return "noncanonical_count_inference"
    if "retry_collapse" in kind or "retry_collapse" in error:
        return "retry_collapse"
    if "qa_soft_warn" in kind or "qa_soft_warn" in error:
        return "qa_soft_warn"
    if "qa_hard_fail" in kind or "qa_hard_fail" in error:
        return "qa_hard_fail"
    if kind.startswith("qa") or "qa" in kind:
        return "qa_retry"
    return "unknown"


def build_call_budget_summary(
    history: list[dict[str, Any]],
    *,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    budget = {**DEFAULT_BUDGET, **(budget or {})}

    # Preprocess history to link classification events to their preceding QA calls
    processed_history = []
    last_qa_idx = -1
    for rec in history:
        rec_copy = dict(rec)
        provider = rec_copy.get("provider")
        kind = rec_copy.get("kind")
        if (
            (provider == "gemini" and kind in ("qa_scenes", "qa_script", "qa"))
            or (provider == "deterministic" and kind == "stage_status" and (rec_copy.get("payload") or {}).get("stage") in ("qa_scenes", "qa_script"))
        ):
            last_qa_idx = len(processed_history)
        
        if provider == "deterministic" and kind == "qa_classification":
            reason = (rec_copy.get("payload") or {}).get("reason")
            if reason and last_qa_idx != -1:
                processed_history[last_qa_idx]["reason"] = reason
            continue
            
        processed_history.append(rec_copy)

    history = processed_history
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
