"""Coverage tests: every long-form ChatGPT image path writes an exact prompt log.

Proves the three long-form image-generation stages — graphic/card
(``run_graphic_images_stage``), generated scene asset (``generate_scene_asset``),
and thumbnail (``auto_thumbnail_image_stage``) — each append the exact prompt
string they send to ChatGPT into the per-job image-prompt audit log.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from video_agent.orchestrator.image_prompt_log import read_image_prompt_index
from video_agent.orchestrator.orchestrator import create_job
from video_agent.orchestrator.stages.assets_thumbnail import (
    auto_thumbnail_image_stage,
    generate_scene_asset,
)
from video_agent.orchestrator.stages.graphic_images import run_graphic_images_stage


def _fake_image_fn(captured: list[str]):
    async def _fn(*, prompt: str, project_name: str, out_path: str, **kwargs):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        from PIL import Image

        Image.new("RGB", (640, 360), (10, 20, 30)).save(out_path, format="PNG")
        captured.append(prompt)
        return {"src": "https://chatgpt/img", "bytes": 9}

    return _fn


@pytest.fixture()
def channel_path(tmp_path):
    cfg = {
        "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+", "description": "Bienestar 45+"},
        "style": {"palette": {"accent": "#F2C94C", "primary": "#2F6B57", "secondary": "#D98C5F",
                              "background": "#F6F1E8", "text": "#26332F"}},
        "render": {"fps": 30, "resolution": "1920x1080", "duration_sec": 54},
        "visuals": {"strategy": "pexels_video"},
        "tts": {"provider": "mock-local"},
    }
    p = tmp_path / "channel.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return p


def test_graphic_stage_logs_exact_prompt(tmp_path):
    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    scenes = [{"id": "scene-02", "layout": "checklist", "graphic": {"needed": True, "prompt": "a checklist"}}]
    (job_dir / "json" / "scenes.json").write_text(json.dumps({"job_id": "j1", "scenes": scenes}))
    create_job(job_dir, "j1", "vida-plena-45", "json/idea.json", stages=["graphic_images"])

    captured: list[str] = []
    asyncio.run(run_graphic_images_stage(job_dir, None, _fake_image_fn(captured)))

    index = read_image_prompt_index(job_dir)
    graphic_recs = [r for r in index if r["kind"] == "graphic"]
    assert len(graphic_recs) == 1
    rec = graphic_recs[0]
    assert rec["stage"] == "graphic_images"
    assert rec["scene_id"] == "scene-02"
    # The logged prompt is byte-exact with what was sent to ChatGPT.
    assert rec["prompt"] == captured[0]
    assert rec["prompt_chars"] == len(captured[0])


def test_scene_asset_logs_exact_prompt(tmp_path, channel_path):
    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    scenes = [{"id": "scene-01", "visual_prompt": "una cocina luminosa con fruta"}]
    (job_dir / "json" / "scenes.json").write_text(json.dumps({"job_id": "j1", "scenes": scenes}))
    create_job(job_dir, "j1", "vida-plena-45", "json/idea.json", stages=["assets_chatgpt"])

    captured: list[str] = []
    asyncio.run(generate_scene_asset(job_dir, channel_path, "scene-01", _fake_image_fn(captured)))

    index = read_image_prompt_index(job_dir)
    scene_recs = [r for r in index if r["kind"] == "scene_asset"]
    assert len(scene_recs) == 1
    rec = scene_recs[0]
    assert rec["stage"] == "assets_chatgpt"
    assert rec["scene_id"] == "scene-01"
    assert rec["prompt"] == captured[0]
    assert "una cocina luminosa con fruta" in rec["prompt"]


def test_thumbnail_stage_logs_exact_prompt(tmp_path, channel_path):
    from unittest.mock import patch

    from video_agent.orchestrator.job_state import DEFAULT_STAGES, JobState, StageStatus, save_job
    from video_agent.orchestrator.orchestrator import _now

    job_dir = tmp_path / "job-thumb"
    ts = _now()
    all_stages = list(DEFAULT_STAGES)
    if "thumbnail_image" not in all_stages:
        all_stages.insert(all_stages.index("whisper_timestamps"), "thumbnail_image")
    stages = []
    found = False
    for name in all_stages:
        if name == "thumbnail_image":
            stages.append(StageStatus(name=name, status="pending"))
            found = True
        elif not found:
            stages.append(StageStatus(name=name, status="completed", started_at=ts, completed_at=ts))
        else:
            stages.append(StageStatus(name=name, status="pending"))
    save_job(job_dir, JobState(
        job_id=job_dir.name, channel_id="vida-plena-45", idea_path="idea.json",
        created_at=ts, updated_at=ts, current_stage="thumbnail_image", stages=stages,
    ))
    seo = {
        "job_id": job_dir.name, "title": "5 secretos para dormir bien",
        "thumbnail_text": "DUERME MEJOR HOY", "description": "Desc", "tags": ["sueño"],
        "language": "es-ES", "ai_disclosure": True, "thumbnail_path": "",
        "title_variants": [{"title": "5 secretos", "thumbnail_text": "DUERME MEJOR HOY", "score": 85}],
    }
    (job_dir / "seo.json").write_text(json.dumps(seo), encoding="utf-8")
    (job_dir / "assets").mkdir(parents=True, exist_ok=True)

    captured: list[str] = []
    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, _fake_image_fn(captured)))

    index = read_image_prompt_index(job_dir)
    thumb_recs = [r for r in index if r["kind"] == "thumbnail"]
    assert len(thumb_recs) >= 1
    # Every thumbnail prompt sent was logged exactly.
    logged = {r["prompt"] for r in thumb_recs}
    for sent in captured:
        assert sent in logged
    assert thumb_recs[0]["stage"] == "thumbnail_image"
    assert thumb_recs[0]["index"] is not None
