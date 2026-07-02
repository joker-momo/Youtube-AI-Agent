"""Segmented render (bug-441 follow-up): a long render is split into
frame-range chunks so a mid-render crash only loses one segment instead of
the whole render, and each segment's Remotion cache is freed immediately
after it lands on disk.

Uses real ffmpeg/ffprobe to generate and probe tiny test clips — the
verification logic (_segment_is_valid, _concat_segments) is exactly what
guards against trusting a corrupt/partial segment on resume, so it must be
tested against real files, not mocks.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from video_agent.stages.render import (
    RemotionSubprocessError,
    _clear_render_tmp_cache,
    _concat_segments,
    _segment_is_valid,
    _segment_plan,
    _segment_seconds,
    _segmented_render_enabled,
    _total_render_frames,
    build_remotion_commands,
)


def _make_clip(path: Path, *, seconds: float, fps: int = 30) -> None:
    """Real, ffprobe-able mp4 with silent audio (matches segment shape)."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s=320x180:r={fps}:d={seconds}",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-shortest",
            "-c:v", "libx264", "-c:a", "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# Segment planning
# ---------------------------------------------------------------------------

def test_segment_plan_covers_total_frames_exactly():
    plan = _segment_plan(total_frames=100, segment_frames=30)
    assert plan == [(0, 29), (30, 59), (60, 89), (90, 99)]
    covered = sum(end - start + 1 for start, end in plan)
    assert covered == 100


def test_segment_plan_single_segment_when_shorter_than_chunk():
    assert _segment_plan(total_frames=50, segment_frames=3000) == [(0, 49)]


def test_segment_plan_exact_multiple_has_no_trailing_short_segment():
    plan = _segment_plan(total_frames=90, segment_frames=30)
    assert plan == [(0, 29), (30, 59), (60, 89)]


def test_segment_seconds_reads_config_and_falls_back_to_default(tmp_path):
    props = tmp_path / "render_props.json"
    props.write_text(json.dumps({"render": {"segment_seconds": 45}}), encoding="utf-8")
    assert _segment_seconds(props) == 45.0

    props.write_text(json.dumps({"render": {}}), encoding="utf-8")
    from video_agent.stages.render import DEFAULT_SEGMENT_SECONDS
    assert _segment_seconds(props) == DEFAULT_SEGMENT_SECONDS


def test_segmented_render_defaults_to_enabled(tmp_path):
    props = tmp_path / "render_props.json"
    props.write_text(json.dumps({"render": {}}), encoding="utf-8")
    assert _segmented_render_enabled(props) is True

    props.write_text(json.dumps({"render": {"segmented": False}}), encoding="utf-8")
    assert _segmented_render_enabled(props) is False


def test_total_render_frames_prefers_duration_in_frames(tmp_path):
    props = tmp_path / "render_props.json"
    props.write_text(
        json.dumps({"render": {"duration_in_frames": 4321, "fps": 30, "duration_sec": 1.0}}),
        encoding="utf-8",
    )
    assert _total_render_frames(props) == 4321


def test_total_render_frames_falls_back_to_fps_times_duration(tmp_path):
    props = tmp_path / "render_props.json"
    props.write_text(json.dumps({"render": {"fps": 30, "duration_sec": 10.0}}), encoding="utf-8")
    assert _total_render_frames(props) == 300


# ---------------------------------------------------------------------------
# build_remotion_commands: --frames / --for-seamless-aac-concatenation
# ---------------------------------------------------------------------------

def test_frame_range_adds_frames_flag(tmp_path):
    props = tmp_path / "render_props.json"
    props.write_text("{}", encoding="utf-8")
    commands = build_remotion_commands(
        props, tmp_path / "seg_0001.mp4", frame_range=(0, 2999)
    )
    assert "--frames" in commands.video
    assert commands.video[commands.video.index("--frames") + 1] == "0-2999"


def test_seamless_concat_adds_flag_only_when_requested(tmp_path):
    props = tmp_path / "render_props.json"
    props.write_text("{}", encoding="utf-8")
    plain = build_remotion_commands(props, tmp_path / "v.mp4")
    assert "--for-seamless-aac-concatenation" not in plain.video

    seamless = build_remotion_commands(
        props, tmp_path / "v.mp4", frame_range=(0, 99), seamless_concat=True
    )
    assert "--for-seamless-aac-concatenation" in seamless.video


# ---------------------------------------------------------------------------
# Segment verification (resume safety) — real ffprobe against real clips
# ---------------------------------------------------------------------------

def test_segment_is_valid_accepts_matching_duration(tmp_path):
    seg = tmp_path / "seg_0001.mp4"
    _make_clip(seg, seconds=1.0, fps=30)  # 30 frames @ 30fps
    assert _segment_is_valid(seg, start=0, end=29, fps=30.0) is True


def test_segment_is_valid_rejects_short_partial_write(tmp_path):
    seg = tmp_path / "seg_0001.mp4"
    _make_clip(seg, seconds=0.3, fps=30)  # far short of the expected 1.0s
    assert _segment_is_valid(seg, start=0, end=29, fps=30.0) is False


def test_segment_is_valid_rejects_missing_or_empty_file(tmp_path):
    missing = tmp_path / "nope.mp4"
    assert _segment_is_valid(missing, start=0, end=29, fps=30.0) is False

    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    assert _segment_is_valid(empty, start=0, end=29, fps=30.0) is False


# ---------------------------------------------------------------------------
# Concat: lossless join, correct total duration
# ---------------------------------------------------------------------------

def test_concat_segments_produces_correct_total_duration(tmp_path):
    from video_agent.stages.render import probe_video_duration_sec

    seg1 = tmp_path / "seg_0001.mp4"
    seg2 = tmp_path / "seg_0002.mp4"
    seg3 = tmp_path / "seg_0003.mp4"
    _make_clip(seg1, seconds=1.0)
    _make_clip(seg2, seconds=1.0)
    _make_clip(seg3, seconds=0.5)

    output = tmp_path / "video.mp4"
    _concat_segments([seg1, seg2, seg3], output)

    assert output.exists()
    duration = probe_video_duration_sec(output)
    assert duration is not None
    assert abs(duration - 2.5) < 0.15
    # Concat list file is scratch, must not leak into outputs/.
    assert not output.with_name("video.concat.txt").exists()


def test_concat_segments_raises_on_ffmpeg_failure(tmp_path):
    bad = tmp_path / "not_a_video.mp4"
    bad.write_text("not a real video file", encoding="utf-8")
    with pytest.raises(RemotionSubprocessError):
        _concat_segments([bad], tmp_path / "out.mp4")


# ---------------------------------------------------------------------------
# Cache cleanup (user request: free disk right after each segment)
# ---------------------------------------------------------------------------

def test_clear_render_tmp_cache_empties_directory_without_deleting_it(monkeypatch, tmp_path):
    fake_tmp = tmp_path / ".render_tmp"
    fake_tmp.mkdir()
    (fake_tmp / "bundle-abc").mkdir()
    (fake_tmp / "bundle-abc" / "asset.js").write_text("x", encoding="utf-8")
    (fake_tmp / "loose_file.tmp").write_text("y", encoding="utf-8")

    import video_agent.stages.render as render_mod
    monkeypatch.setattr(render_mod, "_render_tmp_dir", lambda: fake_tmp)

    _clear_render_tmp_cache()

    assert fake_tmp.exists()
    assert list(fake_tmp.iterdir()) == []
