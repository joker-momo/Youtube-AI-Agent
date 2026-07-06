from __future__ import annotations

import subprocess
from pathlib import Path

from video_agent.shorts.visual_local_analysis import (
    LocalVisualAnalyzer,
    TrimWindowConfig,
    select_trim_window,
)


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _weak_start_video(path: Path) -> Path:
    _run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:r=30:d=1.2",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=30:d=3.0",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p",
            str(path),
        ]
    )
    return path


def _solid_video(path: Path, *, color: str = "blue", seconds: float = 3.0) -> Path:
    _run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=320x180:r=30:d={seconds}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    return path


def test_analyzer_selects_nonzero_trim_after_weak_black_start(tmp_path: Path) -> None:
    video = _weak_start_video(tmp_path / "weak-start.mp4")

    analysis = LocalVisualAnalyzer(stride_sec=0.4).analyze(video, required_frames=60, fps=30)
    trim = select_trim_window(
        analysis,
        required_frames=60,
        config=TrimWindowConfig(stride_sec=0.4, max_windows=12, reject_black_ratio=0.05),
    )

    assert analysis["decode"]["verdict"] == "PASS"
    assert analysis["actual_duration_in_frames"] >= 120
    assert analysis["black_frame_ratio"] > 0.0
    assert trim["status"] == "selected"
    assert trim["selected_window_start_in_frames"] > 0
    assert "no fade/black" in trim["selection_reasons"]


def test_black_windows_are_rejected(tmp_path: Path) -> None:
    video = _solid_video(tmp_path / "black.mp4", color="black", seconds=3.0)

    analysis = LocalVisualAnalyzer(stride_sec=0.5).analyze(video, required_frames=45, fps=30)
    trim = select_trim_window(
        analysis,
        required_frames=45,
        config=TrimWindowConfig(stride_sec=0.5, max_windows=8, reject_black_ratio=0.05),
    )

    assert trim["status"] == "no_valid_window"
    assert "black_or_fade" in trim["rejection_reasons"]


def test_motion_band_and_sharpness_are_recorded(tmp_path: Path) -> None:
    video = _solid_video(tmp_path / "static-blue.mp4", color="blue", seconds=3.0)

    analysis = LocalVisualAnalyzer(stride_sec=0.5).analyze(video, required_frames=45, fps=30)

    assert analysis["motion_band"] == "near_static"
    assert analysis["technical_quality"]["sharpness_score"] >= 0.0
    assert analysis["crop_feasibility"]["full_window_feasible"] is True


# bug-476: a container duration that overhangs its last real frame must NOT
# produce a spurious decode failure. Seeks into the ~1-frame tail zone fail even
# on valid clips; that tail failure is a clean EOF, not corruption. A failure
# well before the tail is still a real decode error.
import numpy as _np  # noqa: E402
import video_agent.shorts.visual_local_analysis as _vla  # noqa: E402


def _patch_probe(monkeypatch, *, duration_sec: float, source_fps: float = 23.976) -> None:
    monkeypatch.setattr(
        _vla, "_probe_video",
        lambda path: {"duration_sec": duration_sec, "source_fps": source_fps,
                      "width": 1080, "height": 1920},
    )


def test_decode_passes_when_only_eof_tail_frame_is_unextractable(monkeypatch, tmp_path) -> None:
    # Container reports 10.01s but frames end ~9.968s (24fps). Any seek past the
    # last real frame fails; that must not fail the whole clip.
    duration = 10.01
    last_real_frame_ts = 9.968
    _patch_probe(monkeypatch, duration_sec=duration)
    monkeypatch.setattr(
        _vla, "_extract_frame",
        lambda media, ts, out: (out.write_bytes(b"x") or True) if ts <= last_real_frame_ts else False,
    )
    monkeypatch.setattr(
        _vla, "_frame_metrics",
        lambda out, frame_no, ts: {"frame_no": frame_no, "ts": ts, "sharpness_score": 80.0,
                                   "black_or_fade": False, "_luma": _np.zeros((4, 4), dtype=_np.uint8)},
    )
    res = _vla.LocalVisualAnalyzer(stride_sec=0.5).analyze(tmp_path / "v.mp4", required_frames=135, fps=30)
    assert res["decode"]["verdict"] == "PASS", res["decode"]
    assert res["decode"]["errors"] == []
    assert len(res["sampled_frames"]) >= 20


def test_decode_still_fails_on_real_midstream_gap(monkeypatch, tmp_path) -> None:
    # A frame that fails to extract well before the EOF tail is genuine corruption.
    duration = 10.0
    _patch_probe(monkeypatch, duration_sec=duration, source_fps=30.0)
    monkeypatch.setattr(
        _vla, "_extract_frame",
        lambda media, ts, out: False if 4.9 <= ts <= 5.1 else (out.write_bytes(b"x") or True),
    )
    monkeypatch.setattr(
        _vla, "_frame_metrics",
        lambda out, frame_no, ts: {"frame_no": frame_no, "ts": ts, "sharpness_score": 80.0,
                                   "black_or_fade": False, "_luma": _np.zeros((4, 4), dtype=_np.uint8)},
    )
    res = _vla.LocalVisualAnalyzer(stride_sec=0.5).analyze(tmp_path / "v.mp4", required_frames=135, fps=30)
    assert res["decode"]["verdict"] == "FAIL"
    assert any("decode_failed_at" in e for e in res["decode"]["errors"])
