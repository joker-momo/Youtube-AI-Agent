from pathlib import Path

from video_agent.pipeline import OperatorRenderOptions, PipelineOptions, render_operator_job, run_pipeline
from video_agent.utils.json_io import read_json, read_yaml
import yaml
import json

ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_writes_structured_artifacts_without_render(tmp_path, monkeypatch):
    import video_agent.operator as operator_mod

    chapter_resync_calls: list[float] = []
    real_resync = operator_mod.resync_seo_chapters

    def tracked_resync(*args, **kwargs):
        chapter_resync_calls.append(float(kwargs.get("content_offset_sec") or 0.0))
        return real_resync(*args, **kwargs)

    monkeypatch.setattr(operator_mod, "resync_seo_chapters", tracked_resync)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    channel_config = read_yaml(ROOT / "configs/vida-plena-45/channel.yaml")
    channel_config["visuals"] = {
        "strategy": "local_directory",
        "source_dir": str(tmp_path / "missing-image-library"),
        "scene_count_target": 5,
    }
    channel_config["tts"] = {"provider": "mock-local", "sample_rate": 24000}
    channel_path = tmp_path / "channel.yaml"
    channel_path.write_text(yaml.safe_dump(channel_config), encoding="utf-8")
    result = run_pipeline(
        PipelineOptions(
            channel_path=channel_path,
            idea_path=ROOT / "inputs/manual_idea.json",
            jobs_dir=tmp_path,
            render=False,
        )
    )
    assert result.video_path is None
    assert (result.job_dir / "json" / "script.json").exists()
    assert (result.job_dir / "json" / "scenes.json").exists()
    assert (result.job_dir / "json" / "assets_manifest.json").exists()
    assert (result.job_dir / "json" / "visual_review.json").exists()
    # visual_contact_sheet.jpg is no longer generated (create_visual_contact_sheet
    # was removed from the render flow by the faceless-render refactor -- see
    # test_shorts_render_handoff.py); visual_review["contact_sheet"] below is
    # still just a path REFERENCE, not a promise the file exists.
    assert (result.job_dir / "json" / "render_props.json").exists()
    assert (result.job_dir / "json" / "seo.json").exists()
    assert (result.job_dir / "outputs" / "thumbnail.jpg").exists()
    assert (result.job_dir / "outputs" / "report.md").exists()
    render_props = read_json(result.job_dir / "json" / "render_props.json")
    expected_offset = (
        float(render_props["branding"]["intro_sec"])
        + float(render_props["branding"]["medical_disclaimer"]["duration_sec"])
    )
    assert chapter_resync_calls == [expected_offset]
    assert render_props["channel"]["id"] == "vida-plena-45"
    assert len(render_props["scenes"]) == 5
    visual_review = read_json(result.job_dir / "json" / "visual_review.json")
    assert visual_review["job_id"] == result.job_id
    assert visual_review["contact_sheet"] == "outputs/visual_contact_sheet.jpg"
    assert len(visual_review["scenes"]) == 5
    assert visual_review["summary"]["total_scenes"] == 5
    assert visual_review["qa"]["status"] == "WARN"
    scene_issue_count = sum(len(scene["qa"]["issues"]) for scene in visual_review["scenes"])
    assert visual_review["qa"]["issue_count"] == scene_issue_count
    assert any(
        issue["type"] == "PLACEHOLDER_USED"
        for scene in visual_review["scenes"]
        for issue in scene["qa"]["issues"]
    )
    report = (result.job_dir / "outputs" / "report.md").read_text(encoding="utf-8")
    assert "Visual Review" in report
    assert "Visual QA: WARN" in report
    assert "Visual contact sheet: outputs/visual_contact_sheet.jpg" in report


def test_operator_render_writes_review_page_without_render(tmp_path, monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    channel_config = read_yaml(ROOT / "configs/vida-plena-45/channel.yaml")
    # Real local image so scene-01 resolves normally instead of falling back
    # to generated_placeholder, which _validate_visual_review hard-blocks
    # (PLACEHOLDER_USED=error) — this test is about the review-page artifact,
    # not visual QA.
    image_library = tmp_path / "image-library"
    image_library.mkdir()
    from PIL import Image

    Image.new("RGB", (64, 64), color=(40, 60, 50)).save(image_library / "scene-01.jpg")
    channel_config["visuals"] = {
        "strategy": "local_directory",
        "source_dir": str(image_library),
        "scene_count_target": 1,
    }
    channel_config["tts"] = {"provider": "mock-local", "sample_rate": 24000}
    channel_path = tmp_path / "channel.yaml"
    channel_path.write_text(yaml.safe_dump(channel_config), encoding="utf-8")

    job_dir = tmp_path / "operator-job"
    qa_dir = job_dir / "operator" / "gemini"
    qa_dir.mkdir(parents=True)
    (job_dir / "script.json").write_text(
        json.dumps(
            {
                "channel_id": "vida-plena-45",
                "job_id": "operator-job",
                "hook": "Dormir mejor empieza con calma.",
                "sections": [{"title": "Calma", "text": "Respira lento antes de dormir."}],
                "narration": "Dormir mejor empieza con calma.",
                "cta": "Prueba esta rutina esta noche.",
                "qa": {"verdict": "PASS"},
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "scenes.json").write_text(
        json.dumps(
            {
                "channel_id": "vida-plena-45",
                "job_id": "operator-job",
                "total_duration_sec": 5,
                "scenes": [
                    {
                        "id": "scene-01",
                        "duration_sec": 5,
                        "narration": "Dormir mejor empieza con calma.",
                        "on_screen_text": "Respira lento",
                        "caption": "Un cierre tranquilo para el dia",
                        "visual_prompt": "calm bedroom night routine",
                        "motion": "slow zoom",
                        "asset_refs": {},
                    }
                ],
                "qa": {"verdict": "PASS"},
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "seo.json").write_text(
        json.dumps(
            {
                "job_id": "operator-job",
                "title": "Dormir mejor despues de los 45",
                "description": "Una rutina simple para descansar mejor.",
                "tags": ["sueño", "bienestar"],
                "language": "es-ES",
                "ai_disclosure": True,
                "thumbnail_path": "thumbnail.jpg",
                "thumbnail_text": "DUERME MEJOR HOY",
                "suggested_pinned_comments": "¿Qué opinas? 👇\n\nSuscríbete: https://www.youtube.com/channel/UCKUswqsAaLsEkcsgzTuKAmw?sub_confirmation=1"
            }
        ),
        encoding="utf-8",
    )
    for artifact in ["script", "scenes", "seo"]:
        (qa_dir / f"{artifact}_qa.json").write_text(
            json.dumps({"artifact": artifact, "verdict": "PASS", "issues": [], "required_changes": [], "scores": {}}),
            encoding="utf-8",
        )

    result = render_operator_job(OperatorRenderOptions(channel_path=channel_path, job_dir=job_dir, render=False))

    review_path = result.job_dir / "outputs" / "operator_review.html"
    if not review_path.exists():
        review_path = result.job_dir / "operator_review.html"
    assert review_path.exists()
    review = review_path.read_text(encoding="utf-8")
    assert "Dormir mejor despues de los 45" in review
    assert "Visual QA" in review
    assert "script_qa.json" in review


def test_operator_render_prepares_kokoro_audio_in_subprocess(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEO_AGENT_AUDIO_SUBPROCESS", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    channel_config = read_yaml(ROOT / "configs/vida-plena-45/channel.yaml")
    # A genuinely empty library makes EVERY scene fall back to
    # generated_placeholder, which _validate_visual_review now hard-blocks
    # (PLACEHOLDER_USED=error — the project's "never ship a blank scene"
    # rule). This test is about the kokoro audio subprocess path, not visual
    # QA, so give scene-01 a real local image to resolve normally.
    image_library = tmp_path / "image-library"
    image_library.mkdir()
    from PIL import Image

    Image.new("RGB", (64, 64), color=(40, 60, 50)).save(image_library / "scene-01.jpg")
    channel_config["visuals"] = {
        "strategy": "local_directory",
        "source_dir": str(image_library),
        "scene_count_target": 1,
    }
    channel_config["tts"] = {
        **(channel_config.get("tts") or {}),
        "provider": "kokoro",
    }
    channel_path = tmp_path / "channel.yaml"
    channel_path.write_text(yaml.safe_dump(channel_config), encoding="utf-8")

    job_dir = tmp_path / "operator-job"
    qa_dir = job_dir / "operator" / "gemini"
    qa_dir.mkdir(parents=True)
    (job_dir / "script.json").write_text(
        json.dumps(
            {
                "channel_id": "vida-plena-45",
                "job_id": "operator-job",
                "hook": "Dormir mejor empieza con calma.",
                "sections": [{"title": "Calma", "text": "Respira lento antes de dormir."}],
                "narration": "Dormir mejor empieza con calma.",
                "cta": "Prueba esta rutina esta noche.",
                "qa": {"verdict": "PASS"},
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "scenes.json").write_text(
        json.dumps(
            {
                "channel_id": "vida-plena-45",
                "job_id": "operator-job",
                "total_duration_sec": 5,
                "scenes": [
                    {
                        "id": "scene-01",
                        "duration_sec": 5,
                        "narration": "Dormir mejor empieza con calma.",
                        "on_screen_text": "Respira lento",
                        "caption": "Un cierre tranquilo para el dia",
                        "visual_prompt": "calm bedroom night routine",
                        "motion": "slow zoom",
                        "asset_refs": {},
                    }
                ],
                "qa": {"verdict": "PASS"},
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "seo.json").write_text(
        json.dumps(
            {
                "job_id": "operator-job",
                "title": "Dormir mejor despues de los 45",
                "description": "Una rutina simple para descansar mejor.",
                "tags": ["sueño", "bienestar"],
                "language": "es-ES",
                "ai_disclosure": True,
                "thumbnail_path": "thumbnail.jpg",
                "thumbnail_text": "DUERME MEJOR HOY",
                "suggested_pinned_comments": "¿Qué opinas? Suscríbete.",
            }
        ),
        encoding="utf-8",
    )
    for artifact in ["script", "scenes", "seo"]:
        (qa_dir / f"{artifact}_qa.json").write_text(
            json.dumps({"artifact": artifact, "verdict": "PASS", "issues": [], "required_changes": [], "scores": {}}),
            encoding="utf-8",
        )

    calls: list[tuple[Path, Path]] = []

    def fake_prepare_audio(job_path: Path, config_path: Path) -> None:
        calls.append((job_path, config_path))
        audio_dir = job_path / "assets"
        audio_dir.mkdir(parents=True)
        (audio_dir / "narration.wav").write_bytes(b"cached-audio")

    monkeypatch.setattr(
        "video_agent.pipeline._run_prepare_assets_audio_subprocess",
        fake_prepare_audio,
        raising=False,
    )

    render_operator_job(
        OperatorRenderOptions(channel_path=channel_path, job_dir=job_dir, render=False)
    )

    assert calls == [(job_dir, channel_path)]
