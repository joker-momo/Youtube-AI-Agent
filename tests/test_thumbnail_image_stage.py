import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from video_agent.orchestrator.stages import _build_thumbnail_prompt, auto_thumbnail_image_stage
from video_agent.orchestrator.stages import StageInputMissingError
from video_agent.orchestrator.job_state import DEFAULT_STAGES


# ── _build_thumbnail_prompt ───────────────────────────────────────────────────

def test_build_thumbnail_prompt_contains_title():
    p = _build_thumbnail_prompt("5 secretos para dormir mejor", "DUERME MEJOR HOY", "#F2C94C", "Bienestar 45+")
    assert "5 secretos para dormir mejor" in p


def test_build_thumbnail_prompt_contains_thumbnail_text():
    p = _build_thumbnail_prompt("Test title", "INSOMNIO SECRETO", "#F2C94C", "Wellness")
    assert "INSOMNIO SECRETO" in p


def test_build_thumbnail_prompt_no_text_instruction():
    p = _build_thumbnail_prompt("t", "HOOK", "#fff", "desc")
    assert "no text" in p.lower() or "no watermark" in p.lower()


def test_build_thumbnail_prompt_16x9():
    p = _build_thumbnail_prompt("t", "HOOK", "#fff", "desc")
    assert "16:9" in p


def test_build_thumbnail_prompt_accent_color():
    p = _build_thumbnail_prompt("t", "HOOK", "#F2C94C", "desc")
    assert "#F2C94C" in p


# ── helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def channel_path(tmp_path):
    cfg = {
        "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+", "description": "Bienestar adultos 45+"},
        "style": {"palette": {"accent": "#F2C94C", "primary": "#2F6B57", "secondary": "#D98C5F",
                               "background": "#F6F1E8", "text": "#26332F"}},
        "render": {"fps": 30, "resolution": "1920x1080", "duration_sec": 54},
        "visuals": {"strategy": "pexels_video"},
        "tts": {"provider": "mock-local"},
    }
    p = tmp_path / "channel.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return p


def _seed_at_thumbnail_image(job_dir: Path) -> None:
    """Seed a job with current_stage='thumbnail_image'. All prior stages completed."""
    from video_agent.orchestrator.job_state import JobState, StageStatus, save_job
    from video_agent.orchestrator.orchestrator import _now

    ts = _now()
    stage_name = "thumbnail_image"
    # Build stage list from DEFAULT_STAGES; inject thumbnail_image if not present
    all_stages = list(DEFAULT_STAGES)
    if stage_name not in all_stages:
        idx = all_stages.index("whisper_timestamps")
        all_stages.insert(idx, stage_name)

    stages = []
    found = False
    for name in all_stages:
        if name == stage_name:
            stages.append(StageStatus(name=name, status="pending"))
            found = True
        elif not found:
            stages.append(StageStatus(name=name, status="completed", started_at=ts, completed_at=ts))
        else:
            stages.append(StageStatus(name=name, status="pending"))

    state = JobState(
        job_id=job_dir.name,
        channel_id="vida-plena-45",
        idea_path="idea.json",
        created_at=ts,
        updated_at=ts,
        current_stage=stage_name,
        stages=stages,
    )
    job_dir.mkdir(parents=True, exist_ok=True)
    save_job(job_dir, state)
    seo = {
        "job_id": job_dir.name, "title": "5 secretos para dormir bien",
        "thumbnail_text": "DUERME MEJOR HOY", "description": "Desc",
        "tags": ["sueño"], "language": "es-419", "ai_disclosure": True,
        "thumbnail_path": "",
        "title_variants": [{"title": "5 secretos", "thumbnail_text": "DUERME MEJOR HOY", "score": 85}],
    }
    (job_dir / "seo.json").write_text(json.dumps(seo), encoding="utf-8")
    (job_dir / "assets").mkdir(parents=True, exist_ok=True)


# ── auto_thumbnail_image_stage ────────────────────────────────────────────────

def test_auto_thumbnail_image_stage_calls_image_fn(tmp_path, channel_path):
    job_dir = tmp_path / "job-thumb"
    _seed_at_thumbnail_image(job_dir)

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        Path(out_path).write_bytes(b"\x89PNG fake")
        return {"src": "https://example.com/img.png", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        result = asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn))

    assert result == job_dir / "seo.json"
    seo = json.loads((job_dir / "seo.json").read_text())
    assert "thumbnail_bg.png" in seo["thumbnail_path"]
    assert seo["thumbnail_path"].startswith("jobs/")

    state = json.loads((job_dir / "job.json").read_text())
    assert state["current_stage"] != "thumbnail_image"


def test_auto_thumbnail_image_stage_uses_variant_text(tmp_path, channel_path):
    job_dir = tmp_path / "job-thumb2"
    _seed_at_thumbnail_image(job_dir)
    seo = json.loads((job_dir / "seo.json").read_text())
    seo["title_variants"] = [{"title": "Best", "thumbnail_text": "INSOMNIO SECRETO", "score": 90}]
    seo["thumbnail_text"] = "DUERME MEJOR HOY"
    (job_dir / "seo.json").write_text(json.dumps(seo))

    captured = []

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        captured.append(prompt)
        Path(out_path).write_bytes(b"\x89PNG fake")
        return {"src": "x", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn))

    assert any("INSOMNIO SECRETO" in p for p in captured)


def test_auto_thumbnail_image_stage_wrong_stage_raises(tmp_path, channel_path):
    job_dir = tmp_path / "job-wrong"
    _seed_at_thumbnail_image(job_dir)
    state = json.loads((job_dir / "job.json").read_text())
    state["current_stage"] = "render"
    (job_dir / "job.json").write_text(json.dumps(state))

    async def fake_image_fn(**kwargs):
        return {}

    with pytest.raises(StageInputMissingError, match="thumbnail_image"):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn))
