"""PR C span-aware metadata acquisition contracts (spec v4.0.3).

This module is render-neutral: it builds acquisition contexts and metadata-only
query plans. PR D owns local analysis, final frame timing, trim windows, and
render-eligible asset decisions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from video_agent.shorts.visual_vocabulary import label_for_token, normalize_visual_tokens

CONTRACT_REVISION = "4.0.3"


@dataclass(frozen=True)
class SpanSearchBudget:
    max_queries: int
    metadata_candidates_per_query: int
    max_unique_metadata_candidates: int
    max_download_candidates: int
    max_provider_retries: int


_BUDGET_DEFAULTS: dict[str, SpanSearchBudget] = {
    "critical": SpanSearchBudget(4, 10, 24, 5, 2),
    "high": SpanSearchBudget(3, 8, 18, 4, 2),
    "normal": SpanSearchBudget(2, 8, 12, 3, 1),
    "low": SpanSearchBudget(1, 6, 6, 2, 1),
}


def stable_hash(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def duration_bucket(duration_sec: float) -> str:
    if duration_sec < 3.0:
        return "0_to_3_sec"
    if duration_sec < 5.0:
        return "3_to_5_sec"
    if duration_sec < 8.0:
        return "5_to_8_sec"
    if duration_sec < 12.0:
        return "8_to_12_sec"
    return "12_plus_sec"


def resolve_visual_quality_flow_config(channel_config: dict[str, Any]) -> dict[str, Any]:
    raw = ((channel_config or {}).get("shorts") or {}).get("visual_quality_flow") or {}
    mode = str(raw.get("mode") or "disabled").strip().lower()
    if mode not in {"disabled", "report_only", "enforced"}:
        mode = "report_only"
    acquisition = dict(raw.get("acquisition") or {})
    scoring = dict(raw.get("scoring") or {})
    local_qa = dict(raw.get("local_qa") or {})
    trim_selector = dict(raw.get("trim_selector") or {})
    return {
        "enabled": bool(raw.get("enabled", False)),
        "mode": mode,
        "acquisition": {
            "span_aware": bool(acquisition.get("span_aware", True)),
            "provider": acquisition.get("provider", "pexels_video"),
            "allow_text_query_hints": bool(acquisition.get("allow_text_query_hints", True)),
            "trim_margin_sec": float(acquisition.get("trim_margin_sec", 1.0)),
            "minimum_width": int(acquisition.get("minimum_width", 720)),
            "minimum_height": int(acquisition.get("minimum_height", 720)),
            "download_policy": str(acquisition.get("download_policy") or "none"),
            "max_queries_by_importance": dict(acquisition.get("max_queries_by_importance") or {}),
        },
        "scoring": scoring,
        "trim_selector": {
            "stride_sec": float(trim_selector.get("stride_sec", 0.5)),
            "max_windows": int(trim_selector.get("max_windows", 24)),
            "reject_black_ratio": float(trim_selector.get("reject_black_ratio", 0.05)),
            "reject_unstable_motion": bool(trim_selector.get("reject_unstable_motion", True)),
            "dynamic_crop": bool(trim_selector.get("dynamic_crop", False)),
        },
        "local_qa": {
            "enabled": bool(local_qa.get("enabled", False)),
            "max_runner_ups": int(local_qa.get("max_runner_ups", 2)),
            "semantic_adapter": str(local_qa.get("semantic_adapter") or "none"),
            "detector_adapter": str(local_qa.get("detector_adapter") or "none"),
            "critical_fail_closed": bool(local_qa.get("critical_fail_closed", True)),
            "report_only_never_blocks_render": bool(
                local_qa.get("report_only_never_blocks_render", True)
            ),
        },
        "controlled_vocabulary": dict(raw.get("controlled_vocabulary") or {}),
    }


def budget_for_importance(
    importance: str, config: dict[str, Any] | None = None
) -> SpanSearchBudget:
    importance = importance if importance in _BUDGET_DEFAULTS else "normal"
    budget = _BUDGET_DEFAULTS[importance]
    max_queries_by_importance = ((config or {}).get("acquisition") or {}).get(
        "max_queries_by_importance"
    ) or {}
    if importance in max_queries_by_importance:
        try:
            budget = SpanSearchBudget(
                max(1, int(max_queries_by_importance[importance])),
                budget.metadata_candidates_per_query,
                budget.max_unique_metadata_candidates,
                budget.max_download_candidates,
                budget.max_provider_retries,
            )
        except (TypeError, ValueError):
            pass
    if ((config or {}).get("acquisition") or {}).get("download_policy", "none") == "none":
        budget = SpanSearchBudget(
            budget.max_queries,
            budget.metadata_candidates_per_query,
            budget.max_unique_metadata_candidates,
            0,
            budget.max_provider_retries,
        )
    return budget


def _scene_id(scene: dict[str, Any], index: int) -> str:
    return str(scene.get("id") or scene.get("scene_id") or f"s{index + 1:02d}")


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _collect_scene_values(scenes: list[dict[str, Any]], field: str) -> list[Any]:
    values: list[Any] = []
    for scene in scenes:
        raw = scene.get(field)
        if isinstance(raw, list):
            values.extend(raw)
        elif raw:
            values.append(raw)
    return values


def _importance(scenes: list[dict[str, Any]]) -> str:
    order = {"critical": 4, "high": 3, "normal": 2, "low": 1}
    best = "normal"
    for scene in scenes:
        raw = str(scene.get("visual_importance") or "normal").strip().lower()
        if order.get(raw, 0) > order.get(best, 0):
            best = raw
    return best if best in order else "normal"


def _query_plan(
    span_record: dict[str, Any], scenes: list[dict[str, Any]], config: dict[str, Any]
) -> dict[str, Any]:
    primary_terms = []
    for field in (
        "required_subject_tags",
        "required_action_tags",
        "required_environment_tags",
        "required_evidence_tags",
    ):
        primary_terms.extend(label_for_token(token) for token in span_record.get(field) or [])
    primary = " ".join(_unique(primary_terms)) or span_record["visual_intent"]

    hints: list[str] = []
    if config["acquisition"].get("allow_text_query_hints", True):
        for raw in _collect_scene_values(scenes, "visual_search_queries"):
            if isinstance(raw, list):
                hints.extend(str(v).strip() for v in raw if str(v).strip())
            elif raw:
                hints.append(str(raw).strip())

    alternates = _unique(hints)[:2]
    equivalent = []
    if span_record.get("required_action_tags"):
        equivalent = [f"{label_for_token(span_record['required_action_tags'][0])} mobility"]
    return {
        "primary": primary.strip(),
        "alternates": alternates,
        "equivalent_action": equivalent[:1],
    }


def build_visual_acquisition_context(
    *,
    visual_span: dict[str, Any],
    member_scenes: list[dict[str, Any]],
    channel_config: dict[str, Any],
) -> dict[str, Any]:
    """Build one-span acquisition context from validated span + member scenes."""
    config = resolve_visual_quality_flow_config(channel_config)
    vocab_cfg = config.get("controlled_vocabulary") or {}
    scene_ids = [_scene_id(scene, idx) for idx, scene in enumerate(member_scenes)]
    planned_duration = round(
        sum(float(scene.get("duration_sec") or 0.0) for scene in member_scenes),
        2,
    )
    warnings: list[str] = []

    def norm(field: str, category: str) -> list[str]:
        result = normalize_visual_tokens(
            _collect_scene_values(member_scenes, field), category=category, config=vocab_cfg
        )
        warnings.extend(result["warnings"])
        return result["tokens"]

    required_subject = norm("required_subject_tags", "subjects")
    required_action = norm("required_action_tags", "actions")
    required_environment = norm("required_environment_tags", "environments")
    required_evidence = norm("required_evidence_tags", "evidence")
    forbidden_subject = norm("forbidden_subject_tags", "subjects")
    forbidden_action = norm("forbidden_action_tags", "actions")
    forbidden_evidence = norm("forbidden_evidence_tags", "evidence")

    visual_intent = str(
        visual_span.get("visual_intent")
        or visual_span.get("visual_span_intent")
        or next(
            (
                scene.get("visual_span_intent")
                for scene in member_scenes
                if scene.get("visual_span_intent")
            ),
            "",
        )
        or next(
            (scene.get("visual_prompt") for scene in member_scenes if scene.get("visual_prompt")),
            "",
        )
        or "coherent visual span"
    ).strip()
    span_record = {
        "visual_span_id": str(visual_span.get("id") or visual_span.get("visual_span_id") or "vs01"),
        "scene_ids": list(visual_span.get("scene_ids") or scene_ids),
        "planned_duration_sec": planned_duration,
        "duration_bucket": duration_bucket(planned_duration),
        "duration_source": "scene_plan",
        "trim_margin_sec": config["acquisition"]["trim_margin_sec"],
        "visual_intent": visual_intent,
        "required_subject_tags": required_subject,
        "required_action_tags": required_action,
        "required_environment_tags": required_environment,
        "required_evidence_tags": required_evidence,
        "forbidden_subject_tags": forbidden_subject,
        "forbidden_action_tags": forbidden_action,
        "forbidden_evidence_tags": forbidden_evidence,
        "first_frame_intent": str(
            next(
                (s.get("first_frame_intent") for s in member_scenes if s.get("first_frame_intent")),
                "",
            )
        ).strip(),
        "crop_target": str(
            next((s.get("crop_target") for s in member_scenes if s.get("crop_target")), "")
        ).strip(),
        "visual_importance": _importance(member_scenes),
        "warnings": warnings,
    }
    span_record["query_plan"] = _query_plan(span_record, member_scenes, config)
    payload = {
        "schema_version": 1,
        "contract_revision": CONTRACT_REVISION,
        "short_id": str(channel_config.get("short_id") or visual_span.get("short_id") or ""),
        "input_hash": stable_hash(
            {"visual_span": visual_span, "member_scenes": member_scenes, "config": config}
        ),
        "created_by_stage": "visual_acquisition_context",
        "spans": [span_record],
    }
    return payload


def compile_span_search_queries(
    context: dict[str, Any],
    *,
    locale: str,
    provider: str,
) -> dict[str, list[str]]:
    """Return bounded provider queries from a single span context."""
    del locale, provider  # provider-specific negative syntax is intentionally unused in PR C.
    plan = context.get("query_plan") or {}
    importance = str(context.get("visual_importance") or "normal")
    limit = _BUDGET_DEFAULTS.get(importance, _BUDGET_DEFAULTS["normal"]).max_queries
    ordered = [
        ("primary", plan.get("primary")),
        ("alternates", plan.get("alternates") or []),
        ("equivalent_action", plan.get("equivalent_action") or []),
    ]
    out = {"primary": [], "alternates": [], "equivalent_action": []}
    count = 0
    seen: set[str] = set()
    for key, values in ordered:
        vals = values if isinstance(values, list) else [values]
        for value in vals:
            text = str(value or "").strip()
            if not text or text in seen or count >= limit:
                continue
            out[key].append(text)
            seen.add(text)
            count += 1
    return out
