"""Regression guard for _sample_luma's png->frame mapping (bug-386 follow-up).

_sample_luma maps the i-th extracted png to wanted[i] POSITIONALLY. That is only
valid when ffmpeg emits exactly one frame per requested index. If it emits fewer
(a dropped/duplicated frame on VFR input, or an out-of-range request), the
positional mapping shifts and every later boundary is read off the wrong frame ->
a silent false continuity verdict. The function must fail safe (return None ->
QA skips) on a count mismatch, and map each png to its own frame on a full count.

ffmpeg is stubbed so the test is deterministic and needs no real video.
"""

from __future__ import annotations

from PIL import Image

from video_agent.orchestrator.stages import render_continuity_qa as rcq


def _stub_ffmpeg(monkeypatch, tmp_dir, lumas: list[int]):
    """Make _sample_luma's ffmpeg call write ``len(lumas)`` solid-gray pngs."""
    monkeypatch.setattr(rcq.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def _fake_run(cmd, **kwargs):
        tmp_dir.mkdir(parents=True, exist_ok=True)
        for i, val in enumerate(lumas):
            Image.new("L", (4, 4), color=val).save(tmp_dir / f"{i + 1:05d}.png")

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr(rcq.subprocess, "run", _fake_run)


def test_sample_luma_skips_on_png_count_mismatch(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    tmp_dir = video.parent / "_continuity_tmp"
    # Request 3 frames but ffmpeg yields only 2 -> positional map is unreliable.
    _stub_ffmpeg(monkeypatch, tmp_dir, lumas=[0, 255])

    assert rcq._sample_luma(video, [10, 20, 30], total=40) is None


def test_sample_luma_maps_each_png_to_its_own_frame(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    tmp_dir = video.parent / "_continuity_tmp"
    # Full count: first wanted frame black, second white.
    _stub_ffmpeg(monkeypatch, tmp_dir, lumas=[0, 255])

    luma = rcq._sample_luma(video, [10, 20], total=40)

    assert luma is not None
    assert luma[10] == 0.0      # frame 10 -> first png (black)
    assert luma[20] == 255.0    # frame 20 -> second png (white)
    assert luma[15] == 128.0    # unsampled index keeps the non-black sentinel
