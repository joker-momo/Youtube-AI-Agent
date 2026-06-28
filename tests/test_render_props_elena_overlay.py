"""Elena overlay injection is decoupled from background span-planning mode.

Elena is a brand-support overlay, so ``_attach_elena_cues`` mounts it on an
opt-in ``visual.elena.enabled`` flag regardless of whether span planning is
``report_only`` / ``disabled`` / ``enforced``. It is default-off (existing
channels unchanged), never double-injects over the enforced schedule path, and
recompiles from the final post-TTS scene durations carried in render_props.
"""

from __future__ import annotations

import json

from video_agent.pipeline import _attach_elena_cues

_ELENA_ENABLED = {"visual": {"elena": {"enabled": True}}, "render": {"fps": 30}}


def _write_elena_sidecar(job_dir, total_frames=10, cues=None):
    (job_dir / "json").mkdir(parents=True, exist_ok=True)
    (job_dir / "json" / "elena_cues.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "fps": 30,
                "total_frames": total_frames,
                "cues": cues or [],
            }
        )
    )


def test_overlay_injected_when_enabled_even_in_report_only(tmp_path):
    _write_elena_sidecar(tmp_path)
    rp: dict = {}
    _attach_elena_cues(rp, tmp_path, _ELENA_ENABLED)
    assert rp["elena_cues"]["schema_version"] == 1


def test_overlay_not_injected_by_default(tmp_path):
    _write_elena_sidecar(tmp_path)
    rp: dict = {}
    _attach_elena_cues(rp, tmp_path, {"render": {"fps": 30}})
    assert "elena_cues" not in rp


def test_overlay_not_injected_when_no_sidecar(tmp_path):
    rp: dict = {}
    _attach_elena_cues(rp, tmp_path, _ELENA_ENABLED)
    assert "elena_cues" not in rp


def test_overlay_does_not_clobber_enforced_cues(tmp_path):
    _write_elena_sidecar(tmp_path)
    sentinel = {"schema_version": 1, "fps": 30, "total_frames": 99, "cues": []}
    rp: dict = {"elena_cues": sentinel}
    _attach_elena_cues(rp, tmp_path, _ELENA_ENABLED)
    assert rp["elena_cues"] is sentinel  # enforced path wins, no double-inject


def test_overlay_recompiles_from_final_durations(tmp_path):
    # Stale sidecar total = 10 frames; render_props carries the FINAL scenes.
    _write_elena_sidecar(tmp_path, total_frames=10)
    rp: dict = {
        "scenes": [
            {"id": "s1", "layout": "hook", "duration_sec": 12.0},
            {"id": "s2", "layout": "subtitle", "duration_sec": 12.0},
        ]
    }
    _attach_elena_cues(rp, tmp_path, _ELENA_ENABLED)
    # Recompiled against 24s of scenes at 30fps -> 720 frames, not the stale 10.
    assert rp["elena_cues"]["total_frames"] == 720
