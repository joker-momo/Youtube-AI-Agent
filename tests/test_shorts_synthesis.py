from __future__ import annotations

import json
from pathlib import Path


def _cfg() -> dict:
    return {
        "channel": {"id": "vida-plena-45"},
        "shorts": {
            "autopilot": {"max_regeneration_attempts": 1},
            "cover": {"text_max_words": 5},
            "duration": {"min_sec": 20, "target_max_sec": 45},
            "tts": {"provider": "kokoro", "voice_id": "ef_dora", "speed": 1.07},
            "funnel": {"default_cta_without_url": "Vídeo completo en el canal.", "cta_max_words": 8},
        },
    }


def _make_job(tmp_path: Path) -> Path:
    job = tmp_path / "job-1"
    (job / "json").mkdir(parents=True, exist_ok=True)
    (job / "job.json").write_text(json.dumps({"job_id": "job-1", "channel_id": "vida-plena-45"}), encoding="utf-8")
    (job / "json" / "script.json").write_text(json.dumps({"narration": "full long narration"}), encoding="utf-8")
    (job / "json" / "seo.json").write_text(json.dumps({"title": "Dormir mejor"}), encoding="utf-8")
    (job / "outputs").mkdir(parents=True, exist_ok=True)
    (job / "outputs" / "video.mp4").write_bytes(b"x")
    scenes = {
        "scenes": [
            {
                "id": "scene-01",
                "audio_offset_sec": 0.0,
                "duration_sec": 10.0,
                "narration": "Primera idea con contexto suficiente.",
                "visual_prompt": "woman stretching",
                "layout": "hook",
            },
            {
                "id": "scene-02",
                "audio_offset_sec": 10.0,
                "duration_sec": 12.0,
                "narration": "",
                "visual_prompt": "empty",
                "layout": "tip",
            },
            {
                "id": "scene-03",
                "audio_offset_sec": 22.0,
                "duration_sec": 11.0,
                "narration": "Tercera idea útil y separada.",
                "visual_prompt": "kitchen night",
                "layout": "tip",
            },
        ]
    }
    (job / "json" / "scenes.json").write_text(json.dumps(scenes), encoding="utf-8")
    return job


def test_synthesis_paths_exist(tmp_path: Path):
    from video_agent.shorts import paths

    job = tmp_path / "job-1"

    assert paths.short_ideas_path(job) == job / "shorts" / "short_ideas.json"
    assert paths.selected_short_ideas_path(job) == job / "shorts" / "selected_short_ideas.json"
    assert paths.idea_generation_run_path(job) == job / "shorts" / "idea_generation_run.json"
    assert paths.studio_render_run_path(job) == job / "shorts" / "studio_render_run.json"
    assert paths.idea_generation_lock_path(job) == job / "shorts" / ".ideas.lock"
    assert paths.render_selected_lock_path(job) == job / "shorts" / ".render-selected.lock"


def test_build_long_narration_source_preserves_ids_and_skips_empty(tmp_path: Path):
    from video_agent.shorts.idea_generator import build_long_narration_source

    job = _make_job(tmp_path)

    source = build_long_narration_source(job)

    assert [scene["scene_id"] for scene in source["scenes"]] == ["scene-01", "scene-03"]
    assert source["scenes"][0]["index"] == 1
    assert source["scenes"][1]["index"] == 3
    assert source["truncated"] is False
    assert "SCENE scene-01" in source["full_narration"]
    assert "SCENE scene-03" in source["full_narration"]


def test_build_long_narration_source_truncates_on_scene_boundaries(tmp_path: Path):
    from video_agent.shorts.idea_generator import build_long_narration_source

    job = _make_job(tmp_path)

    source = build_long_narration_source(job, max_chars=70)

    assert source["truncated"] is True
    assert "SCENE scene-01" in source["full_narration"]
    assert "scene-03" not in source["full_narration"]


def test_short_ideas_prompt_includes_scene_blocks_and_excerpt_guardrails(tmp_path: Path):
    from video_agent.shorts.idea_generator import build_long_narration_source
    from video_agent.shorts.idea_prompts import short_ideas_prompt

    job = _make_job(tmp_path)
    source = build_long_narration_source(job)

    prompt = short_ideas_prompt(_cfg(), source, target_count=8)
    low = prompt.lower()

    assert "scene scene-01" in low
    assert "scene scene-03" in low
    assert "do not propose raw excerpts" in low
    assert "source_scene_ids" in prompt


def test_validate_and_score_ideas_normalizes_rejects_and_dedupes():
    from video_agent.shorts.idea_scorer import validate_and_score_ideas

    source_doc = {
        "source_long_job_id": "job-1",
        "title": "Dormir mejor",
        "scenes": [
            {"scene_id": "scene-01", "index": 1},
            {"scene_id": "scene-03", "index": 3},
            {"scene_id": "scene-07", "index": 7},
        ],
    }
    raw = {
        "source_long_job_id": "job-1",
        "source_title": "Dormir mejor",
        "ideas": [
            {
                "idea_id": "foo",
                "idea_type": "synthesis",
                "format": "checklist",
                "title": "3 errores al dormir",
                "hook_text": "3 errores al dormir",
                "viewer_pain": "p",
                "practical_payoff": "q",
                "source_scene_ids": ["scene-01", "scene-03", "scene-03"],
                "key_points": [{"point": "a", "source_scene_ids": ["scene-01"]}],
                "narration_seed": "seed",
                "visual_angle": "contrast",
                "risk_level": "lifestyle",
                "scores": {"hook_strength": 120, "viewer_pain": 80, "practical_value": 75, "source_fidelity": 91, "visual_potential": 60, "safety": 95, "uniqueness": 50},
            },
            {
                "idea_id": "bar",
                "idea_type": "excerpt",
                "format": "recap",
                "title": "bad",
                "hook_text": "bad hook",
                "source_scene_ids": ["scene-01"],
            },
            {
                "idea_id": "baz",
                "idea_type": "synthesis",
                "format": "checklist",
                "title": "3 errores al dormir",
                "hook_text": "3 errores al dormir",
                "viewer_pain": "p",
                "practical_payoff": "q",
                "source_scene_ids": ["scene-01", "scene-03"],
                "key_points": [{"point": "a", "source_scene_ids": ["scene-01"]}],
                "narration_seed": "seed",
                "visual_angle": "contrast",
                "risk_level": "lifestyle",
                "scores": {"hook_strength": 90, "viewer_pain": 70, "practical_value": 70, "source_fidelity": 70, "visual_potential": 60, "safety": 90, "uniqueness": 50},
            },
        ],
    }

    out = validate_and_score_ideas(raw, source_doc, target_count=10)

    assert [idea["idea_id"] for idea in out["ideas"]] == ["idea-01"]
    assert out["ideas"][0]["source_scene_ids"] == ["scene-01", "scene-03"]
    assert 0 <= out["ideas"][0]["scores"]["overall"] <= 100
    assert {item["reason"] for item in out["diagnostics"]["rejected_ideas"]} >= {"not_synthesis", "duplicate"}


def test_render_selected_short_ideas_passes_source_artifacts_and_updates_run(tmp_path: Path):
    from video_agent.shorts import manifest, paths
    from video_agent.shorts.idea_store import write_short_ideas
    from video_agent.shorts.synthesis import render_selected_short_ideas

    job = _make_job(tmp_path)
    write_short_ideas(
        job,
        {
            "schema_version": "short_ideas.v1",
            "source_long_job_id": "job-1",
            "source_title": "Dormir mejor",
            "generation_id": "ideas-1",
            "ideas": [
                {
                    "idea_id": "idea-01",
                    "idea_type": "synthesis",
                    "format": "checklist",
                    "title": "3 errores al dormir",
                    "hook_text": "3 errores",
                    "viewer_pain": "p",
                    "practical_payoff": "q",
                    "source_scene_ids": ["scene-01", "scene-03"],
                    "key_points": [{"point": "a", "source_scene_ids": ["scene-01"]}],
                    "narration_seed": "seed",
                    "visual_angle": "contrast",
                    "risk_level": "lifestyle",
                    "scores": {"overall": 88},
                }
            ],
            "warnings": [],
        },
    )
    captured: dict[str, object] = {}

    def fake_build(long_job_dir, short_plan, channel_config, **kwargs):
        captured["short_plan"] = short_plan
        captured["source_artifacts"] = kwargs.get("source_artifacts")
        captured["require_render_confirmation"] = kwargs.get("require_render_confirmation")
        short_dir = paths.short_dir(long_job_dir, short_plan["short_id"])
        short_dir.mkdir(parents=True, exist_ok=True)
        manifest.write_short_status(
            long_job_dir,
            short_plan["short_id"],
            {
                "short_id": short_plan["short_id"],
                "idea_id": short_plan["idea_id"],
                "status": "rendered",
                "rendered": True,
                "qa_verdict": "PASS",
                "video_path": f"shorts/{short_plan['short_id']}/short.mp4",
                "cover_path": f"shorts/{short_plan['short_id']}/short_cover.jpg",
            },
        )
        return {
            "short_id": short_plan["short_id"],
            "idea_id": short_plan["idea_id"],
            "status": "rendered",
            "rendered": True,
            "qa_verdict": "PASS",
            "video_path": f"shorts/{short_plan['short_id']}/short.mp4",
            "cover_path": f"shorts/{short_plan['short_id']}/short_cover.jpg",
            "source_scene_ids": short_plan["source_scene_ids"],
        }

    result = render_selected_short_ideas(job, _cfg(), ["idea-01"], build_short_fn=fake_build)

    assert result["status"] == "completed"
    assert captured["require_render_confirmation"] is False
    assert captured["source_artifacts"]["idea"]["idea_id"] == "idea-01"
    assert [scene["scene_id"] for scene in captured["source_artifacts"]["source_scenes"]] == ["scene-01", "scene-03"]
    run_doc = json.loads(paths.studio_render_run_path(job).read_text(encoding="utf-8"))
    assert run_doc["rendered_count"] == 1
    assert run_doc["attempted_render_count"] == 1
    manifest_doc = json.loads(paths.manifest_path(job).read_text(encoding="utf-8"))
    assert manifest_doc["mode"] == "synthesis_ideas"
    assert manifest_doc["shorts"][0]["idea_id"] == "idea-01"


def test_render_selected_short_ideas_skips_previously_rendered_idea(tmp_path: Path):
    from video_agent.shorts import manifest, paths
    from video_agent.shorts.idea_store import write_short_ideas
    from video_agent.shorts.synthesis import render_selected_short_ideas

    job = _make_job(tmp_path)
    old_short_dir = paths.short_dir(job, "short-01")
    old_short_dir.mkdir(parents=True, exist_ok=True)
    manifest.write_short_status(job, "short-01", {"short_id": "short-01", "idea_id": "idea-01", "status": "rendered"})
    manifest.write_manifest(
        job,
        {
            "source_long_job_id": "job-1",
            "mode": "synthesis_ideas",
            "status": "completed",
            "shorts": [{"short_id": "short-01", "idea_id": "idea-01", "status": "rendered", "rendered": True}],
        },
    )
    write_short_ideas(
        job,
        {
            "schema_version": "short_ideas.v1",
            "source_long_job_id": "job-1",
            "source_title": "Dormir mejor",
            "generation_id": "ideas-2",
            "ideas": [
                {
                    "idea_id": "idea-01",
                    "idea_type": "synthesis",
                    "format": "checklist",
                    "title": "3 errores al dormir",
                    "hook_text": "3 errores",
                    "viewer_pain": "p",
                    "practical_payoff": "q",
                    "source_scene_ids": ["scene-01", "scene-03"],
                    "key_points": [],
                    "narration_seed": "seed",
                    "visual_angle": "contrast",
                    "risk_level": "lifestyle",
                    "scores": {"overall": 88},
                }
            ],
            "warnings": [],
        },
    )

    result = render_selected_short_ideas(job, _cfg(), ["idea-01"], build_short_fn=lambda *args, **kwargs: None)

    assert result["status"] == "failed"
    run_doc = json.loads(paths.studio_render_run_path(job).read_text(encoding="utf-8"))
    assert run_doc["skipped_count"] == 1
    assert run_doc["attempted_render_count"] == 0
    assert "already_rendered_idea:idea-01" in run_doc["warnings"]
