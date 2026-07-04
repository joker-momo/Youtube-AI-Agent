"""Regression guard for _sample_luma's per-frame extraction (bug-386, bug-458).

_sample_luma used to extract all requested frames in ONE ffmpeg call (a
sequential ``select`` scan) and map the i-th output png to wanted[i]
POSITIONALLY. That was fragile (a count mismatch shifted every later boundary
onto the wrong frame -> bug-386) AND slow on long videos with late boundaries,
since ffmpeg had to decode from frame 0 up to the last requested index every
time -> timeout -> silent QA skip (bug-458).

The fixed version extracts each frame independently via its own ffmpeg seek
(fast input-seek, falling back to an accurate output-seek on a miss) and maps
each result by FRAME NUMBER (embedded in the temp filename), not position —
so a mismatch class of bug can no longer happen, and per-frame seeking scales
with the number of boundaries, not the video length. The function must still
fail safe (return None -> QA skips) when any single requested frame cannot be
extracted at all, rather than emit a verdict missing that frame's data.

ffmpeg is stubbed so the test is deterministic and needs no real video.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from video_agent.orchestrator.stages import render_continuity_qa as rcq


def _stub_ffmpeg(monkeypatch, lumas: dict[int, int]):
    """Make every ffmpeg seek attempt succeed for the frames in ``lumas``
    (writing a solid-gray png named after that frame) and fail for any other
    requested frame (no file written, on both the fast and accurate attempt)."""
    monkeypatch.setattr(rcq.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def _fake_run(cmd, **kwargs):
        out_path = Path(cmd[-1])
        frame = int(out_path.stem.split("-", 1)[1])
        if frame in lumas:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("L", (4, 4), color=lumas[frame]).save(out_path)

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr(rcq.subprocess, "run", _fake_run)


def test_sample_luma_returns_none_when_a_frame_fails_extraction(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    # Frame 20 never succeeds on either the fast or accurate seek attempt.
    _stub_ffmpeg(monkeypatch, lumas={10: 0})

    assert rcq._sample_luma(video, [10, 20], total=40, fps=30) is None


def test_sample_luma_maps_each_frame_by_name_not_position(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    _stub_ffmpeg(monkeypatch, lumas={10: 0, 20: 255})

    luma = rcq._sample_luma(video, [10, 20], total=40, fps=30)

    assert luma is not None
    assert luma[10] == 0.0      # frame 10 -> its own named file (black)
    assert luma[20] == 255.0    # frame 20 -> its own named file (white)
    assert luma[15] == 128.0    # unsampled index keeps the non-black sentinel


def test_sample_luma_requires_positive_fps(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    _stub_ffmpeg(monkeypatch, lumas={10: 0})

    assert rcq._sample_luma(video, [10], total=40, fps=0) is None
