from __future__ import annotations

import json

import pytest

from video_agent.shorts import audio


def test_video_covered_marker_never_skips_ai_for_graphic_scenes(monkeypatch, tmp_path):
    monkeypatch.setattr(audio, "_spans_with_provisional_video", lambda short_dir: {"s01", "s02"})
    scenes = {
        "scenes": [
            {"id": "s01", "layout": "graphic_checklist"},
            {"id": "s02", "layout": "short_tip"},
        ]
    }

    audio._mark_video_covered_scenes(tmp_path, scenes)

    assert scenes["scenes"][0]["_skip_ai_fallback"] is False
    assert scenes["scenes"][1]["_skip_ai_fallback"] is True


def test_background_stage_persists_converted_graphic_scene(monkeypatch, tmp_path):
    short_dir = tmp_path / "shorts" / "short-01"
    scenes = {
        "short_id": "short-01",
        "total_duration_sec": 4.0,
        "scenes": [
            {
                "id": "s01",
                "layout": "graphic_checklist",
                "duration_sec": 4.0,
                "asset_refs": {},
            }
        ],
    }

    def fake_prepare_assets(*, scene_doc, **kwargs):
        scene = scene_doc["scenes"][0]
        scene["generated_image_source_layout"] = scene["layout"]
        scene["layout"] = "short_tip"
        scene["background_mode"] = "generated_image"
        scene["asset_refs"]["background"] = "jobs/short-01/assets/s01.mp4"
        return {"scenes": []}

    monkeypatch.setattr("video_agent.stages.assets.prepare_assets", fake_prepare_assets)
    monkeypatch.setattr(audio, "_short_asset_context", lambda short_dir, config: {})
    monkeypatch.setattr(audio, "_mark_video_covered_scenes", lambda short_dir, doc: None)

    audio.synthesize_short_backgrounds(short_dir, scenes, {})

    persisted = json.loads((short_dir / "json" / "short_scenes.json").read_text())
    assert persisted["scenes"][0]["layout"] == "short_tip"
    assert persisted["scenes"][0]["generated_image_source_layout"] == "graphic_checklist"


def test_background_stage_rejects_unconverted_graphic_scene(monkeypatch, tmp_path):
    short_dir = tmp_path / "shorts" / "short-01"
    scenes = {
        "short_id": "short-01",
        "total_duration_sec": 4.0,
        "scenes": [{"id": "s01", "layout": "graphic_checklist", "asset_refs": {}}],
    }

    monkeypatch.setattr(
        "video_agent.stages.assets.prepare_assets",
        lambda **kwargs: {"scenes": []},
    )
    monkeypatch.setattr(audio, "_short_asset_context", lambda short_dir, config: {})
    monkeypatch.setattr(audio, "_mark_video_covered_scenes", lambda short_dir, doc: None)

    with pytest.raises(RuntimeError, match=r"s01.*graphic_checklist"):
        audio.synthesize_short_backgrounds(short_dir, scenes, {})
