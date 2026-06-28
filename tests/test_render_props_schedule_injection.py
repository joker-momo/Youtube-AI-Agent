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


def test_elena_cues_injected_when_enforced(tmp_path):
    (tmp_path / "json").mkdir(parents=True)
    (tmp_path / "json" / "elena_cues.json").write_text(
        json.dumps({"schema_version": 1, "fps": 30, "total_frames": 10, "cues": []})
    )
    rp: dict = {}
    _attach_enforced_visual_schedule(
        rp, tmp_path, {"visual": {"span_planning": {"mode": "enforced"}}}
    )
    assert rp["elena_cues"]["schema_version"] == 1


def test_elena_cues_not_injected_in_report_only(tmp_path):
    (tmp_path / "json").mkdir(parents=True)
    (tmp_path / "json" / "elena_cues.json").write_text(
        json.dumps({"schema_version": 1, "fps": 30, "total_frames": 10, "cues": []})
    )
    rp: dict = {}
    _attach_enforced_visual_schedule(
        rp, tmp_path, {"visual": {"span_planning": {"mode": "report_only"}}}
    )
    assert "elena_cues" not in rp


# ── C1: recompile from FINAL post-TTS durations, not the stale on-disk file ────
def _write_spans(job_dir):
    (job_dir / "json").mkdir(parents=True, exist_ok=True)
    (job_dir / "json" / "visual_spans.json").write_text(json.dumps({
        "spans": [{"id": "vs01", "scene_ids": ["s1", "s2"],
                   "planned_mode": "continuous_clip"}]
    }))


def _final_scenes():
    # Durations differ from the stale file's total (10 frames). At 30fps the
    # recompiled schedule must total round(2*30)+round(3*30) = 150 frames.
    return [
        {"id": "s1", "duration_sec": 2.0, "asset_refs": {"background": "a.mp4"}},
        {"id": "s2", "duration_sec": 3.0, "asset_refs": {"background": "a.mp4"}},
    ]


def test_schedule_recompiled_from_final_durations(tmp_path):
    _write_schedule(tmp_path)  # stale file: total_duration_in_frames=10
    _write_spans(tmp_path)
    rp: dict = {"scenes": _final_scenes()}
    _attach_enforced_visual_schedule(
        rp, tmp_path,
        {"visual": {"span_planning": {"mode": "enforced"}}, "render": {"fps": 30}},
    )
    assert rp["visual_schedule"]["total_duration_in_frames"] == 150


def test_elena_recompiled_from_final_durations(tmp_path):
    (tmp_path / "json").mkdir(parents=True, exist_ok=True)
    (tmp_path / "json" / "elena_cues.json").write_text(
        json.dumps({"schema_version": 1, "fps": 30, "total_frames": 10, "cues": []})
    )
    rp: dict = {"scenes": _final_scenes()}
    _attach_enforced_visual_schedule(
        rp, tmp_path,
        {"visual": {"span_planning": {"mode": "enforced"}}, "render": {"fps": 30}},
    )
    assert rp["elena_cues"]["total_frames"] == 150


def test_falls_back_to_stale_file_when_no_scenes(tmp_path):
    # Back-compat: callers that pass no scenes still get the on-disk artifact.
    _write_schedule(tmp_path)
    rp: dict = {}
    _attach_enforced_visual_schedule(
        rp, tmp_path, {"visual": {"span_planning": {"mode": "enforced"}}}
    )
    assert rp["visual_schedule"]["total_duration_in_frames"] == 10
