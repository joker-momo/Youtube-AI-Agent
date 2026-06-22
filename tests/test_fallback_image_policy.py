"""`_stage_fallback_image_gen` must keep confidently-passing native footage and
only AI-fallback scenes that need it (review P1: it was a blanket AI override of
every hook/short_tip/CTA, ignoring good native -> weak motion, slideshow feel)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from video_agent.shorts.builder.stages import media


def _run(monkeypatch, tmp_path: Path, asset_qa, scenes):
    captured: dict[str, set] = {}

    def _regen(short_dir, short_scenes, channel_config, rejected_scene_ids):
        captured["regen"] = set(rejected_scene_ids)

    monkeypatch.setattr("video_agent.shorts.audio.regen_fallback_backgrounds", _regen)
    ctx = SimpleNamespace(
        extras={"visual_span_asset_qa": asset_qa, "short_scenes": scenes},
        short_dir=tmp_path,
        json_dir=tmp_path,  # no assets_manifest.json present -> re-read branch skipped
        channel_config={},
        update_stage=lambda *a, **k: None,
    )
    media._stage_fallback_image_gen(ctx)
    return captured.get("regen", set())


def test_passing_native_hook_is_kept(monkeypatch, tmp_path):
    asset_qa = {"spans": [
        {"scene_ids": ["s1"], "final_candidate_id": "c1", "render_eligible": True,
         "final_selection_status": "promoted", "qa": {"verdict": "PASS"}},
    ]}
    scenes = {"scenes": [{"id": "s1", "layout": "short_hook"}]}
    regen = _run(monkeypatch, tmp_path, asset_qa, scenes)
    assert "s1" not in regen  # good native hook kept, not overridden


def test_failing_native_tip_gets_fallback(monkeypatch, tmp_path):
    asset_qa = {"spans": [
        {"scene_ids": ["s1"], "final_candidate_id": "c1", "render_eligible": True,
         "final_selection_status": "promoted", "qa": {"verdict": "CAPABILITY_REDUCED"}},
    ]}
    scenes = {"scenes": [{"id": "s1", "layout": "short_tip"}]}
    regen = _run(monkeypatch, tmp_path, asset_qa, scenes)
    assert "s1" in regen  # non-PASS tip -> controlled AI


def test_rejected_span_gets_fallback(monkeypatch, tmp_path):
    asset_qa = {"spans": [
        {"scene_ids": ["s1"], "final_candidate_id": None,
         "final_selection_status": "rejected", "qa": {"verdict": "FAIL"}},
    ]}
    scenes = {"scenes": [{"id": "s1", "layout": "short_checklist"}]}
    regen = _run(monkeypatch, tmp_path, asset_qa, scenes)
    assert "s1" in regen


def test_cta_always_forced_even_with_passing_native(monkeypatch, tmp_path):
    asset_qa = {"spans": [
        {"scene_ids": ["s1"], "final_candidate_id": "c1", "render_eligible": True,
         "final_selection_status": "promoted", "qa": {"verdict": "PASS"}},
    ]}
    scenes = {"scenes": [{"id": "s1", "layout": "short_cta"}]}
    regen = _run(monkeypatch, tmp_path, asset_qa, scenes)
    assert "s1" in regen  # brand end-card policy


def test_passing_native_checklist_kept(monkeypatch, tmp_path):
    asset_qa = {"spans": [
        {"scene_ids": ["s1"], "final_candidate_id": "c1", "render_eligible": True,
         "final_selection_status": "promoted", "qa": {"verdict": "PASS"}},
    ]}
    scenes = {"scenes": [{"id": "s1", "layout": "short_checklist"}]}
    regen = _run(monkeypatch, tmp_path, asset_qa, scenes)
    assert regen == set()  # nothing forced; montage keeps real motion
