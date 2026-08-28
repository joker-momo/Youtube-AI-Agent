import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from video_agent.orchestrator.stages import _build_thumbnail_prompt, auto_thumbnail_image_stage
from video_agent.orchestrator.stages import StageInputMissingError
from video_agent.orchestrator.job_state import DEFAULT_STAGES
from video_agent.orchestrator.stages.assets_thumbnail import _discover_recent_thumbnail_history


# ── _build_thumbnail_prompt ───────────────────────────────────────────────────

def test_build_thumbnail_prompt_contains_title():
    p = _build_thumbnail_prompt("5 secretos para dormir mejor", "DUERME MEJOR HOY", "#F2C94C", "Bienestar 45+")
    assert "5 secretos para dormir mejor" in p


def test_build_thumbnail_prompt_contains_thumbnail_text():
    p = _build_thumbnail_prompt("Test title", "INSOMNIO SECRETO", "#F2C94C", "Wellness")
    assert "INSOMNIO SECRETO" in p




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
        "tags": ["sueño"], "language": "es-ES", "ai_disclosure": True,
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
        from PIL import Image
        Image.new("RGB", (640, 360), (12, 34, 56)).save(out_path, format="PNG")
        return {"src": "https://example.com/img.png", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        result = asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn))

    assert result == job_dir / "seo.json"
    seo = json.loads((job_dir / "seo.json").read_text())
    assert seo["thumbnail_path"].endswith("thumbnail_1.jpg")
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
        from PIL import Image
        Image.new("RGB", (640, 360), (12, 34, 56)).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0))

    assert any("INSOMNIO SECRETO" in p for p in captured)


def test_auto_thumbnail_image_stage_binds_variant_title_per_image(tmp_path, channel_path):
    """Spec v5.6 P0: each variant prompt must use the variant's own title."""
    job_dir = tmp_path / "job-thumb-variants"
    _seed_at_thumbnail_image(job_dir)
    seo = json.loads((job_dir / "seo.json").read_text())
    seo["title_variants"] = [
        {"title": "Variant A face", "thumbnail_text": "INSOMNIO 45", "score": 90},
        {"title": "Variant B object", "thumbnail_text": "TU CAMA HABLA", "score": 88},
        {"title": "Variant C compare", "thumbnail_text": "ANTES Y DESPUÉS", "score": 85},
    ]
    (job_dir / "seo.json").write_text(json.dumps(seo))

    captured = []

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        captured.append(prompt)
        from PIL import Image
        Image.new("RGB", (640, 360), (12, 34, 56)).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0))

    assert len(captured) == 3
    # Each prompt carries its own variant title (not the top-level seo.title).
    assert any("Variant A face" in p for p in captured)
    assert any("Variant B object" in p for p in captured)
    assert any("Variant C compare" in p for p in captured)
    # Each prompt advertises a distinct variant strategy.
    assert sum("FACE-DRIVEN" in p for p in captured) == 1
    assert sum("OBJECT-DRIVEN" in p for p in captured) == 1
    assert sum("COMPARISON-DRIVEN" in p for p in captured) == 1


def test_auto_thumbnail_image_stage_passes_style_dna_palette_to_planner(
    tmp_path, channel_path
):
    job_dir = tmp_path / "job-thumb-brand-palette"
    _seed_at_thumbnail_image(job_dir)
    seo = json.loads((job_dir / "seo.json").read_text())
    seo["title_variants"] = [
        {"title": "Variant A", "thumbnail_text": "DUERME MEJOR HOY"},
        {"title": "Variant B", "thumbnail_text": "MEJORA TU DESCANSO"},
        {"title": "Variant C", "thumbnail_text": "CAMBIA TU RUTINA"},
    ]
    (job_dir / "seo.json").write_text(json.dumps(seo))

    channel = yaml.safe_load(channel_path.read_text())
    channel.pop("style", None)
    channel["thumbnail"] = {"persona_reference": "persona.jpeg"}
    channel_path.write_text(yaml.safe_dump(channel))
    style_dna = {
        "palette": {
            "primary": "#2F6B57",
            "secondary": "#D98C5F",
            "accent": "#F5C24B",
        }
    }

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        from PIL import Image

        Image.new("RGB", (640, 360), (12, 34, 56)).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    with (
        patch("video_agent.contracts.repo_root", return_value=tmp_path),
        patch(
            "video_agent.orchestrator.stages.assets_thumbnail.load_style_dna",
            return_value=style_dna,
        ),
    ):
        asyncio.run(
            auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0)
        )

    plans = json.loads(
        (job_dir / "json" / "thumbnail_prompt_plans.json").read_text()
    )
    assert {plan["accent_color"] for plan in plans} == {
        "#2F6B57",
        "#D98C5F",
        "#F5C24B",
    }


def test_auto_thumbnail_image_stage_writes_prompt_logs(tmp_path, channel_path):
    """Spec v5.6 P1: persist per-variant prompts under operator/chatgpt/."""
    job_dir = tmp_path / "job-thumb-logs"
    _seed_at_thumbnail_image(job_dir)

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        from PIL import Image
        Image.new("RGB", (640, 360), (12, 34, 56)).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn))

    log_dir = job_dir / "operator" / "chatgpt"
    log_paths = sorted(log_dir.glob("thumbnail_prompt_*.md"))
    assert log_paths, "expected at least one persisted thumbnail prompt"
    body = log_paths[0].read_text(encoding="utf-8")
    assert "Thumbnail prompt — variant" in body
    assert "thumbnail_text:" in body


def test_auto_thumbnail_image_stage_enforces_1920x1080(tmp_path, channel_path):
    """Spec v5.6 P1: ImageOps.fit crop to canonical YouTube thumbnail size."""
    job_dir = tmp_path / "job-thumb-size"
    _seed_at_thumbnail_image(job_dir)

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        # Simulate ChatGPT returning the wrong size.
        from PIL import Image
        Image.new("RGB", (1024, 1024), (200, 50, 50)).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn))

    from PIL import Image as _PilImage

    jpg = next((job_dir / "outputs").glob("thumbnail_*.jpg"))
    with _PilImage.open(jpg) as img:
        assert img.size == (1920, 1080)


# ── read-only recent-thumbnail history discovery ────────────────────────────

def _make_long_form_job(
    jobs_root: Path,
    name: str,
    *,
    has_thumbnail: bool = True,
    signature: dict | None = "OMIT",
    mtime_offset: float = 0.0,
) -> Path:
    job_dir = jobs_root / name
    (job_dir / "json").mkdir(parents=True, exist_ok=True)
    (job_dir / "json" / "seo.json").write_text(json.dumps({"job_id": name}), encoding="utf-8")
    if has_thumbnail:
        (job_dir / "outputs").mkdir(parents=True, exist_ok=True)
        thumb = job_dir / "outputs" / "thumbnail.jpg"
        from PIL import Image
        Image.new("RGB", (1920, 1080), (10, 20, 30)).save(thumb, format="JPEG")
        if mtime_offset:
            new_time = time.time() + mtime_offset
            import os
            os.utime(thumb, (new_time, new_time))
    if signature != "OMIT":
        report = {"selected_signature": signature} if signature is not None else {}
        (job_dir / "json" / "thumbnail_quality_report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
    return job_dir


def test_history_excludes_current_job(tmp_path):
    jobs_root = tmp_path / "jobs"
    current = _make_long_form_job(jobs_root, "current-job")
    _make_long_form_job(jobs_root, "other-job", mtime_offset=-10)

    history = _discover_recent_thumbnail_history(current)
    assert all(entry["job_id"] != "current-job" for entry in history)


def test_history_only_includes_jobs_with_a_selected_image(tmp_path):
    jobs_root = tmp_path / "jobs"
    current = _make_long_form_job(jobs_root, "current-job")
    _make_long_form_job(jobs_root, "no-thumbnail-yet", has_thumbnail=False, mtime_offset=-5)
    with_thumb = _make_long_form_job(jobs_root, "has-thumbnail", mtime_offset=-5)

    history = _discover_recent_thumbnail_history(current)
    job_ids = {entry["job_id"] for entry in history}
    assert "has-thumbnail" in job_ids
    assert "no-thumbnail-yet" not in job_ids
    assert with_thumb.exists()


def test_history_sorts_by_selected_image_mtime_descending(tmp_path):
    jobs_root = tmp_path / "jobs"
    current = _make_long_form_job(jobs_root, "current-job")
    _make_long_form_job(jobs_root, "oldest", mtime_offset=-300)
    _make_long_form_job(jobs_root, "newest", mtime_offset=-10)
    _make_long_form_job(jobs_root, "middle", mtime_offset=-150)

    history = _discover_recent_thumbnail_history(current)
    assert [entry["job_id"] for entry in history] == ["newest", "middle", "oldest"]


def test_history_caps_at_five_entries(tmp_path):
    jobs_root = tmp_path / "jobs"
    current = _make_long_form_job(jobs_root, "current-job")
    for i in range(8):
        _make_long_form_job(jobs_root, f"job-{i}", mtime_offset=-(i + 1) * 10)

    history = _discover_recent_thumbnail_history(current)
    assert len(history) == 5


def test_history_supplies_selected_signature_when_present(tmp_path):
    jobs_root = tmp_path / "jobs"
    current = _make_long_form_job(jobs_root, "current-job")
    sig = {"strategy": "face_driven", "setting_family": "kitchen"}
    _make_long_form_job(jobs_root, "with-sig", signature=sig, mtime_offset=-5)

    history = _discover_recent_thumbnail_history(current)
    entry = next(e for e in history if e["job_id"] == "with-sig")
    assert entry["signature"] == sig


def test_history_older_job_without_report_supplies_path_and_none_signature(tmp_path):
    jobs_root = tmp_path / "jobs"
    current = _make_long_form_job(jobs_root, "current-job")
    _make_long_form_job(jobs_root, "no-report", signature="OMIT", mtime_offset=-5)

    history = _discover_recent_thumbnail_history(current)
    entry = next(e for e in history if e["job_id"] == "no-report")
    assert entry["signature"] is None
    assert entry["path"]


def test_history_malformed_report_is_skipped_with_no_exception(tmp_path):
    jobs_root = tmp_path / "jobs"
    current = _make_long_form_job(jobs_root, "current-job")
    job_dir = _make_long_form_job(jobs_root, "malformed", signature="OMIT", mtime_offset=-5)
    (job_dir / "json" / "thumbnail_quality_report.json").write_text("{not json", encoding="utf-8")

    history = _discover_recent_thumbnail_history(current)
    entry = next(e for e in history if e["job_id"] == "malformed")
    assert entry["signature"] is None


def test_history_discovery_never_writes_to_prior_directories(tmp_path):
    jobs_root = tmp_path / "jobs"
    current = _make_long_form_job(jobs_root, "current-job")
    other = _make_long_form_job(jobs_root, "other-job", mtime_offset=-5)
    before = {p: p.stat().st_mtime for p in other.rglob("*") if p.is_file()}

    _discover_recent_thumbnail_history(current)

    after = {p: p.stat().st_mtime for p in other.rglob("*") if p.is_file()}
    assert before == after


def test_history_excludes_shorts_shaped_jobs_without_seo_artifact(tmp_path):
    jobs_root = tmp_path / "jobs"
    current = _make_long_form_job(jobs_root, "current-job")
    shorts_job = jobs_root / "shorts-job-20260101-000000"
    (shorts_job / "outputs").mkdir(parents=True, exist_ok=True)
    from PIL import Image
    Image.new("RGB", (1080, 1920), (5, 5, 5)).save(shorts_job / "outputs" / "thumbnail.jpg", format="JPEG")

    history = _discover_recent_thumbnail_history(current)
    assert all(entry["job_id"] != "shorts-job-20260101-000000" for entry in history)


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
