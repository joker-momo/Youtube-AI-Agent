"""PR C report-only visual metadata acquisition stage (spec v4.0.3)."""

from __future__ import annotations

from typing import Any

from video_agent.assets.service import StockAssetService
from video_agent.shorts import paths
from video_agent.shorts.builder.context import BuildContext
from video_agent.shorts.builder.types import _PROCEED, StageResult
from video_agent.shorts.manifest import write_short_status
from video_agent.shorts.visual_acquisition import (
    CONTRACT_REVISION,
    budget_for_importance,
    build_visual_acquisition_context,
    resolve_visual_quality_flow_config,
    stable_hash,
)
from video_agent.storage.atomic import atomic_write_json


def _scene_id(scene: dict[str, Any], index: int) -> str:
    return str(scene.get("id") or scene.get("scene_id") or f"s{index + 1:02d}")


def _member_scenes(short_scenes: dict[str, Any], scene_ids: list[str]) -> list[dict[str, Any]]:
    by_id = {
        _scene_id(scene, idx): scene for idx, scene in enumerate(short_scenes.get("scenes") or [])
    }
    return [by_id[sid] for sid in scene_ids if sid in by_id]


def _artifact_header(short_id: str, *, input_hash: str, created_by_stage: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "contract_revision": CONTRACT_REVISION,
        "short_id": short_id,
        "input_hash": input_hash,
        "created_by_stage": created_by_stage,
    }


def _span_verdict(selection: dict[str, Any]) -> str:
    if selection.get("metadata_selection_status") in {"metadata_rejected", "metadata_insufficient"}:
        return "FAIL"
    records = selection.get("evidence_records") or []
    if any(record.get("status") in {"UNKNOWN", "CAPABILITY_UNAVAILABLE"} for record in records):
        return "CAPABILITY_REDUCED"
    return "PASS"


def _qa_verdict(span_verdicts: list[str]) -> str:
    if any(v == "FAIL" for v in span_verdicts):
        return "FAIL"
    if any(v == "CAPABILITY_REDUCED" for v in span_verdicts):
        return "CAPABILITY_REDUCED"
    if any(v == "WARN" for v in span_verdicts):
        return "WARN"
    return "PASS"


def _metadata_qa(
    *,
    short_id: str,
    mode: str,
    input_hash: str,
    acquisition_spans: list[dict[str, Any]],
    candidates_by_span: dict[str, list[dict[str, Any]]],
    selections: list[dict[str, Any]],
) -> dict[str, Any]:
    selection_by_span = {s["visual_span_id"]: s for s in selections}
    span_records: list[dict[str, Any]] = []
    for span in acquisition_spans:
        sid = span["visual_span_id"]
        candidates = candidates_by_span.get(sid, [])
        selection = selection_by_span.get(sid, {})
        rejection_reasons = selection.get("rejection_reasons") or {}
        verdict = _span_verdict(selection)
        evidence_records = selection.get("evidence_records") or []
        span_records.append(
            {
                "visual_span_id": sid,
                "scene_ids": span.get("scene_ids") or [],
                "planned_duration_sec": span.get("planned_duration_sec"),
                "duration_bucket": span.get("duration_bucket"),
                "verdict": verdict,
                "render_eligible": False,
                "requires_local_validation": bool(selection.get("requires_local_validation", True)),
                "metadata_gate": {
                    "verdict": "PASS"
                    if candidates and not rejection_reasons
                    else ("FAIL" if rejection_reasons else "WARN"),
                    "candidate_count": len(candidates),
                    "eligible_candidate_count": sum(
                        1 for c in candidates if (c.get("metadata_gate") or {}).get("eligible")
                    ),
                    "rejected_candidate_count": sum(
                        1 for c in candidates if not (c.get("metadata_gate") or {}).get("eligible")
                    ),
                    "rejection_reasons": rejection_reasons,
                },
                "selection_policy_qa": {
                    "provisional_candidate_id": selection.get("provisional_candidate_id"),
                    "metadata_selection_status": selection.get("metadata_selection_status"),
                    "metadata_pre_score": selection.get("metadata_pre_score"),
                    "runner_up_ids": selection.get("runner_up_ids") or [],
                    "budget_compliant": True,
                    "download_policy": selection.get("download_policy", "none"),
                    "downloaded_candidate_count": int(
                        selection.get("downloaded_candidate_count") or 0
                    ),
                },
                "evidence_capability": {
                    "provider_metadata_requirements": evidence_records,
                    "unknown_requirement_count": sum(
                        1 for r in evidence_records if r.get("status") == "UNKNOWN"
                    ),
                    "confirmed_forbidden_count": rejection_reasons.get(
                        "metadata_confirmed_forbidden_evidence", 0
                    ),
                    "capability_unavailable_count": sum(
                        1 for r in evidence_records if r.get("status") == "CAPABILITY_UNAVAILABLE"
                    ),
                },
                "errors": [],
                "warnings": [
                    "Pixel-level subject, action, crop, motion, and forbidden-evidence checks are deferred to PR D."
                ],
            }
        )
    span_verdicts = [s["verdict"] for s in span_records]
    verdict = _qa_verdict(span_verdicts)
    return {
        **_artifact_header(short_id, input_hash=input_hash, created_by_stage="visual_metadata_qa"),
        "mode": mode,
        "summary": {
            "span_count": len(span_records),
            "metadata_pass_count": sum(1 for s in span_records if s["verdict"] == "PASS"),
            "metadata_warn_count": sum(
                1 for s in span_records if s["verdict"] in {"WARN", "CAPABILITY_REDUCED"}
            ),
            "metadata_fail_count": sum(1 for s in span_records if s["verdict"] == "FAIL"),
            "capability_reduced_count": sum(
                1 for s in span_records if s["verdict"] == "CAPABILITY_REDUCED"
            ),
            "provisional_selection_count": sum(
                1 for s in span_records if s["selection_policy_qa"]["provisional_candidate_id"]
            ),
            "no_candidate_count": sum(
                1 for s in span_records if not s["selection_policy_qa"]["provisional_candidate_id"]
            ),
            "download_count": sum(
                s["selection_policy_qa"]["downloaded_candidate_count"] for s in span_records
            ),
        },
        "spans": span_records,
        "qa": {"verdict": verdict, "errors": [], "warnings": []},
    }


def _stage_visual_acquisition(ctx: BuildContext) -> StageResult:
    """Write PR C metadata-acquisition artifacts without changing render behavior."""
    short_id = ctx.short_plan["short_id"]
    cfg = resolve_visual_quality_flow_config(ctx.channel_config)
    mode = cfg["mode"]
    if not cfg.get("enabled") or mode == "disabled":
        ctx.update_stage("visual_acquisition", "skipped", mode=mode)
        return _PROCEED

    ctx.update_stage("visual_acquisition", "in_progress")
    try:
        ctx.check_stop()
        short_scenes = ctx.extras.get("short_scenes") or {}
        visual_spans = ctx.extras.get("visual_spans") or {}
        channel_config = {**ctx.channel_config, "short_id": short_id}
        visual_config = dict(ctx.channel_config.get("visuals") or {})
        visual_config["visual_quality_flow"] = cfg
        provider = cfg["acquisition"].get("provider")
        if provider:
            visual_config["providers"] = [provider]

        service = StockAssetService(visual_config)
        acquisition_spans: list[dict[str, Any]] = []
        candidates_by_span: dict[str, list[dict[str, Any]]] = {}
        selections: list[dict[str, Any]] = []

        for span in visual_spans.get("spans") or []:
            scene_ids = [str(sid) for sid in (span.get("scene_ids") or [])]
            members = _member_scenes(short_scenes, scene_ids)
            if not members:
                continue
            one_context = build_visual_acquisition_context(
                visual_span=span,
                member_scenes=members,
                channel_config=channel_config,
            )
            acquisition = one_context["spans"][0]
            acquisition_spans.append(acquisition)
            budget = budget_for_importance(
                str(acquisition.get("visual_importance") or "normal"), cfg
            )
            candidates = service.get_visual_span_candidates(
                acquisition_context=acquisition, budget=budget
            )
            candidates_by_span[acquisition["visual_span_id"]] = candidates
            selections.append(
                service.select_provisional_span_candidate(
                    acquisition_context=acquisition,
                    candidates=candidates,
                    recent_visual_memory={},
                    config={"download_policy": cfg["acquisition"].get("download_policy", "none")},
                )
            )

        input_hash = stable_hash(
            {"short_scenes": short_scenes, "visual_spans": visual_spans, "visual_quality_flow": cfg}
        )
        acquisition_doc = {
            **_artifact_header(
                short_id, input_hash=input_hash, created_by_stage="visual_acquisition_context"
            ),
            "spans": acquisition_spans,
        }
        candidates_doc = {
            **_artifact_header(
                short_id, input_hash=input_hash, created_by_stage="visual_span_candidates"
            ),
            "spans": [
                {"visual_span_id": sid, "candidates": candidates}
                for sid, candidates in candidates_by_span.items()
            ],
        }
        selection_doc = {
            **_artifact_header(
                short_id, input_hash=input_hash, created_by_stage="visual_span_asset_selection"
            ),
            "selection_kind": "provisional_metadata",
            "spans": selections,
        }
        metadata_qa = _metadata_qa(
            short_id=short_id,
            mode=mode,
            input_hash=input_hash,
            acquisition_spans=acquisition_spans,
            candidates_by_span=candidates_by_span,
            selections=selections,
        )

        atomic_write_json(
            ctx.json_dir / paths.SHORT_VISUAL_ACQUISITION_CONTEXT_FILE, acquisition_doc
        )
        atomic_write_json(ctx.json_dir / paths.SHORT_VISUAL_SPAN_CANDIDATES_FILE, candidates_doc)
        atomic_write_json(ctx.json_dir / paths.SHORT_VISUAL_SPAN_SELECTION_FILE, selection_doc)
        atomic_write_json(ctx.json_dir / paths.SHORT_VISUAL_SPAN_METADATA_QA_FILE, metadata_qa)

        ctx.extras["visual_acquisition_context"] = acquisition_doc
        ctx.extras["visual_span_candidates"] = candidates_doc
        ctx.extras["visual_span_asset_selection"] = selection_doc
        ctx.extras["visual_metadata_qa"] = metadata_qa
        ctx.update_stage(
            "visual_acquisition",
            "completed",
            verdict=metadata_qa["qa"]["verdict"],
            mode=mode,
            span_count=len(acquisition_spans),
            provisional_selection_count=metadata_qa["summary"]["provisional_selection_count"],
        )
        return _PROCEED
    except Exception as exc:  # noqa: BLE001 - report_only must preserve legacy render.
        if mode == "enforced":
            ctx.update_stage("visual_acquisition", "failed", error=str(exc))
            ctx.status["status"] = "failed"
            write_short_status(ctx.long_job_dir, short_id, ctx.status)
            raise
        ctx.update_stage("visual_acquisition", "skipped", error=str(exc), mode=mode)
        return _PROCEED
