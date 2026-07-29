"""Long-form fixed-ROI continuity render proof (Phase 2 / Gate 2).

Renders the long-form ``ChannelVideoStandard`` composition through Remotion with a
compiled ``visual_schedule`` whose single ``background_media`` track mounts ONE
native clip continuously across three editorial scenes, then decodes the per-frame
marker ROI from the rendered output and asserts the source playhead advances 1:1
with no reset / skip / repeat / black across every scene boundary. This is the
measured no-reset proof for ``ChannelVisualTimeline`` — not a heuristic.

Landscape (1920x1080) marker so ``objectFit: cover`` does not crop the top-left
ROI. Marked ``integration``; skips ONLY when the render toolchain is unavailable.
Once the toolchain is present a render error FAILS (never a silent skip).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import _continuity_fixture as cf  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
REMOTION = REPO / "remotion"
FIXTURE = REMOTION / "public" / "test_fixtures" / "continuity_marker_landscape.mp4"

# Scene plan: 2.0 / 2.5 / 1.5 sec @ 30fps = 60 / 75 / 45 frames (total 180).
TRIM_BEFORE = 42


def _render_available() -> bool:
    return bool(
        shutil.which("node")
        and shutil.which("ffmpeg")
        and (REMOTION / "node_modules" / "@remotion" / "renderer").exists()
    )


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _render_available(),
        reason="render toolchain unavailable (node/ffmpeg/@remotion/renderer)",
    ),
]


def _scene(sid: str, dur: float) -> dict:
    return {
        "id": sid, "duration_sec": dur, "narration": "", "caption": "", "on_screen_text": "",
        "visual_type": "stock", "visual_prompt": "", "motion": "none", "layout": "subtitle",
        "asset_refs": {"background": ""}, "layout_payload": {},
    }


def _build_props() -> dict:
    scenes = [_scene("scene-01", 2.0), _scene("scene-02", 2.5), _scene("scene-03", 1.5)]
    boundaries = [
        {"scene_id": "scene-01", "from_frame": 0, "duration_in_frames": 60, "end_frame_exclusive": 60, "is_graphic": False},
        {"scene_id": "scene-02", "from_frame": 60, "duration_in_frames": 75, "end_frame_exclusive": 135, "is_graphic": False},
        {"scene_id": "scene-03", "from_frame": 135, "duration_in_frames": 45, "end_frame_exclusive": 180, "is_graphic": False},
    ]
    track = {
        "track_id": "vt01", "track_type": "background_media", "visual_span_id": "vs01",
        "scene_ids": ["scene-01", "scene-02", "scene-03"],
        "asset_ref": "test_fixtures/continuity_marker_landscape.mp4",
        "render_media_kind": "video", "source_media_kind": "native_video",
        "from_frame": 0, "duration_in_frames": 180, "end_frame_exclusive": 180,
        "trim_before_in_frames": TRIM_BEFORE, "trim_timebase_fps": 30, "trim_end_in_frames": None,
        "playback_rate": 1.0, "loop_policy": "forbid",
    }
    schedule = {
        "schema_version": 2, "fps": 30, "timing_source": "tts_final",
        "total_duration_in_frames": 180, "scene_boundaries": boundaries, "tracks": [track],
    }
    return {
        "channel": {"id": "vida-plena-45", "name": "Vida Plena", "description": "fixture"},
        "style": {"palette": {"background": "#0b1020", "primary": "#fff", "secondary": "#ccc", "accent": "#f5a", "text": "#fff"}},
        "render": {
            "fps": 30, "resolution": "1920x1080", "duration_sec": 6.0, "duration_in_frames": 180,
            "subtitles": {"enabled": False},
        },
        "scenes": scenes, "audio": {"narration": None, "music": None},
        "seo": {"title": "t", "description": "d", "thumbnail_path": "x.jpg"},
        "branding": {"intro_sec": 0, "outro_sec": 0, "show_channel_name_overlay": False, "logo_path": None},
        "visual_schedule": schedule,
    }


def _render(props: dict, tmp_path: Path) -> Path:
    props_path = tmp_path / "props.json"
    out_path = tmp_path / "out.mp4"
    props_path.write_text(json.dumps(props))
    try:
        subprocess.run(
            ["npx", "--prefix", str(REMOTION), "remotion", "render", "src/index.ts",
             "ChannelVideoStandard", str(out_path), f"--props={props_path}"],
            check=True, capture_output=True, cwd=str(REMOTION), timeout=600,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        pytest.fail(f"Remotion render FAILED (exit {exc.returncode}):\n{stderr[-4000:]}")
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"Remotion render TIMED OUT after {exc.timeout}s")
    assert out_path.exists(), "Remotion produced no output"
    return out_path


def test_channel_timeline_continuity_exact_marker(tmp_path: Path) -> None:
    cf.generate_fixture(FIXTURE, width=1920, height=1080)
    out_path = _render(_build_props(), tmp_path)
    vals = cf.decode_video_markers(out_path)

    # 1. Composition frame count equals the schedule total.
    assert len(vals) == 180, f"expected 180 rendered frames, got {len(vals)}"
    # 2. No black/unreadable boundary frames.
    none_frames = [f for f, v in enumerate(vals) if v is None]
    assert not none_frames, f"black/unreadable output frames: {none_frames[:5]}"
    # 3. Exact source identity: output frame F shows source frame TRIM_BEFORE+F
    #    (proves trim applied + one continuous mount, no reset at boundaries).
    expected = [TRIM_BEFORE + f for f in range(len(vals))]
    mism = [(f, vals[f], expected[f]) for f in range(len(vals)) if vals[f] != expected[f]]
    assert not mism, f"source identity drift (reset/skip/repeat): {mism[:8]}"
    # 4. Every span-internal scene boundary increments by exactly one.
    for boundary in (59, 134):  # scene-01->02 and scene-02->03 transitions
        assert vals[boundary + 1] - vals[boundary] == 1, (
            f"discontinuity at output frame {boundary}->{boundary + 1}: "
            f"{vals[boundary]}->{vals[boundary + 1]}"
        )


def test_legacy_render_without_schedule_succeeds(tmp_path: Path) -> None:
    """Gate 2(b): a job WITHOUT a compiled schedule still renders through the
    unchanged per-scene background path (legacy fallback intact)."""
    props = _build_props()
    props.pop("visual_schedule")
    # Give scenes a real background so the legacy per-scene path has media.
    cf.generate_fixture(FIXTURE, width=1920, height=1080)
    for s in props["scenes"]:
        s["asset_refs"]["background"] = "test_fixtures/continuity_marker_landscape.mp4"
    out_path = _render(props, tmp_path)
    vals = cf.decode_video_markers(out_path)
    # Legacy path mounts one clip PER scene (each restarts at source 0) and keeps the
    # per-scene fade-in/out, so early/boundary frames may be black. We only assert the
    # fallback renders the full frame count and is readable mid-scene (frame 30).
    assert len(vals) == 180
    assert vals[30] is not None, "legacy first scene unreadable mid-scene"


def test_disclaimer_delays_content_schedule_without_consuming_it(tmp_path: Path) -> None:
    """Intro + disclaimer occupy their own frames before scene-backed content."""
    props = _build_props()
    props["branding"] = {
        **props["branding"],
        "intro_sec": 1.0,
        "medical_disclaimer": {
            "enabled": True,
            "duration_sec": 1.0,
            "title": "AVISO MÉDICO",
            "lines": ["Contenido informativo.", "Consulta a tu médico."],
        },
    }
    props["render"]["duration_sec"] = 8.0
    props["render"]["duration_in_frames"] = 240
    cf.generate_fixture(FIXTURE, width=1920, height=1080)

    vals = cf.decode_video_markers(_render(props, tmp_path))

    assert len(vals) == 240
    assert all(value is None for value in vals[:60])
    readable = [(frame, value) for frame, value in enumerate(vals[60:], 60) if value is not None]
    assert readable[0][0] <= 60 + 12  # content is only briefly covered by BridgeFade
    assert all(
        value == TRIM_BEFORE + frame - 60
        for frame, value in readable
    )
