"""Compiled asset schedule — schema-v2 deterministic visual timeline (spec v3.2.3
§15, §16, §16.1, §17).

This is the renderer's single source of truth for the visual track: which asset
renders over which absolute output frame range, with what crop / trim / motion.
It is compiled AFTER final audio-tail repair (so scene frame boundaries are
final) and consumed by the prepared-short final props + Remotion VisualTimeline.

Phase 2 scope (this module):
- ``continuous_clip``: one native video track spanning a multi-scene span, when
  an already-resolved member-scene native video is eligible (§17). No new
  provider acquisition.
- ``legacy_scene_assets``: one track per scene using its existing asset (the
  compatibility / fallback mode).
- graphic scenes: omit the background track (rendered full-screen by SceneTimeline).

Everything here is pure except an optional ffprobe duration probe for native
candidates missing ``source_duration_sec`` (§16.1).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from video_agent.shorts.frames import seconds_to_frames
from video_agent.shorts.visual_spans import _is_graphic_scene, _scene_duration, _scene_id

SCHEMA_VERSION = 2
CONTRACT_REVISION = "3.2.3"
COMPILER_VERSION = 1

VIDEO_CONTAINER_EXTS = {".mp4", ".mov", ".webm"}
IMAGE_CONTAINER_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
# asset_tier / provider values that denote real native video footage.
VIDEO_TIERS = {"pexels_video", "pixabay_video", "coverr_video"}
IMAGE_TIERS = {"ai_image", "pexels_photo", "pixabay_photo", "coverr_photo", "stock_photo"}
PLACEHOLDER_MARKERS = {"placeholder", "generated_placeholder", "color_card", "solid"}

# source_media_kind values (§15.1)
NATIVE_VIDEO = "native_video"
IMAGE_BACKED_VIDEO = "image_backed_video"
NATIVE_IMAGE = "native_image"
GENERATED_PLACEHOLDER = "generated_placeholder"


# --------------------------------------------------------------------------- #
# §14.2 / §16 — scene frame timeline
# --------------------------------------------------------------------------- #
def build_scene_frame_timeline(scenes: list[dict[str, Any]], fps: int) -> list[dict[str, Any]]:
    """Cumulative per-scene frame boundaries (§14.2). Starts accumulate; they are
    never recomputed independently from accumulated seconds."""
    timeline: list[dict[str, Any]] = []
    from_frame = 0
    for idx, scene in enumerate(scenes):
        dur = seconds_to_frames(_scene_duration(scene), fps)
        end = from_frame + dur
        timeline.append(
            {
                "scene_id": _scene_id(scene, idx),
                "from_frame": from_frame,
                "duration_in_frames": dur,
                "end_frame_exclusive": end,
                "is_graphic": _is_graphic_scene(scene),
            }
        )
        from_frame = end
    return timeline


# --------------------------------------------------------------------------- #
# §16.1 — resolved visual manifest adapter
# --------------------------------------------------------------------------- #
def _ext(ref: str | None) -> str:
    return Path(str(ref or "")).suffix.lower()


def _derive_media_kinds(entry: dict[str, Any]) -> tuple[str, str | None]:
    """Return ``(render_media_kind, source_media_kind)`` for a manifest entry.

    ``source_media_kind`` may be ``None`` (unknown) — the caller must then exclude
    the candidate from multi-scene continuity (never infer native_video from a
    ``.mp4`` container alone, §16.1).
    """
    public_ref = entry.get("public_background") or entry.get("background")
    container_ext = _ext(public_ref)
    render_media_kind = "video" if container_ext in VIDEO_CONTAINER_EXTS else "image"

    tier = str(entry.get("asset_tier") or "").strip().lower()
    provider = str(entry.get("provider") or "").strip().lower()
    media_kind = str(entry.get("media_kind") or "").strip().lower()
    source = str(entry.get("source") or "").strip().lower()

    is_placeholder = (
        source in PLACEHOLDER_MARKERS
        or tier in PLACEHOLDER_MARKERS
        or provider in PLACEHOLDER_MARKERS
        or "placeholder" in tier
        or "placeholder" in provider
    )
    is_native_video = (
        media_kind == "video"
        or tier in VIDEO_TIERS
        or provider in VIDEO_TIERS
        or provider.endswith("_video")
    )
    is_image_source = (
        media_kind == "image" or tier in IMAGE_TIERS or provider == "ai_generated"
    )

    if is_placeholder:
        return render_media_kind, GENERATED_PLACEHOLDER
    if container_ext in IMAGE_CONTAINER_EXTS:
        return render_media_kind, NATIVE_IMAGE
    if is_native_video:
        return render_media_kind, NATIVE_VIDEO
    if is_image_source:
        return render_media_kind, IMAGE_BACKED_VIDEO
    return render_media_kind, None  # unknown → caller excludes from continuity


def _resolve_local_path(entry: dict[str, Any], short_dir: Path) -> Path | None:
    raw = entry.get("background") or entry.get("local_path")
    if not raw:
        public = str(entry.get("public_background") or "")
        if public:
            # public refs look like jobs/<id>/assets/sNN.mp4; map to short assets dir.
            name = Path(public).name
            cand = short_dir / "assets" / name
            return cand if cand.exists() else None
        return None
    p = Path(str(raw))
    return p if p.is_absolute() else (short_dir / p)


def _semantic_rejection(
    entry: dict[str, Any], scene_id: str, background_report: dict[str, Any] | None
) -> tuple[bool, str | None]:
    """§16.1 semantic-rejection lookup order. Returns ``(rejected, source)``.

    Only records matching the active scene reject it; stale records for other
    candidates must not. Lookup order: asset_selection fields → scene entry →
    background_report scene record.
    """
    sel = entry.get("asset_selection") or {}
    if sel.get("semantic_rejected") is True:
        return True, "asset_selection.semantic_rejected"
    if sel.get("rejection_reasons"):
        return True, "asset_selection.rejection_reasons"
    if entry.get("semantic_rejected") is True:
        return True, "scene.semantic_rejected"
    if entry.get("rejection_reasons"):
        return True, "scene.rejection_reasons"
    for rec in (background_report or {}).get("scenes") or []:
        if str(rec.get("scene_id")) != str(scene_id):
            continue
        if rec.get("semantic_rejected") is True or rec.get("rejection_reasons"):
            return True, "background_report.scene"
    return False, None


def adapt_assets_manifest(
    manifest: dict[str, Any],
    *,
    short_dir: Path,
    background_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize raw ``assets_manifest.json`` scene entries into one canonical
    shape (§16.1). Returns ``{"scenes": {scene_id: adapted}, "warnings": [...]}``.

    The adapter is the single place Phase 2 reads the manifest; no other module
    should parse raw manifest entries ad hoc.
    """
    scenes_out: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for entry in (manifest or {}).get("scenes") or []:
        scene_id = str(entry.get("scene_id") or entry.get("id") or "")
        if not scene_id:
            continue
        public_ref = entry.get("public_background") or entry.get("public_ref")
        local_path = _resolve_local_path(entry, short_dir)
        render_media_kind, source_media_kind = _derive_media_kinds(entry)
        sel = entry.get("asset_selection") or {}

        score = sel.get("score")
        if score is None:
            warnings.append(f"missing_selection_score:{scene_id}")
        match_status = sel.get("asset_match_status") or "unknown"
        rejected, rejection_source = _semantic_rejection(entry, scene_id, background_report)

        exists = bool(local_path and local_path.exists()) or bool(public_ref)
        source_duration_sec = entry.get("source_duration_sec")

        # Base continuity eligibility (span-specific duration checked later).
        eligible = (
            source_media_kind == NATIVE_VIDEO
            and exists
            and not rejected
        )

        scenes_out[scene_id] = {
            "scene_id": scene_id,
            "local_path": str(local_path) if local_path else None,
            "public_ref": public_ref,
            "provider": entry.get("provider"),
            "provider_asset_id": entry.get("provider_asset_id"),
            "asset_id": entry.get("asset_id"),
            "asset_tier": entry.get("asset_tier"),
            "source": entry.get("source"),
            "render_media_kind": render_media_kind,
            "source_media_kind": source_media_kind,
            "source_duration_sec": (
                float(source_duration_sec) if isinstance(source_duration_sec, (int, float)) else None
            ),
            "selection_score": float(score) if isinstance(score, (int, float)) else None,
            "asset_match_status": match_status,
            "semantic_rejected": rejected,
            "semantic_rejection_source": rejection_source,
            "semantic_rejection_asset_match": sel.get("asset_match_status") if rejected else None,
            "rejection_reasons": list(sel.get("rejection_reasons") or entry.get("rejection_reasons") or []),
            "exists": exists,
            "eligible_for_multi_scene_continuity": eligible,
            "crop_plan": entry.get("crop_plan"),
            "adapter_warnings": [],
        }
    return {"scenes": scenes_out, "warnings": warnings}


# --------------------------------------------------------------------------- #
# §16.1 — duration probe (lazy ffprobe)
# --------------------------------------------------------------------------- #
def _known_source_duration(adapted: dict[str, Any]) -> float | None:
    dur = adapted.get("source_duration_sec")
    if isinstance(dur, (int, float)) and dur > 0:
        return float(dur)
    local = adapted.get("local_path")
    if not local or not Path(local).exists():
        return None
    try:  # reuse existing ffprobe helper (DRY)
        from video_agent.stages.render import probe_video_duration_sec

        probed = probe_video_duration_sec(Path(local))
    except Exception:  # noqa: BLE001 — probe failure must not crash compile
        return None
    if probed and probed > 0:
        adapted["source_duration_sec"] = float(probed)
        return float(probed)
    return None


# --------------------------------------------------------------------------- #
# §17 — deterministic Phase 2 asset reuse
# --------------------------------------------------------------------------- #
def select_continuous_asset_for_span(
    member_ids: list[str],
    adapted_scenes: dict[str, dict[str, Any]],
    span_seconds: float,
) -> dict[str, Any] | None:
    """Pick one already-resolved native video to cover a full continuous span,
    or ``None`` to fall back to per-scene tracks (§17). No provider calls.

    Phase-2 quality-first gate (§16.1): a candidate must be native video, exist,
    be unrejected, carry ``strong_match`` status, and have a known source duration
    ≥ the span duration. Unknown / weak / ai-generated match status is never
    promoted into a multi-scene continuous track merely to reduce asset count
    (§17A) — those fall back to legacy per-scene tracks. Ranking among eligible
    candidates: highest score, then earliest member scene (deterministic).
    """
    candidates: list[tuple[tuple[float, int], dict[str, Any]]] = []
    for order, sid in enumerate(member_ids):
        adapted = adapted_scenes.get(sid)
        if not adapted or adapted.get("source_media_kind") != NATIVE_VIDEO:
            continue
        if not adapted.get("exists") or adapted.get("semantic_rejected"):
            continue
        if str(adapted.get("asset_match_status")) != "strong_match":
            continue  # quality-first: unknown/weak/ai never enter continuity
        duration = _known_source_duration(adapted)
        if duration is None or duration + 1e-6 < span_seconds:
            continue  # too short / unknown duration → not eligible
        score = adapted.get("selection_score")
        score_key = -(score if isinstance(score, (int, float)) else -1.0)
        candidates.append(((score_key, order), {**adapted, "_source_scene_id": sid}))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


# --------------------------------------------------------------------------- #
# §15 / §16 — compile
# --------------------------------------------------------------------------- #
def _crop_plan(adapted: dict[str, Any] | None, scene: dict[str, Any]) -> dict[str, Any]:
    plan = (adapted or {}).get("crop_plan") or scene.get("crop_plan")
    if isinstance(plan, dict) and plan:
        return plan
    return {"mode": "cover", "anchor": "center", "scale": 1.0, "target": ""}


def _motion_plan(source_media_kind: str | None, scene: dict[str, Any]) -> dict[str, Any]:
    # Native video must never get synthetic drift (§4.3). Image-backed stills keep
    # their planned Ken Burns motion.
    if source_media_kind == NATIVE_VIDEO:
        return {"name": "none", "apply_to_native_video": False}
    name = str(scene.get("motion") or "none")
    return {"name": name, "apply_to_native_video": False}


def _legacy_track(
    track_id: str,
    span_id: str,
    scene: dict[str, Any],
    boundary: dict[str, Any],
    adapted: dict[str, Any] | None,
) -> dict[str, Any]:
    smk = (adapted or {}).get("source_media_kind") or GENERATED_PLACEHOLDER
    return {
        "track_id": track_id,
        "track_type": "background_media",
        "visual_span_id": span_id,
        "visual_beat_id": None,
        "scene_ids": [boundary["scene_id"]],
        "asset_ref": (adapted or {}).get("public_ref") or (adapted or {}).get("local_path") or "",
        "asset_id": (adapted or {}).get("asset_id"),
        "provider": (adapted or {}).get("provider"),
        "render_media_kind": (adapted or {}).get("render_media_kind") or "video",
        "source_media_kind": smk,
        "from_frame": boundary["from_frame"],
        "duration_in_frames": boundary["duration_in_frames"],
        "end_frame_exclusive": boundary["end_frame_exclusive"],
        "trim_before_in_frames": 0,
        "trim_timebase_fps": 0,
        "trim_end_in_frames": None,
        "source_duration_sec": (adapted or {}).get("source_duration_sec"),
        "playback_rate": 1.0,
        "loop_policy": "forbid",
        "crop_plan": _crop_plan(adapted, scene),
        "motion_plan": _motion_plan(smk, scene),
        "overlay_policy": "scene_controlled",
        "z_index": 0,
        "selection_debug": {
            "mode": "legacy_scene_assets",
            "asset_match_status": (adapted or {}).get("asset_match_status"),
            "semantic_rejection_source": (adapted or {}).get("semantic_rejection_source"),
        },
    }


def compile_asset_schedule(
    *,
    short_id: str,
    scene_doc: dict[str, Any],
    visual_spans: dict[str, Any],
    resolved_visuals: dict[str, Any],
    fps: int,
    timing_source: str,
    scene_version: int,
) -> dict[str, Any]:
    """Compile the schema-v2 schedule from final scene timing + spans + adapted
    manifest. Deterministic; no provider acquisition (§17)."""
    scenes = list((scene_doc or {}).get("scenes") or [])
    boundaries = build_scene_frame_timeline(scenes, fps)
    by_id = {b["scene_id"]: b for b in boundaries}
    scene_by_id = {_scene_id(s, i): s for i, s in enumerate(scenes)}
    adapted_scenes = (resolved_visuals or {}).get("scenes") or {}

    tracks: list[dict[str, Any]] = []
    track_n = 0

    def _next_track_id() -> str:
        nonlocal track_n
        track_n += 1
        return f"vt{track_n:02d}"

    for span in (visual_spans or {}).get("spans") or []:
        span_id = span.get("id")
        # Drop any span scene refs that are not real scenes (phantom refs) so
        # downstream boundary lookups never KeyError.
        member_ids = [sid for sid in (span.get("scene_ids") or []) if sid in scene_by_id]
        member_scenes = [scene_by_id[m] for m in member_ids]
        if not member_scenes:
            continue
        # Graphic spans omit the background track (full-screen graphic scene).
        if all(_is_graphic_scene(s) for s in member_scenes):
            continue

        span_seconds = sum(_scene_duration(s) for s in member_scenes)
        is_continuous = (
            span.get("planned_mode") == "continuous_clip"
            and len(member_ids) >= 2
            and not any(_is_graphic_scene(s) for s in member_scenes)
        )
        chosen = (
            select_continuous_asset_for_span(member_ids, adapted_scenes, span_seconds)
            if is_continuous
            else None
        )

        if chosen is not None:
            first_b = by_id[member_ids[0]]
            last_b = by_id[member_ids[-1]]
            src_scene = scene_by_id[chosen["_source_scene_id"]]
            tracks.append(
                {
                    "track_id": _next_track_id(),
                    "track_type": "background_media",
                    "visual_span_id": span_id,
                    "visual_beat_id": None,
                    "scene_ids": list(member_ids),
                    "asset_ref": chosen.get("public_ref") or chosen.get("local_path") or "",
                    "asset_id": chosen.get("asset_id"),
                    "provider": chosen.get("provider"),
                    "render_media_kind": "video",
                    "source_media_kind": NATIVE_VIDEO,
                    "from_frame": first_b["from_frame"],
                    "duration_in_frames": last_b["end_frame_exclusive"] - first_b["from_frame"],
                    "end_frame_exclusive": last_b["end_frame_exclusive"],
                    "trim_before_in_frames": 0,
                    "trim_timebase_fps": fps,
                    "trim_end_in_frames": None,
                    "source_duration_sec": chosen.get("source_duration_sec"),
                    "playback_rate": 1.0,
                    "loop_policy": "forbid",
                    "crop_plan": _crop_plan(chosen, src_scene),
                    "motion_plan": {"name": "none", "apply_to_native_video": False},
                    "overlay_policy": "scene_controlled",
                    "z_index": 0,
                    "selection_debug": {
                        "mode": "continuous_clip",
                        "query": (src_scene.get("asset_selection") or {}).get("query"),
                        "score": chosen.get("selection_score"),
                        "fallback": False,
                        "phase2_reuse_source_scene_id": chosen["_source_scene_id"],
                        "asset_match_status": chosen.get("asset_match_status"),
                    },
                }
            )
        else:
            # Fallback: one legacy track per non-graphic member scene.
            for m in member_ids:
                scene = scene_by_id[m]
                if _is_graphic_scene(scene):
                    continue
                tracks.append(
                    _legacy_track(_next_track_id(), span_id, scene, by_id[m], adapted_scenes.get(m))
                )

    total_frames = boundaries[-1]["end_frame_exclusive"] if boundaries else 0
    schedule = {
        "schema_version": SCHEMA_VERSION,
        "contract_revision": CONTRACT_REVISION,
        "compiler_version": COMPILER_VERSION,
        "short_id": short_id,
        "fps": fps,
        "timing_source": timing_source,
        "scene_version": scene_version,
        "total_duration_in_frames": total_frames,
        "scene_boundaries": boundaries,
        "tracks": tracks,
    }
    qa = validate_compiled_asset_schedule(schedule, scene_doc)
    schedule["qa"] = qa
    return schedule


def compute_schedule_hash(schedule: dict[str, Any], *, public_job_id: str = "") -> str:
    """Stable hash over the schedule's rendered content (§40.3).

    Covers final scene frames, fps, scene version, span/track refs, asset ids,
    trim, crop, motion, compiler version, and public job id — i.e. everything that
    must match between the handoff, the embedded schedule, and the rendered frames.
    The QA verdict is intentionally excluded so the hash is stable across re-runs.
    """
    payload = {
        "fps": schedule.get("fps"),
        "scene_version": schedule.get("scene_version"),
        "compiler_version": schedule.get("compiler_version"),
        "total_duration_in_frames": schedule.get("total_duration_in_frames"),
        "public_job_id": public_job_id,
        "scene_boundaries": schedule.get("scene_boundaries"),
        "tracks": [
            {
                k: tr.get(k)
                for k in (
                    "track_id", "visual_span_id", "scene_ids", "asset_ref", "asset_id",
                    "render_media_kind", "source_media_kind", "from_frame", "duration_in_frames",
                    "end_frame_exclusive", "trim_before_in_frames", "trim_timebase_fps",
                    "playback_rate", "loop_policy", "crop_plan", "motion_plan",
                )
            }
            for tr in schedule.get("tracks") or []
        ],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# --------------------------------------------------------------------------- #
# §16 — validate
# --------------------------------------------------------------------------- #
def validate_compiled_asset_schedule(
    schedule: dict[str, Any],
    scene_doc: dict[str, Any],
    *,
    render_fps: int | None = None,
    expected_scene_version: int | None = None,
) -> dict[str, Any]:
    """Structural validation of a compiled schedule (§16). Returns
    ``{"verdict", "errors", "warnings"}``. Invalid schedules must be rejected
    before Remotion in enforced mode."""
    errors: list[str] = []
    warnings: list[str] = []

    if schedule.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported_schema_version:{schedule.get('schema_version')}")
    fps = schedule.get("fps")
    if not isinstance(fps, int) or fps <= 0:
        errors.append("fps_not_positive")
    elif render_fps is not None and fps != render_fps:
        errors.append(f"fps_mismatch:{fps}!={render_fps}")

    scenes = list((scene_doc or {}).get("scenes") or [])
    boundaries = schedule.get("scene_boundaries") or []
    expected = build_scene_frame_timeline(scenes, fps) if isinstance(fps, int) and fps > 0 else []
    if len(boundaries) != len(expected):
        errors.append("scene_boundary_count_mismatch")
    else:
        cursor = 0
        for got, exp in zip(boundaries, expected, strict=True):
            if got.get("scene_id") != exp["scene_id"]:
                errors.append(f"scene_boundary_order_mismatch:{got.get('scene_id')}")
            if got.get("from_frame") != cursor:
                errors.append(f"scene_boundary_not_cumulative:{got.get('scene_id')}")
            if got.get("end_frame_exclusive") != got.get("from_frame", 0) + got.get("duration_in_frames", 0):
                errors.append(f"scene_boundary_end_mismatch:{got.get('scene_id')}")
            cursor = got.get("end_frame_exclusive", cursor)
        total = schedule.get("total_duration_in_frames")
        if total != cursor:
            errors.append(f"total_frames_mismatch:{total}!={cursor}")

    # Track invariants.
    nongraphic_frames_covered: dict[int, int] = {}
    for tr in schedule.get("tracks") or []:
        tid = tr.get("track_id")
        if tr.get("end_frame_exclusive") != tr.get("from_frame", 0) + tr.get("duration_in_frames", 0):
            errors.append(f"track_end_mismatch:{tid}")
        if tr.get("playback_rate") != 1.0:
            errors.append(f"playback_rate_not_1:{tid}")
        if tr.get("loop_policy") != "forbid":
            errors.append(f"loop_policy_not_forbid:{tid}")
        if (tr.get("trim_before_in_frames") or 0) < 0:
            errors.append(f"negative_trim:{tid}")
        ref = str(tr.get("asset_ref") or "")
        if not ref:
            errors.append(f"empty_asset_ref:{tid}")
        rmk = tr.get("render_media_kind")
        ext = _ext(ref)
        if rmk == "video" and ext and ext not in VIDEO_CONTAINER_EXTS:
            errors.append(f"render_media_kind_container_mismatch:{tid}")
        # native video must not carry synthetic drift
        if tr.get("source_media_kind") == NATIVE_VIDEO:
            mp = tr.get("motion_plan") or {}
            if mp.get("name") not in (None, "none") and not mp.get("apply_to_native_video"):
                errors.append(f"native_video_synthetic_drift:{tid}")
        # member scenes exist + contiguous + range == union of member boundaries
        sids = tr.get("scene_ids") or []
        idxs = [next((i for i, b in enumerate(boundaries) if b["scene_id"] == s), None) for s in sids]
        if any(i is None for i in idxs):
            errors.append(f"track_scene_missing:{tid}")
        elif idxs != list(range(idxs[0], idxs[0] + len(idxs))):
            errors.append(f"track_scenes_not_contiguous:{tid}")
        else:
            union_from = boundaries[idxs[0]]["from_frame"]
            union_end = boundaries[idxs[-1]]["end_frame_exclusive"]
            if tr.get("from_frame") != union_from or tr.get("end_frame_exclusive") != union_end:
                errors.append(f"track_range_not_member_union:{tid}")
        # continuous track must keep one stable crop + sufficient source duration
        if tr.get("selection_debug", {}).get("mode") == "continuous_clip":
            span_secs = (tr.get("duration_in_frames", 0)) / (fps or 1)
            src = tr.get("source_duration_sec")
            if isinstance(src, (int, float)) and src + 1e-6 < span_secs:
                errors.append(f"source_duration_insufficient:{tid}")
        # record covered frames for non-graphic overlap/gap checks
        for f in range(tr.get("from_frame", 0), tr.get("end_frame_exclusive", 0)):
            nongraphic_frames_covered[f] = nongraphic_frames_covered.get(f, 0) + 1

    # Every non-graphic frame covered by exactly one background track; graphic
    # frames may be uncovered.
    for b in boundaries:
        if b.get("is_graphic"):
            continue
        for f in range(b["from_frame"], b["end_frame_exclusive"]):
            count = nongraphic_frames_covered.get(f, 0)
            if count == 0:
                errors.append(f"uncovered_nongraphic_frame:{f}")
                break
            if count > 1:
                errors.append(f"overlapping_track_frame:{f}")
                break

    # version / timing
    if expected_scene_version is not None and schedule.get("scene_version") != expected_scene_version:
        errors.append(f"scene_version_mismatch:{schedule.get('scene_version')}!={expected_scene_version}")
    if schedule.get("timing_source") not in ("tts_final", "scene_plan"):
        warnings.append(f"unknown_timing_source:{schedule.get('timing_source')}")

    verdict = "FAIL" if errors else "PASS"
    return {"verdict": verdict, "errors": errors, "warnings": warnings}
