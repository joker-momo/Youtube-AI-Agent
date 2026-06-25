"""The compiled schedule is fed to the renderer ONLY under enforced mode.

This guards the frame-identical safety rule: report_only / disabled (the default)
must never attach ``visual_schedule`` to render_props, so the renderer keeps the
legacy per-scene background.
"""

from __future__ import annotations

import json

from video_agent.pipeline import _attach_enforced_visual_schedule

_SCHEDULE = {"schema_version": 2, "fps": 30, "total_duration_in_frames": 10,
             "scene_boundaries": [], "tracks": []}


def _write_schedule(job_dir):
    (job_dir / "json").mkdir(parents=True)
    (job_dir / "json" / "compiled_asset_schedule.json").write_text(json.dumps(_SCHEDULE))


def test_not_injected_in_report_only(tmp_path):
    _write_schedule(tmp_path)
    rp: dict = {}
    _attach_enforced_visual_schedule(
        rp, tmp_path, {"visual": {"span_planning": {"mode": "report_only"}}}
    )
    assert "visual_schedule" not in rp


def test_not_injected_when_disabled(tmp_path):
    _write_schedule(tmp_path)
    rp: dict = {}
    _attach_enforced_visual_schedule(
        rp, tmp_path, {"visual": {"span_planning": {"mode": "disabled"}}}
    )
    assert "visual_schedule" not in rp


def test_injected_when_enforced(tmp_path):
    _write_schedule(tmp_path)
    rp: dict = {}
    _attach_enforced_visual_schedule(
        rp, tmp_path, {"visual": {"span_planning": {"mode": "enforced"}}}
    )
    assert rp["visual_schedule"]["schema_version"] == 2


def test_enforced_but_no_schedule_file_is_noop(tmp_path):
    rp: dict = {}
    _attach_enforced_visual_schedule(
        rp, tmp_path, {"visual": {"span_planning": {"mode": "enforced"}}}
    )
    assert "visual_schedule" not in rp
