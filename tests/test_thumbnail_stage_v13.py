"""Spec v1.3 Phase 2 stage integration tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from video_agent.orchestrator.job_state import DEFAULT_STAGES
from video_agent.orchestrator.stages import (
    StageInputMissingError,
    _build_thumbnail_prompt,
    auto_thumbnail_image_stage,
)


@pytest.fixture()
def channel_path(tmp_path: Path) -> Path:
    cfg = {
        "channel": {
            "id": "vida-plena-45",
            "name": "Vida Plena 45+",
            "description": "Bienestar adultos 45+",
        },
        "style": {
            "palette": {
                "accent": "#F2C94C",
                "primary": "#2F6B57",
                "secondary": "#D98C5F",
                "background": "#F6F1E8",
                "text": "#26332F",
            }
        },
        "render": {"fps": 30, "resolution": "1920x1080", "duration_sec": 54},
        "visuals": {"strategy": "pexels_video"},
        "tts": {"provider": "mock-local"},
    }
    p = tmp_path / "channel.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return p


def _seed_at_thumbnail_image(job_dir: Path) -> None:
    """Seed a job at current_stage='thumbnail_image' with completed predecessors."""
    from video_agent.orchestrator.job_state import JobState, StageStatus, save_job
    from video_agent.orchestrator.orchestrator import _now

    ts = _now()
    stage_name = "thumbnail_image"
    all_stages = list(DEFAULT_STAGES)
    if stage_name not in all_stages:
        idx = all_stages.index("whisper_timestamps")
        all_stages.insert(idx, stage_name)

    stages: list[StageStatus] = []
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
        "job_id": job_dir.name,
        "title": "Cómo saber si tu cena te roba energía",
        "thumbnail_text": "CENA Y ENERGÍA",
        "description": "Desc",
        "tags": ["energia"],
        "language": "es-ES",
        "ai_disclosure": True,
        "thumbnail_path": "",
        "title_variants": [
            {
                "title": "Si te despiertas cansado después de los 45",
                "thumbnail_text": "DUERME MEJOR",
                "score": 90,
            },
            {
                "title": "Café por la noche y tu corazón",
                "thumbnail_text": "EL CAFÉ Y EL CORAZÓN",
                "score": 88,
            },
            {
                "title": "Mejor pan vs peor pan después de los 50",
                "thumbnail_text": "EL PAN CORRECTO",
                "score": 85,
            },
        ],
    }
    (job_dir / "seo.json").write_text(json.dumps(seo), encoding="utf-8")


# Test 17: prompt logged per variant ----------------------------------------

def test_stage_writes_planner_metadata_json(tmp_path: Path, channel_path: Path):
    job_dir = tmp_path / "job-v13-1"
    _seed_at_thumbnail_image(job_dir)

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        from PIL import Image
        Image.new("RGB", (640, 360), (12, 34, 56)).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn))

    plans_path = job_dir / "json" / "thumbnail_prompt_plans.json"
    assert plans_path.exists(), "thumbnail_prompt_plans.json must be written"
    plans = json.loads(plans_path.read_text(encoding="utf-8"))
    assert isinstance(plans, list) and len(plans) >= 1
    # §14: full prompt text must NOT be in the metadata JSON.
    for plan in plans:
        assert "prompt" not in plan
        # but the planner schema fields must be present.
        assert plan.get("variant_index")
        assert plan.get("visual_strategy")
        assert plan.get("primary_category")
        assert plan.get("primary_category_label")
        assert plan.get("category_safety_rules")


def test_stage_uses_planner_prompts_with_variant_strategies(
    tmp_path: Path, channel_path: Path
):
    job_dir = tmp_path / "job-v13-2"
    _seed_at_thumbnail_image(job_dir)

    captured: list[str] = []

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        captured.append(prompt)
        from PIL import Image
        Image.new("RGB", (640, 360), (12, 34, 56)).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn))

    assert len(captured) == 3
    # Each variant must use its own variant_title (planner uses variant title,
    # which differs from the top-level seo.title).
    assert any("Si te despiertas cansado" in p for p in captured)
    assert any("Café por la noche" in p for p in captured)
    assert any("Mejor pan vs peor pan" in p for p in captured)
    # Distinct visual strategies — one per variant.
    assert sum("FACE-DRIVEN" in p for p in captured) == 1
    assert sum("OBJECT-DRIVEN" in p for p in captured) == 1
    assert sum("COMPARISON-DRIVEN" in p for p in captured) == 1


def test_stage_persists_prompt_markdown_per_variant(
    tmp_path: Path, channel_path: Path
):
    job_dir = tmp_path / "job-v13-3"
    _seed_at_thumbnail_image(job_dir)

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        from PIL import Image
        Image.new("RGB", (640, 360), (12, 34, 56)).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn))

    log_dir = job_dir / "operator" / "chatgpt"
    log_paths = sorted(log_dir.glob("thumbnail_prompt_*.md"))
    assert len(log_paths) == 3
    body = log_paths[0].read_text(encoding="utf-8")
    assert "Thumbnail prompt — variant" in body


def test_backward_compat_wrapper_returns_planner_prompt():
    """Wrapper must route through the planner."""
    prompt = _build_thumbnail_prompt(
        "Test topic about sleep",
        "DUERME MEJOR HOY",
        "#F2C94C",
        "Wellness channel",
        variant_index=2,
    )
    assert "DUERME MEJOR HOY" in prompt
    assert "OBJECT-DRIVEN" in prompt
    # Wrapper accent_color must flow through to the rendered prompt.
    assert "#F2C94C" in prompt
