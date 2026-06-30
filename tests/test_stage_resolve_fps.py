"""Guard for the shared stage fps resolver (overlap #3).

The shared ``_resolve_fps`` is centralized in
``orchestrator.stages._shared.resolve_stage_fps``: reads
``render.fps`` from the channel config, defaults to 30, never raises.
"""

from __future__ import annotations

from video_agent.orchestrator.stages._shared import resolve_stage_fps


def test_reads_fps_from_channel_config(tmp_path):
    cfg = tmp_path / "channel.yaml"
    cfg.write_text("render:\n  fps: 24\n")
    assert resolve_stage_fps(cfg) == 24


def test_defaults_to_30_when_missing(tmp_path):
    cfg = tmp_path / "channel.yaml"
    cfg.write_text("render: {}\n")
    assert resolve_stage_fps(cfg) == 30


def test_defaults_when_path_none_or_absent(tmp_path):
    assert resolve_stage_fps(None) == 30
    assert resolve_stage_fps(tmp_path / "nope.yaml") == 30
