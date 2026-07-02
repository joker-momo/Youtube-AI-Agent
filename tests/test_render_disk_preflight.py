"""Regression: bug-441 — render must preflight ALL involved volumes.

A mid-encode ENOSPC kills the whole (non-resumable) render, so the check
covers .render_tmp, the OS temp dir, and the output volume."""
from __future__ import annotations

from pathlib import Path

import pytest

from video_agent.stages.render import _assert_render_disk_space


def test_preflight_passes_on_healthy_disk(tmp_path: Path):
    _assert_render_disk_space(min_gb=0.001, output_path=tmp_path / "outputs" / "video.mp4")
    assert (tmp_path / "outputs").exists()


def test_preflight_fails_when_output_volume_below_threshold(tmp_path: Path):
    with pytest.raises(RuntimeError, match="render output|render temp"):
        _assert_render_disk_space(
            min_gb=10_000_000.0,  # absurd requirement: no volume qualifies
            output_path=tmp_path / "outputs" / "video.mp4",
        )


# ---------------------------------------------------------------------------
# Phase-aware progress parsing (Rendered vs Encoded seesaw regression)
# ---------------------------------------------------------------------------

from video_agent.stages.render import _blank_progress, _update_progress_from_line


def test_rendered_and_encoded_counters_tracked_separately():
    p = _blank_progress("preparing")
    assert _update_progress_from_line(p, "Rendered 17433/52140, time remaining: 1h 29m 13s")
    assert p["phase"] == "rendering"
    assert p["rendered_frame"] == 17433
    # Encoder catching up must NOT drag the rendered counter backwards.
    assert _update_progress_from_line(p, "Encoded 885/52140")
    assert p["phase"] == "encoding"
    assert p["rendered_frame"] == 17433
    assert p["encoded_frame"] == 885
    # Completion percent follows the encoder (true output progress).
    assert p["frame"] == 885
    assert p["percent"] == round(885 / 52140 * 100, 1)


def test_generic_number_slash_number_lines_are_ignored():
    p = _blank_progress("preparing")
    assert not _update_progress_from_line(p, "some 12/34 unrelated ratio")
    assert p["rendered_frame"] == 0 and p["encoded_frame"] == 0


def test_bundling_line_sets_phase_only_from_preparing():
    p = _blank_progress("preparing")
    assert _update_progress_from_line(p, "Bundling video app...")
    assert p["phase"] == "bundling"
    p["phase"] = "encoding"
    assert not _update_progress_from_line(p, "bundled in 12s")
    assert p["phase"] == "encoding"
