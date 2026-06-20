"""Metadata-only candidate scoring for visual-quality PR C."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ")


def _tokens(text: str) -> set[str]:
    import re

    return {token for token in re.findall(r"[a-zA-Z0-9áéíóúñ]+", text.lower()) if len(token) > 2}


def _haystack(candidate: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("tags", "title", "description", "alt", "query"):
        raw = candidate.get(key)
        if isinstance(raw, list):
            parts.extend(str(v) for v in raw)
        elif raw:
            parts.append(str(raw))
    metadata = candidate.get("provider_metadata") or {}
    if isinstance(metadata, dict):
        for raw in metadata.values():
            if isinstance(raw, list):
                parts.extend(str(v) for v in raw)
            elif raw:
                parts.append(str(raw))
    return " ".join(parts).lower()


def metadata_identity(candidate: dict[str, Any]) -> tuple[str, str]:
    provider = str(candidate.get("provider") or "")
    asset_id = str(candidate.get("provider_asset_id") or "").strip()
    if asset_id:
        return provider, asset_id
    identity_payload = {
        "source_url": candidate.get("source_url"),
        "download_url": candidate.get("download_url"),
        "preview_url": candidate.get("preview_url"),
        "duration_sec": candidate.get("duration_sec"),
        "width": candidate.get("width"),
        "height": candidate.get("height"),
        "title": candidate.get("title"),
        "photographer": candidate.get("photographer"),
        "tags": sorted(str(tag) for tag in candidate.get("tags") or []),
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, ensure_ascii=False, default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    return provider, fingerprint


def metadata_identity_basis(candidate: dict[str, Any]) -> str:
    return (
        "provider_asset_id"
        if str(candidate.get("provider_asset_id") or "").strip()
        else "provider_metadata_identity"
    )


def candidate_id(candidate: dict[str, Any]) -> str:
    provider, ident = metadata_identity(candidate)
    return f"{provider}-{ident}"


def source_media_kind(candidate: dict[str, Any]) -> str:
    media_type = str(candidate.get("media_type") or "").lower()
    provider = str(candidate.get("provider") or "").lower()
    if media_type == "video" or provider.endswith("_video"):
        return "native_video"
    if media_type == "photo":
        return "native_image"
    return "generated_placeholder"


def render_media_kind(candidate: dict[str, Any]) -> str:
    return "video" if source_media_kind(candidate) == "native_video" else "image"


def metadata_gate(
    candidate: dict[str, Any], context: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    reasons: list[str] = []
    rejection_counts: dict[str, int] = {}

    def reject(reason: str) -> None:
        reasons.append(reason)
        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    if source_media_kind(candidate) != "native_video":
        reject("source_kind_mismatch")

    width = int(candidate.get("width") or 0)
    height = int(candidate.get("height") or 0)
    min_width = int(config.get("minimum_width") or 720)
    min_height = int(config.get("minimum_height") or 720)
    if width and width < min_width or height and height < min_height:
        reject("resolution_insufficient")

    duration = candidate.get("duration_sec")
    if duration is not None:
        try:
            required = float(context.get("planned_duration_sec") or 0.0) + float(
                context.get("trim_margin_sec") or 0.0
            )
            if float(duration) < required:
                reject("duration_insufficient")
        except (TypeError, ValueError):
            reject("duration_unknown")

    haystack = _haystack(candidate)
    for field in ("forbidden_subject_tags", "forbidden_action_tags", "forbidden_evidence_tags"):
        for forbidden in context.get(field) or []:
            if _norm(forbidden) and _norm(forbidden) in haystack:
                reject("metadata_confirmed_forbidden_evidence")

    return {
        "eligible": not reasons,
        "reasons": reasons,
        "rejection_counts": rejection_counts,
    }


def artifact_candidate_record(
    candidate: dict[str, Any],
    *,
    context: dict[str, Any],
    query: str,
    query_class: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    gate = metadata_gate(candidate, context, config)
    cid = candidate_id(candidate)
    return {
        "candidate_id": cid,
        "dedup_basis": metadata_identity_basis(candidate),
        "provider": candidate.get("provider"),
        "provider_asset_id": candidate.get("provider_asset_id"),
        "query": query,
        "query_class": query_class,
        "query_origins": [{"query": query, "query_class": query_class}],
        "preview_url": candidate.get("preview_url"),
        "download_url_ref": candidate.get("download_url") or candidate.get("source_url") or cid,
        "local_path": None,
        "public_ref": None,
        "source_media_kind": source_media_kind(candidate),
        "render_media_kind": render_media_kind(candidate),
        "width": candidate.get("width"),
        "height": candidate.get("height"),
        "duration_sec": candidate.get("duration_sec"),
        "fps": candidate.get("fps"),
        "provider_metadata": {
            "photographer": candidate.get("photographer"),
            "tags": list(candidate.get("tags") or []),
            "quality": candidate.get("quality"),
        },
        "metadata_gate": gate,
        "local_analysis": None,
        "score": None,
        "rank": None,
    }


def merge_query_origin(record: dict[str, Any], *, query: str, query_class: str) -> None:
    origins = record.setdefault("query_origins", [])
    origin = {"query": query, "query_class": query_class}
    if origin not in origins:
        origins.append(origin)


def evidence_records(
    context: dict[str, Any], candidate: dict[str, Any] | None
) -> list[dict[str, Any]]:
    cid = candidate.get("candidate_id") if candidate else None
    records: list[dict[str, Any]] = []
    for prefix, field in (
        ("required_subject", "required_subject_tags"),
        ("required_action", "required_action_tags"),
        ("required_environment", "required_environment_tags"),
        ("required_evidence", "required_evidence_tags"),
        ("forbidden_evidence", "forbidden_evidence_tags"),
    ):
        for token in context.get(field) or []:
            records.append(
                {
                    "requirement": f"{prefix}:{token}",
                    "status": "UNKNOWN",
                    "capability_source": "provider_metadata",
                    "source_field": "provider.tags",
                    "asset_id": cid,
                    "confidence": None,
                    "reason": "provider metadata cannot confirm visual presence or absence",
                }
            )
    return records


def metadata_pre_score(candidate: dict[str, Any], context: dict[str, Any]) -> float:
    hay = _tokens(_haystack(candidate))
    wanted: list[str] = []
    for field in (
        "required_subject_tags",
        "required_action_tags",
        "required_environment_tags",
        "required_evidence_tags",
    ):
        wanted.extend(str(v).replace("_", " ") for v in context.get(field) or [])
    wanted_tokens = set().union(*(_tokens(v) for v in wanted)) if wanted else set()
    overlap = len(hay & wanted_tokens)
    score = min(45, overlap * 10)
    if candidate.get("source_media_kind") == "native_video":
        score += 15
    if candidate.get("duration_sec") is not None:
        try:
            margin = float(candidate["duration_sec"]) - float(
                context.get("planned_duration_sec") or 0.0
            )
            if margin >= float(context.get("trim_margin_sec") or 0.0):
                score += 15
        except (TypeError, ValueError):
            pass
    width = int(candidate.get("width") or 0)
    height = int(candidate.get("height") or 0)
    if width >= 1280 and height >= 720:
        score += 15
    if candidate.get("provider_metadata", {}).get("quality") in {"hd", "fullhd", "original"}:
        score += 10
    return float(min(100, score))


def select_provisional_span_candidate(
    *,
    acquisition_context: dict[str, Any],
    candidates: list[dict[str, Any]],
    recent_visual_memory: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    del recent_visual_memory  # PR C only applies modest scoring; memory wiring can extend this.
    eligible = [c for c in candidates if (c.get("metadata_gate") or {}).get("eligible")]
    for candidate in candidates:
        candidate["metadata_pre_score"] = metadata_pre_score(candidate, acquisition_context)
    ranked = sorted(eligible, key=lambda c: float(c.get("metadata_pre_score") or 0.0), reverse=True)
    rejected_counts: dict[str, int] = {}
    for c in candidates:
        for reason, count in ((c.get("metadata_gate") or {}).get("rejection_counts") or {}).items():
            rejected_counts[reason] = rejected_counts.get(reason, 0) + int(count)

    if not ranked:
        return {
            "visual_span_id": acquisition_context["visual_span_id"],
            "provisional_candidate_id": None,
            "metadata_selection_status": "metadata_rejected"
            if rejected_counts
            else "metadata_insufficient",
            "runner_up_ids": [],
            "render_eligible": False,
            "requires_local_validation": True,
            "fallback_used": True,
            "fallback_level": 1,
            "rejection_reasons": rejected_counts,
            "evidence_records": evidence_records(acquisition_context, None),
            "download_policy": config.get("download_policy", "none"),
            "downloaded_candidate_count": 0,
        }

    best = ranked[0]
    best_score = float(best.get("metadata_pre_score") or 0.0)
    status = "metadata_promising" if best_score >= 50 else "metadata_acceptable"
    return {
        "visual_span_id": acquisition_context["visual_span_id"],
        "provisional_candidate_id": best["candidate_id"],
        "metadata_selection_status": status,
        "metadata_pre_score": best_score,
        "runner_up_ids": [c["candidate_id"] for c in ranked[1:3]],
        "render_eligible": False,
        "requires_local_validation": True,
        "fallback_used": False,
        "fallback_level": 0,
        "rejection_reasons": rejected_counts,
        "evidence_records": evidence_records(acquisition_context, best),
        "download_policy": config.get("download_policy", "none"),
        "downloaded_candidate_count": 0,
    }
