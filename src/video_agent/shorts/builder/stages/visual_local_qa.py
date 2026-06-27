"""PR D local visual QA and trim-window selection stage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.shorts import asset_schedule, paths
from video_agent.shorts.builder.context import BuildContext
from video_agent.shorts.builder.stages.visual_schedule import _resolve_fps
from video_agent.shorts.builder.types import _PROCEED, StageResult
from video_agent.shorts.manifest import write_short_status
from video_agent.shorts.visual_acquisition import (
    CONTRACT_REVISION,
    resolve_visual_quality_flow_config,
    stable_hash,
)
from video_agent.shorts.visual_local_analysis import (
    FinalistDownloader,
    LocalVisualAnalyzer,
    TrimWindowConfig,
    select_trim_window,
)
from video_agent.shorts.visual_semantic import build_semantic_analyzer
from video_agent.storage.atomic import atomic_write_json


def _artifact_header(short_id: str, *, input_hash: str, created_by_stage: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "contract_revision": CONTRACT_REVISION,
        "short_id": short_id,
        "input_hash": input_hash,
        "created_by_stage": created_by_stage,
    }


def _candidate_map(candidates_doc: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for span in candidates_doc.get("spans") or []:
        sid = str(span.get("visual_span_id") or "")
        out[sid] = {
            c["candidate_id"]: c for c in span.get("candidates") or [] if c.get("candidate_id")
        }
    return out


def _finalist_ids(selection: dict[str, Any], *, max_runner_ups: int) -> list[str]:
    ids: list[str] = []
    winner = selection.get("provisional_candidate_id")
    if winner:
        ids.append(str(winner))
    for rid in selection.get("runner_up_ids") or []:
        if len(ids) >= max_runner_ups + 1:
            break
        if rid and str(rid) not in ids:
            ids.append(str(rid))
    return ids


# Controlled-action layouts: the scene's single human action is the point (hook =
# emotional first frame, tip = sit/feet/breathe/write/message, cta = the ask). For
# these a CONTRADICTED required_action is hard-failed by _qa_verdict so wrong-action
# stock routes to a controlled fallback. This is scoped per-LAYOUT and does NOT mark
# the span visual_importance=critical, so the capability-reduced whole-build
# fail-close (and per-candidate reject) keep their original narrow scope (hook/cta/
# movement) — a flaky semantic model on a tip degrades, it does not nuke the build.
_CONTROLLED_ACTION_LAYOUTS = {"short_hook", "hook", "short_cta", "cta", "short_tip"}
_CONTROLLED_ACTION_FUNCTIONS = {"hook", "cta"}


def _controlled_action_scene_ids(short_scenes: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for scene in short_scenes.get("scenes") or []:
        layout = str(scene.get("layout") or "")
        rf = str(scene.get("retention_function") or "")
        if layout in _CONTROLLED_ACTION_LAYOUTS or rf in _CONTROLLED_ACTION_FUNCTIONS:
            out.add(str(scene.get("id")))
    return out


def _span_required_frames(span: dict[str, Any], short_scenes: dict[str, Any], fps: int) -> int:
    scenes = list(short_scenes.get("scenes") or [])
    boundaries = asset_schedule.build_scene_frame_timeline(scenes, fps)
    by_id = {b["scene_id"]: b for b in boundaries}
    frames = 0
    for sid in span.get("scene_ids") or []:
        boundary = by_id.get(str(sid))
        if boundary:
            frames += int(boundary["duration_in_frames"])
    return frames


def _trim_config(cfg: dict[str, Any]) -> TrimWindowConfig:
    raw = cfg.get("trim_selector") or {}
    return TrimWindowConfig(
        stride_sec=float(raw.get("stride_sec", 0.5)),
        max_windows=int(raw.get("max_windows", 24)),
        reject_black_ratio=float(raw.get("reject_black_ratio", 0.05)),
        reject_unstable_motion=bool(raw.get("reject_unstable_motion", True)),
        min_sharpness_score=float(raw.get("min_sharpness_score", 0.0)),
    )


_REQUIRED_FIELDS = (
    "required_subject_tags",
    "required_action_tags",
    "required_environment_tags",
    "required_evidence_tags",
)
_FORBIDDEN_FIELDS = (
    "forbidden_subject_tags",
    "forbidden_action_tags",
    "forbidden_evidence_tags",
)
_MIN_REQUIRED_SEMANTIC_SUPPORT_CONFIDENCE = 1.0


def _numeric_confidence(record: dict[str, Any]) -> float | None:
    value = record.get("confidence")
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _is_weak_required_support(record: dict[str, Any]) -> bool:
    if record.get("status") != "SUPPORTED":
        return False
    req = str(record.get("requirement") or "")
    if req != "topic:visual_intent" and not req.startswith("required_"):
        return False
    confidence = _numeric_confidence(record)
    return (
        confidence is not None
        and confidence < _MIN_REQUIRED_SEMANTIC_SUPPORT_CONFIDENCE
    )


def _has_weak_required_support(records: list[dict[str, Any]]) -> bool:
    return any(_is_weak_required_support(r) for r in records)


def _reject_capability_reduced_candidate(
    *,
    mode: str,
    local_qa: dict[str, Any],
    span: dict[str, Any],
    records: list[dict[str, Any]],
) -> bool:
    return (
        mode == "enforced"
        and bool(local_qa.get("critical_fail_closed", True))
        and span.get("visual_importance") == "critical"
        and _has_weak_required_support(records)
        and not any(r.get("status") == "CAPABILITY_UNAVAILABLE" for r in records)
    )


def _placeholder_records(span: dict[str, Any], candidate_id: str | None) -> list[dict[str, Any]]:
    """Baseline (no semantic adapter): every semantic requirement is unverified.
    Unknown is never PASS (§12A); absence of capability cannot confirm anything."""
    records: list[dict[str, Any]] = []
    for field in _REQUIRED_FIELDS:
        prefix = field[: -len("_tags")]
        for token in span.get(field) or []:
            records.append(
                {
                    "requirement": f"{prefix}:{token}",
                    "status": "CAPABILITY_UNAVAILABLE",
                    "capability_source": "unknown",
                    "asset_id": candidate_id,
                    "confidence": None,
                    "reason": "no local semantic/detector adapter enabled",
                }
            )
    for token in span.get("forbidden_evidence_tags") or []:
        records.append(
            {
                "requirement": f"forbidden_evidence:{token}",
                "status": "UNKNOWN",
                "capability_source": "unknown",
                "asset_id": candidate_id,
                "confidence": None,
                "reason": "absence of local semantic capability cannot confirm forbidden evidence absence",
            }
        )
    return records


def _semantic_records(
    span: dict[str, Any],
    *,
    candidate_id: str | None,
    local_qa: dict[str, Any],
    semantic_analyzer: Any = None,
    video_path: str | None = None,
    duration_sec: float = 0.0,
) -> list[dict[str, Any]]:
    """Real local-semantic evidence for the selected finalist when an adapter is
    enabled and a downloaded clip exists; otherwise the unverified baseline."""
    if semantic_analyzer is None or not video_path:
        return _placeholder_records(span, candidate_id)
    required = {f: list(span.get(f) or []) for f in _REQUIRED_FIELDS}
    forbidden = {f: list(span.get(f) or []) for f in _FORBIDDEN_FIELDS}
    try:
        records = semantic_analyzer.analyze_span(
            video_path=video_path,
            duration_sec=duration_sec,
            required_tags=required,
            forbidden_tags=forbidden,
            visual_intent=str(span.get("visual_intent") or ""),
            asset_id=candidate_id,
        )
    except Exception as exc:  # noqa: BLE001 - semantic failure degrades, never crashes the build
        return _placeholder_records(span, candidate_id) + [
            {
                "requirement": "semantic:cascade",
                "status": "CAPABILITY_UNAVAILABLE",
                "capability_source": "unknown",
                "asset_id": candidate_id,
                "confidence": None,
                "reason": f"semantic cascade error: {exc.__class__.__name__}: {exc}",
            }
        ]
    return records or _placeholder_records(span, candidate_id)


def _candidate_verdict(
    *,
    downloaded: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    trim: dict[str, Any] | None,
    error: str | None,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if error:
        reasons.append(error)
    if not downloaded:
        reasons.append("download_failed")
    if analysis and (analysis.get("decode") or {}).get("verdict") != "PASS":
        reasons.append("decode_failure")
    if trim and trim.get("status") != "selected":
        reasons.extend((trim.get("rejection_reasons") or {}).keys())
    if analysis and (analysis.get("crop_feasibility") or {}).get("full_window_feasible") is False:
        reasons.append("crop_unstable")
    return ("PASS" if not reasons else "FAIL"), reasons


def _qa_verdict(
    records: list[dict[str, Any]], candidate_verdict: str, *, critical: bool = False
) -> str:
    if candidate_verdict == "FAIL":
        return "FAIL"
    # Hard semantic rejection ONLY when the footage is genuinely wrong (§16/§17):
    #   - a grounded forbidden object is present (e.g. a dog), or
    #   - the required SUBJECT is contradicted (e.g. no mature adult in frame), or
    #   - the overall TOPIC is contradicted (SigLIP off-topic — reliably catches the
    #     dog / feet-POV clips), or
    #   - on a CRITICAL span, the required ACTION is contradicted. The action IS the
    #     point of a critical scene (feet on the floor, slow breathing, writing in a
    #     notebook); stock that topic-matches but does not show the action is wrong
    #     footage there, so route it to a controlled fallback. On non-critical spans
    #     action stays advisory (avoids over-rejecting good on-subject footage).
    # Environment / brand contradictions remain ADVISORY everywhere: a snowy-vs-sunny
    # or one-person-vs-couple detail must not reject otherwise on-topic, on-subject
    # footage. (Treating EVERY contradiction as FAIL dropped good spans to fallback;
    # treating NONE let the dog/feet through.)
    for r in records:
        status = r.get("status")
        req = str(r.get("requirement") or "")
        if status == "CONFIRMED_PRESENT" and req.startswith("forbidden"):
            return "FAIL"
        if status == "CONTRADICTED" and (
            req.startswith("required_subject") or req == "topic:visual_intent"
        ):
            return "FAIL"
        if critical and status == "CONTRADICTED" and req.startswith("required_action"):
            return "FAIL"
    # CAPABILITY_UNAVAILABLE = the model genuinely could not run (missing weights,
    # crash) → we cannot verify → degrade. UNKNOWN = the model RAN but the margin
    # was ambiguous (not contradicted): the footage is not proven wrong, so accept
    # it rather than fail-closing the whole Short on a borderline SigLIP score.
    if any(r.get("status") == "CAPABILITY_UNAVAILABLE" for r in records):
        return "CAPABILITY_REDUCED"
    if _has_weak_required_support(records):
        return "CAPABILITY_REDUCED"
    return "PASS"


def _selection_status(provisional_id: str | None, final_id: str | None) -> str:
    if not final_id:
        return "rejected"
    if final_id == provisional_id:
        return "promoted"
    return "replaced"


def _visual_route(span: dict[str, Any], selection: dict[str, Any]) -> str:
    return str(
        selection.get("visual_route")
        or span.get("visual_route")
        or "native_video_candidate"
    )


def _skip_route_status(visual_route: str) -> str:
    return f"routed_to_{visual_route}"


def _stage_visual_local_qa(ctx: BuildContext) -> StageResult:
    """Analyze PR C finalists locally, choose final trim windows, and write PR D artifacts."""
    short_id = ctx.short_plan["short_id"]
    cfg = resolve_visual_quality_flow_config(ctx.channel_config)
    mode = cfg["mode"]
    local_qa = cfg.get("local_qa") or {}
    if not cfg.get("enabled") or mode == "disabled" or not bool(local_qa.get("enabled", False)):
        ctx.update_stage("visual_local_qa", "skipped", mode=mode)
        return _PROCEED

    ctx.update_stage("visual_local_qa", "in_progress")
    try:
        ctx.check_stop()
        fps = _resolve_fps(ctx.channel_config)
        trim_cfg = _trim_config(cfg)
        analyzer = LocalVisualAnalyzer(stride_sec=trim_cfg.stride_sec)
        downloader = FinalistDownloader()
        # Optional local semantic cascade (SigLIP2 → Qwen-VL → Grounding DINO).
        # None when semantic_adapter=none (default) → baseline path unaffected.
        semantic_analyzer = build_semantic_analyzer(local_qa)
        short_scenes = ctx.extras.get("short_scenes") or {}
        acquisition = ctx.extras.get("visual_acquisition_context") or {}
        candidates_doc = ctx.extras.get("visual_span_candidates") or {}
        selection_doc = ctx.extras.get("visual_span_asset_selection") or {}
        candidates_by_span = _candidate_map(candidates_doc)
        selection_by_span = {s["visual_span_id"]: s for s in selection_doc.get("spans") or []}

        span_qas: list[dict[str, Any]] = []
        trim_spans: list[dict[str, Any]] = []
        download_count = 0
        seen_hashes: set[str] = set()
        max_runner_ups = int(local_qa.get("max_runner_ups", 2))

        spans_list = list(acquisition.get("spans") or [])
        span_total = len(spans_list)
        controlled_action_ids = _controlled_action_scene_ids(short_scenes)
        for span_index, span in enumerate(spans_list, start=1):
            span_id = span["visual_span_id"]
            # Action gate fires for critical spans OR any controlled-action layout
            # (hook/tip/cta). Layout scope does NOT widen visual_importance, so the
            # capability-reduced fail-close stays narrow (see _controlled_action_*).
            action_critical = span.get("visual_importance") == "critical" or any(
                str(sid) in controlled_action_ids for sid in (span.get("scene_ids") or [])
            )
            selection = selection_by_span.get(span_id, {})
            visual_route = _visual_route(span, selection)
            required_frames = _span_required_frames(span, short_scenes, fps)
            provisional_id = selection.get("provisional_candidate_id")
            candidate_qas: list[dict[str, Any]] = []
            final: dict[str, Any] | None = None
            final_trim: dict[str, Any] | None = None
            final_analysis: dict[str, Any] | None = None
            final_semantic_records: list[dict[str, Any]] = []

            if visual_route != "native_video_candidate":
                span_qas.append(
                    {
                        "visual_span_id": span_id,
                        "scene_ids": span.get("scene_ids") or [],
                        "visual_route": visual_route,
                        "required_duration_in_frames": required_frames,
                        "provisional_candidate_id": provisional_id,
                        "final_candidate_id": None,
                        "final_selection_status": _skip_route_status(visual_route),
                        "render_eligible": False,
                        "requires_local_validation": False,
                        "candidate_qa": [],
                        "evidence_records": [],
                        "qa": {"verdict": "PASS", "errors": [], "warnings": []},
                    }
                )
                continue

            finalist_ids = list(_finalist_ids(selection, max_runner_ups=max_runner_ups))
            candidate_total = len(finalist_ids)
            for cand_index, cid in enumerate(finalist_ids, start=1):
                candidate = candidates_by_span.get(span_id, {}).get(cid)
                if not candidate:
                    continue
                # Per-candidate progress so the dashboard reflects the slow
                # download+analyze+semantic cascade in real time (not a silent gap).
                ctx.update_stage(
                    "visual_local_qa",
                    "in_progress",
                    current_span=span_id,
                    # Scenes covered by this span so the dashboard shows WHICH scene
                    # is being QA'd (operators think in scenes, not span ids).
                    current_scene_ids=[str(s) for s in (span.get("scene_ids") or [])],
                    span_index=span_index,
                    span_total=span_total,
                    candidate_index=cand_index,
                    candidate_total=candidate_total,
                    downloads=download_count,
                )
                downloaded: dict[str, Any] | None = None
                analysis: dict[str, Any] | None = None
                trim: dict[str, Any] | None = None
                error: str | None = None
                try:
                    downloaded = downloader.download(
                        candidate=candidate, short_dir=ctx.short_dir, span_id=span_id
                    )
                    download_count += 1
                    digest = str(downloaded.get("content_hash") or "")
                    if digest and digest in seen_hashes:
                        error = "duplicate_content_hash"
                    else:
                        if digest:
                            seen_hashes.add(digest)
                        analysis = analyzer.analyze(
                            Path(downloaded["local_path"]), required_frames=required_frames, fps=fps
                        )
                        analysis["fps"] = fps
                        trim = select_trim_window(
                            analysis, required_frames=required_frames, config=trim_cfg
                        )
                except Exception as exc:  # noqa: BLE001 - candidate QA records failure and tries next finalist.
                    error = f"{exc.__class__.__name__}:{exc}"
                verdict, rejection_reasons = _candidate_verdict(
                    downloaded=downloaded,
                    analysis=analysis,
                    trim=trim,
                    error=error,
                )
                semantic_records: list[dict[str, Any]] = []
                if verdict == "PASS" and downloaded and trim and trim.get("status") == "selected":
                    semantic_records = _semantic_records(
                        span,
                        candidate_id=downloaded.get("candidate_id"),
                        local_qa=local_qa,
                        semantic_analyzer=semantic_analyzer,
                        video_path=downloaded.get("local_path"),
                        duration_sec=float((analysis or {}).get("actual_duration_sec") or 0.0),
                    )
                    semantic_verdict = _qa_verdict(
                        semantic_records, verdict, critical=action_critical
                    )
                    if semantic_verdict == "FAIL":
                        rejection_reasons.append("semantic_mismatch")
                    verdict = semantic_verdict
                    if verdict == "CAPABILITY_REDUCED" and _reject_capability_reduced_candidate(
                        mode=mode,
                        local_qa=local_qa,
                        span=span,
                        records=semantic_records,
                    ):
                        verdict = "FAIL"
                        rejection_reasons.append("semantic_weak_required_support")
                candidate_qas.append(
                    {
                        "candidate_id": cid,
                        "provider": candidate.get("provider"),
                        "provider_asset_id": candidate.get("provider_asset_id"),
                        "downloaded": downloaded is not None,
                        "content_hash": (downloaded or {}).get("content_hash"),
                        "local_path": (downloaded or {}).get("local_path"),
                        "public_ref": (downloaded or {}).get("public_ref"),
                        "analysis": analysis,
                        "trim_window": trim,
                        "evidence_records": semantic_records,
                        "qa": {"verdict": verdict, "rejection_reasons": rejection_reasons},
                    }
                )
                if verdict in {"PASS", "CAPABILITY_REDUCED"} and downloaded and trim and trim.get(
                    "status"
                ) == "selected":
                    final = downloaded
                    final_trim = trim
                    final_analysis = analysis
                    final_semantic_records = semantic_records
                    break

            semantic_records = final_semantic_records or _semantic_records(
                span,
                candidate_id=final.get("candidate_id") if final else None,
                local_qa=local_qa,
                semantic_analyzer=semantic_analyzer,
                video_path=(final or {}).get("local_path") if final else None,
                duration_sec=float((final_analysis or {}).get("actual_duration_sec") or 0.0),
            )
            span_verdict = _qa_verdict(
                semantic_records,
                "PASS" if final else "FAIL",
                critical=action_critical,
            )
            final_id = final.get("candidate_id") if final else None
            status = _selection_status(str(provisional_id) if provisional_id else None, final_id)
            span_qa = {
                "visual_span_id": span_id,
                "scene_ids": span.get("scene_ids") or [],
                "visual_route": visual_route,
                "required_duration_in_frames": required_frames,
                "provisional_candidate_id": provisional_id,
                "final_candidate_id": final_id,
                "final_selection_status": status,
                "render_eligible": bool(final),
                "requires_local_validation": False if final else True,
                "candidate_qa": candidate_qas,
                "evidence_records": semantic_records,
                "qa": {"verdict": span_verdict, "errors": [], "warnings": []},
            }
            span_qas.append(span_qa)
            if final and final_trim and final_analysis:
                trim_spans.append(
                    {
                        "visual_span_id": span_id,
                        "scene_ids": span.get("scene_ids") or [],
                        "final_candidate_id": final["candidate_id"],
                        "provider": final.get("provider"),
                        "provider_asset_id": final.get("provider_asset_id"),
                        "asset_ref": final.get("public_ref") or final.get("local_path"),
                        "local_path": final.get("local_path"),
                        "source_duration_sec": final_analysis.get("actual_duration_sec"),
                        "selected_window_start_in_frames": final_trim[
                            "selected_window_start_in_frames"
                        ],
                        "selected_window_end_in_frames": final_trim[
                            "selected_window_end_in_frames"
                        ],
                        "trim_timebase_fps": final_trim["trim_timebase_fps"],
                        "required_duration_in_frames": required_frames,
                        "window_score": final_trim.get("window_score"),
                        "motion_band": final_analysis.get("motion_band"),
                        "crop_stability_score": (final_analysis.get("crop_feasibility") or {}).get(
                            "crop_stability_score"
                        ),
                        "crop_plan": {
                            "mode": "cover",
                            "anchor": "center",
                            "scale": 1.0,
                            "target": span.get("crop_target") or "",
                        },
                        "qa": span_qa["qa"],
                    }
                )

        verdicts = [(s.get("qa") or {}).get("verdict") for s in span_qas]
        overall = (
            "FAIL"
            if any(v == "FAIL" for v in verdicts)
            else (
                "CAPABILITY_REDUCED" if any(v == "CAPABILITY_REDUCED" for v in verdicts) else "PASS"
            )
        )
        critical_fail = (
            mode == "enforced"
            and bool(local_qa.get("critical_fail_closed", True))
            and any(
                span.get("visual_importance") == "critical"
                and (span_qa.get("qa") or {}).get("verdict") == "CAPABILITY_REDUCED"
                for span, span_qa in zip(acquisition.get("spans") or [], span_qas, strict=False)
            )
        )
        input_hash = stable_hash(
            {
                "acquisition": acquisition,
                "candidates": candidates_doc,
                "selection": selection_doc,
                "fps": fps,
            }
        )
        asset_qa = {
            **_artifact_header(
                short_id, input_hash=input_hash, created_by_stage="visual_span_asset_qa"
            ),
            "mode": mode,
            "summary": {
                "span_count": len(span_qas),
                "download_count": download_count,
                "promoted_count": sum(
                    1 for s in span_qas if s["final_selection_status"] == "promoted"
                ),
                "replaced_count": sum(
                    1 for s in span_qas if s["final_selection_status"] == "replaced"
                ),
                "rejected_count": sum(
                    1 for s in span_qas if s["final_selection_status"] == "rejected"
                ),
                "capability_reduced_count": sum(
                    1 for s in span_qas if (s["qa"] or {}).get("verdict") == "CAPABILITY_REDUCED"
                ),
            },
            "spans": span_qas,
            "qa": {"verdict": overall, "errors": [], "warnings": []},
        }
        trim_doc = {
            **_artifact_header(
                short_id, input_hash=input_hash, created_by_stage="trim_window_plan"
            ),
            "fps": fps,
            "trim_timebase_fps": fps,
            "spans": trim_spans,
            "qa": {"verdict": "PASS" if trim_spans else "FAIL", "errors": [], "warnings": []},
        }

        atomic_write_json(ctx.json_dir / paths.SHORT_VISUAL_SPAN_ASSET_QA_FILE, asset_qa)
        atomic_write_json(ctx.json_dir / paths.SHORT_TRIM_WINDOW_PLAN_FILE, trim_doc)
        ctx.extras["visual_span_asset_qa"] = asset_qa
        ctx.extras["trim_window_plan"] = trim_doc

        if critical_fail:
            ctx.update_stage(
                "visual_local_qa", "failed", verdict="FAIL", reason="critical_capability_reduced"
            )
            ctx.status["status"] = "failed"
            write_short_status(ctx.long_job_dir, short_id, ctx.status)
            return StageResult(
                returns={
                    "status": "failed",
                    "failure_stage": "visual_local_qa",
                    "failure_reason": "critical_semantic_capability_unavailable",
                }
            )

        ctx.update_stage(
            "visual_local_qa",
            "completed",
            verdict=overall,
            mode=mode,
            download_count=download_count,
            trim_count=len(trim_spans),
        )
        return _PROCEED
    except Exception as exc:  # noqa: BLE001 - report-only must preserve legacy rendering.
        if mode == "enforced":
            ctx.update_stage("visual_local_qa", "failed", error=str(exc))
            ctx.status["status"] = "failed"
            write_short_status(ctx.long_job_dir, short_id, ctx.status)
            raise
        ctx.update_stage("visual_local_qa", "skipped", error=str(exc), mode=mode)
        return _PROCEED
