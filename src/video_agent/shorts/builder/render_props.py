"""Short render handoff writer + shared prepared-short final-props builder.

Two distinct artifacts (spec v3.2.3 §23, §24):

- ``short_render_props.json`` (the *handoff*): a builder→render supplement, NOT
  the final Remotion props. Written by :func:`_write_render_props`.
- ``json/render_props.json`` (the *final props*): the canonical complete Remotion
  input, built by the single shared owner :func:`build_prepared_short_render_props`
  and consumed by both the initial render and every existing-Short rerender path.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from video_agent.shorts import paths
from video_agent.shorts.asset_schedule import compute_schedule_hash
from video_agent.shorts.builder.snapshots import _scene_duration_sum
from video_agent.storage.atomic import atomic_write_json


def _scene_timing_hash(short_scenes: dict, fps: int) -> str:
    """Stable hash over ordered scene ids + durations + fps (§40.4).

    Used to detect post-compile timing drift between the handoff and the final
    props (any drift invalidates an embedded schedule)."""
    payload = {
        "fps": fps,
        "scenes": [
            {"id": str(s.get("id") or s.get("scene_id") or i), "duration_sec": float(s.get("duration_sec") or 0.0)}
            for i, s in enumerate(short_scenes.get("scenes") or [])
        ],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _render_block(channel_config: dict, duration_sec: float) -> dict[str, Any]:
    rcfg = (channel_config.get("shorts") or {}).get("render") or {}
    base_render = channel_config.get("render") or {}
    return {
        "composition": rcfg.get("composition", "ShortVideoStandard"),
        "resolution": rcfg.get("resolution", "1080x1920"),
        "fps": rcfg.get("fps", base_render.get("fps", 30)),
        "duration_sec": duration_sec,
        "codec": rcfg.get("codec", base_render.get("codec", "h264")),
        "video_bitrate": rcfg.get("video_bitrate", base_render.get("video_bitrate")),
        "gl": rcfg.get("gl", base_render.get("gl")),
        "concurrency": rcfg.get("concurrency", base_render.get("concurrency", "auto")),
    }


def _write_render_props(
    short_dir: Path,
    short_scenes: dict,
    channel_config: dict,
    music_track: str | None,
    visual_schedule: dict | None = None,
    *,
    scene_version: int | None = None,
) -> None:
    """Write the Short render handoff supplement ``short_render_props.json`` (§23).

    Existing call sites remain valid (``visual_schedule`` / ``scene_version``
    default to ``None``). When a compiled schedule exists it is written verbatim
    for debug/handoff along with its hash and ``duration_in_frames``; scene
    durations are never mutated here beyond the existing total rounding.
    """
    duration_sec = _scene_duration_sum(short_scenes) or float(
        short_scenes.get("total_duration_sec") or 35
    )
    short_scenes["total_duration_sec"] = round(duration_sec, 1)
    render_block = _render_block(channel_config, duration_sec)
    fps = int(render_block["fps"] or 30)

    schedule_hash = compute_schedule_hash(visual_schedule) if visual_schedule else None
    duration_in_frames = (
        (visual_schedule or {}).get("total_duration_in_frames") if visual_schedule else None
    )
    if duration_in_frames is not None:
        render_block["duration_in_frames"] = duration_in_frames

    props = {
        "schema_version": 1,
        "contract_revision": "3.2.3",
        "short_id": short_scenes.get("short_id") or short_dir.name,
        "scene_version": scene_version,
        "scene_timing_hash": _scene_timing_hash(short_scenes, fps),
        "visual_schedule_hash": schedule_hash,
        "render": render_block,
        # Legacy top-level fields kept for ShortVideo.tsx + materialize back-compat.
        "scenes": short_scenes.get("scenes") or [],
        "total_duration_sec": duration_sec,
        "audio": "audio/short_mix.m4a",
        "music_track": music_track,
        # Handoff carries the validated schedule verbatim for the prepared-short
        # final-props path; final render activation is decided there, not here.
        "visual_schedule": visual_schedule or {},
    }
    jd = short_dir / paths.SHORT_JSON_SUBDIR
    jd.mkdir(parents=True, exist_ok=True)
    atomic_write_json(jd / paths.SHORT_RENDER_PROPS_FILE, props)


# --------------------------------------------------------------------------- #
# §24.2 — shared prepared-short final-props builder (single owner)
# --------------------------------------------------------------------------- #
class PreparedPropsError(RuntimeError):
    """Raised when an enforced prepared-short merge fails verification (§24.3)."""


def _audio_object(assets_manifest: dict, short_scenes: dict) -> dict[str, Any]:
    """Normalize audio to the RenderProps ``{narration, music}`` shape (§23).

    Sourced from the already-resolved manifest audio block (the prepared short's
    mix already exists) — never by re-running asset/TTS preparation."""
    audio = (assets_manifest or {}).get("audio")
    if isinstance(audio, dict):
        return audio
    return {"narration": "audio/short_mix.m4a", "music": None}


def build_prepared_short_render_props(
    *,
    short_dir: Path,
    channel_config: dict[str, Any],
    style: dict[str, Any],
    scenes: dict[str, Any],
    assets_manifest: dict[str, Any],
    seo: dict[str, Any],
    branding: dict[str, Any],
    handoff: dict[str, Any],
    visual_timeline_mode: str,
) -> dict[str, Any]:
    """Assemble the canonical final ``render_props.json`` for a prepared Short.

    Single owner of final-props construction for the initial render and EVERY
    existing-Short rerender path (§24.2). It reads only already-prepared artifacts
    — it never re-runs ``prepare_assets``, re-acquires backgrounds, re-plans crops,
    regenerates TTS, or mutates scene durations.

    Mode behavior (§22/§23):
    - ``report_only`` / ``disabled``: the final props intentionally omit/null
      ``visual_schedule`` (renderer stays legacy).
    - ``enforced``: the exact validated schedule from the handoff is embedded and
      verified (§24.3); a mismatch raises :class:`PreparedPropsError`.
    """
    scene_list = scenes.get("scenes") or []
    # Shorts never render the long-form intro/outro brand clips: the Remotion Short
    # composition renders scenes ONLY, and a multi-second intro/outro on a ~20-30s
    # Short would wreck opening retention (and mismatch the encoded MP4 — bug-478,
    # after branding.enable_intro_outro was turned on channel-wide 2026-07-01 for
    # long-form). Force them to zero here so render.duration_sec == scene_sum and the
    # embedded branding does not point the renderer at intro/outro clips.
    branding = {
        **branding,
        "intro_sec": 0.0,
        "outro_sec": 0.0,
        "intro_video_path": None,
        "outro_video_path": None,
    }
    intro = 0.0
    outro = 0.0
    scene_sum = round(sum(float(s.get("duration_sec") or 0.0) for s in scene_list), 1)

    render_config = dict(channel_config.get("render") or {})
    shorts_render = (channel_config.get("shorts") or {}).get("render") or {}
    for k, v in shorts_render.items():
        if v is not None:
            render_config[k] = v
    render_config["duration_sec"] = scene_sum + intro + outro

    schedule = handoff.get("visual_schedule") or {}
    embed_schedule: dict[str, Any] | None = None
    if visual_timeline_mode == "enforced" and schedule:
        _verify_enforced_merge(schedule, handoff, scenes, render_config)
        embed_schedule = schedule
        render_config["duration_in_frames"] = schedule.get("total_duration_in_frames")

    return {
        "channel": channel_config["channel"],
        "style": style,
        "render": render_config,
        "scenes": scene_list,
        "audio": _audio_object(assets_manifest, scenes),
        "seo": seo,
        "branding": branding,
        "visual_schedule": embed_schedule,
    }


def _verify_enforced_merge(
    schedule: dict[str, Any], handoff: dict[str, Any], scenes: dict[str, Any], render_config: dict[str, Any]
) -> None:
    """§24.3 merge verification for enforced mode. Raises on any mismatch."""
    fps = int(render_config.get("fps") or 30)
    errors: list[str] = []
    if schedule.get("fps") != fps:
        errors.append(f"schedule_fps!=render_fps ({schedule.get('fps')}!={fps})")
    expected_hash = handoff.get("visual_schedule_hash")
    if expected_hash and compute_schedule_hash(schedule) != expected_hash:
        errors.append("schedule_hash_mismatch")
    current_timing = _scene_timing_hash(scenes, fps)
    if handoff.get("scene_timing_hash") and handoff["scene_timing_hash"] != current_timing:
        errors.append("scene_timing_hash_mismatch")
    sv = handoff.get("scene_version")
    if sv is not None and schedule.get("scene_version") is not None and schedule.get("scene_version") != sv:
        errors.append(f"scene_version_mismatch ({schedule.get('scene_version')}!={sv})")
    if (schedule.get("qa") or {}).get("verdict") not in (None, "PASS"):
        errors.append("schedule_qa_not_pass")
    if errors:
        raise PreparedPropsError("; ".join(errors))
