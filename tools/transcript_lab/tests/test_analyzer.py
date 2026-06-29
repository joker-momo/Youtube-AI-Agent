"""Analyzer tests on a synthetic ffmpeg clip — no network, no real download.

Run with the MAIN project venv (has cv2/numpy/soundfile):
    .venv/bin/python -m pytest tools/transcript_lab/tests/test_analyzer.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("cv2")
pytest.importorskip("numpy")
pytest.importorskip("soundfile")

import analyzer  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")


def _make_clip(job_dir, seconds=6, hz=200):
    job_dir.mkdir(parents=True, exist_ok=True)
    video = job_dir / "video.mp4"
    audio = job_dir / "audio.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         f"testsrc=duration={seconds}:size=320x240:rate=10",
         "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={seconds}",
         "-shortest", "-pix_fmt", "yuv420p", str(video)],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio)],
        capture_output=True, check=True,
    )
    return video, audio


@pytest.fixture()
def job_dir(tmp_path):
    d = tmp_path / "testvid"
    _make_clip(d)
    return d


def test_analyze_writes_schema(job_dir):
    out = analyzer.analyze(job_dir)
    assert (job_dir / "analysis.json").exists()
    for key in ("format", "composition", "color", "audio", "voice", "keyframes"):
        assert key in out


def test_format_duration_and_orientation(job_dir):
    out = analyzer.analyze(job_dir)
    assert 5.0 <= out["format"]["duration_sec"] <= 7.0
    assert out["format"]["orientation"] == "landscape"
    assert out["format"]["width"] == 320


def test_keyframes_extracted(job_dir):
    out = analyzer.analyze(job_dir)
    assert len(out["keyframes"]) == analyzer.KEYFRAME_COUNT
    assert (job_dir / "frames" / "00.jpg").exists()


def test_voice_f0_detects_sine_pitch(job_dir):
    # 200 Hz sine -> autocorrelation F0 should land near 200.
    out = analyzer.analyze(job_dir)
    f0 = out["voice"]["f0_median_hz"]
    assert f0 is not None
    assert 180 <= f0 <= 220


def test_audio_loudness_present(job_dir):
    out = analyzer.analyze(job_dir)
    assert out["audio"].get("integrated_lufs") is not None
    assert "silence_ratio" in out["audio"]


def test_composition_has_shot_stats(job_dir):
    out = analyzer.analyze(job_dir)
    assert out["composition"]["shot_count"] >= 1
    assert out["composition"]["motion_label"] in {
        "very_static", "calm", "moderate", "dynamic"}
