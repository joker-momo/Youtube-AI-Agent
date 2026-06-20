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
