from __future__ import annotations

import json

import pytest

from video_agent.shorts import audio


def test_video_covered_marker_never_skips_ai_for_graphic_scenes(monkeypatch, tmp_path):
    monkeypatch.setattr(audio, "_scenes_with_native_video_route", lambda short_dir: {"s01", "s02"})
    scenes = {
        "scenes": [
            {"id": "s01", "layout": "graphic_checklist"},
            {"id": "s02", "layout": "short_tip"},
        ]
    }

    audio._mark_video_covered_scenes(tmp_path, scenes)

    assert scenes["scenes"][0]["_skip_ai_fallback"] is False
    assert scenes["scenes"][1]["_skip_ai_fallback"] is True


def test_video_covered_marker_uses_native_video_route_not_stale_provisional_id(tmp_path):
    short_dir = tmp_path / "short-01"
    json_dir = short_dir / "json"
    json_dir.mkdir(parents=True)
    (json_dir / "visual_spans.json").write_text(
        json.dumps(
            {
                "spans": [
                    {"id": "vs01", "scene_ids": ["s01"]},
                    {"id": "vs02", "scene_ids": ["s02"]},
                ]
            }
        )
    )
    (json_dir / "visual_span_asset_selection.json").write_text(
        json.dumps(
            {
                "spans": [
                    {
                        "visual_span_id": "vs01",
                        "visual_route": "generated_graphic",
                        "provisional_candidate_id": "stale-pexels",
                    },
                    {
                        "visual_span_id": "vs02",
                        "visual_route": "native_video_candidate",
                        "provisional_candidate_id": "pexels-ok",
                    },
                ]
            }
        )
    )
    scenes = {
        "scenes": [
            {"id": "s01", "layout": "short_tip"},
            {"id": "s02", "layout": "short_tip"},
        ]
    }

    audio._mark_video_covered_scenes(short_dir, scenes)

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


def test_background_stage_defers_graphic_scene_without_raising(monkeypatch, tmp_path):
    """Step 5: the background pass now DEFERS graphic gen — it must not raise on
    an unconverted graphic scene; the fail-closed guard moved to the post-QA
    unified pass (_stage_fallback_image_gen)."""
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

    audio.synthesize_short_backgrounds(short_dir, scenes, {})  # no raise

    persisted = json.loads((short_dir / "json" / "short_scenes.json").read_text())
    assert persisted["scenes"][0]["layout"] == "graphic_checklist"  # deferred, not converted


def test_background_stage_does_not_assert_graphic_conversion(monkeypatch, tmp_path):
    """Step 5: _stage_background must NOT fail on a deferred (still graphic_*)
    scene — the conversion guard moved to the post-QA unified pass."""
    from types import SimpleNamespace

    from video_agent.shorts.builder.stages import media

    monkeypatch.setattr(media, "assert_latest_scenes_ready", lambda state: None)
    scenes = {"scenes": [{"id": "s01", "layout": "graphic_checklist"}]}

    def fake_bg(sd, short_scenes, cfg, on_scene_resolved=None):
        pass  # leaves the graphic_* scene unconverted (deferred to post-QA)

    ctx = SimpleNamespace(
        short_plan={"short_id": "short-01"},
        short_dir=tmp_path,
        json_dir=tmp_path,
        long_job_dir=tmp_path,
        extras={"short_scenes": scenes, "scene_pipeline_state": object()},
        channel_config={},
        status={},
        background_fn=fake_bg,
        update_stage=lambda *a, **k: None,
        check_stop=lambda: None,
    )
    media._stage_background(ctx)  # must not raise
    assert scenes["scenes"][0]["layout"] == "graphic_checklist"


def test_post_qa_pass_fails_closed_on_unconverted_graphic_scene(monkeypatch, tmp_path):
    """If the unified post-QA gen does not convert a graphic scene, the moved
    fail-closed guard must raise rather than let graphic_* reach render."""
    from types import SimpleNamespace

    from video_agent.shorts.builder.stages import media

    # regen that fails to convert (leaves the graphic_* layout intact).
    monkeypatch.setattr(
        "video_agent.shorts.audio.regen_fallback_backgrounds",
        lambda short_dir, short_scenes, channel_config, rejected_scene_ids: None,
    )
    scenes = {"scenes": [{"id": "s1", "layout": "graphic_checklist"}]}
    ctx = SimpleNamespace(
        extras={"visual_span_asset_qa": {"spans": []}, "short_scenes": scenes},
        short_dir=tmp_path,
        json_dir=tmp_path,
        channel_config={},
        update_stage=lambda *a, **k: None,
    )
    with pytest.raises(RuntimeError, match=r"s1.*graphic_checklist"):
        media._stage_fallback_image_gen(ctx)
