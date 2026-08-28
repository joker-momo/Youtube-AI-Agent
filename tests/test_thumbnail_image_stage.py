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
        _pattern_image(len(captured)).save(out_path, format="PNG")
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

    calls = {"n": 0}

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        calls["n"] += 1
        _pattern_image(calls["n"]).save(out_path, format="PNG")
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


# ── QA-driven primary selection (Task 6) ────────────────────────────────────

_WEAK_TITLE_VARIANT = {"title": "Consejo", "thumbnail_text": "MIRA ESTO"}
_STRONG_TITLE_VARIANT = {
    "title": "Pierdes fuerza en las piernas si evitas caminar cada día",
    "thumbnail_text": "EVITA PERDER FUERZA HOY",
}
_WEAK_TITLE_VARIANT_2 = {"title": "Rutina", "thumbnail_text": "CAMBIA ALGO"}


def _seed_three_variants(job_dir: Path, variants: list[dict]) -> None:
    _seed_at_thumbnail_image(job_dir)
    seo = json.loads((job_dir / "seo.json").read_text())
    seo["title_variants"] = [dict(v, score=50) for v in variants]
    (job_dir / "seo.json").write_text(json.dumps(seo))


def _pattern_image(variant_index: int, width: int = 1920, height: int = 1080):
    """A distinctive, non-flat synthetic image per variant.

    A flat solid color has zero gradient anywhere, so every candidate would
    hash identically (dHash sees no edges) and trip the sibling-similarity
    QA check as a false "near duplicate". Real generated thumbnails are
    photographic, so these small distinct stripe patterns are closer to
    realistic per-candidate variety while staying fast and deterministic.
    """
    from PIL import Image
    tiny = Image.new("RGB", (8, 8))
    pixels = tiny.load()
    for y in range(8):
        for x in range(8):
            if variant_index == 1:
                on = x % 2 == 0
            elif variant_index == 2:
                on = y % 2 == 0
            else:
                on = (x + y) % 3 == 0
            pixels[x, y] = (230, 230, 230) if on else (20, 20, 20)
    return tiny.resize((width, height), Image.NEAREST)


def _flat_color_image_fn(colors: dict[int, tuple[int, int, int]] | None = None):
    """Per-call fake image_fn; `colors` is accepted for call-signature parity
    with earlier tests but each candidate gets a distinct pattern (see
    `_pattern_image`) so QA similarity checks behave realistically."""
    calls = {"n": 0}

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        calls["n"] += 1
        _pattern_image(calls["n"]).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    return fake_image_fn


def test_stage_selects_variant_two_when_it_has_highest_valid_score(tmp_path, channel_path):
    job_dir = tmp_path / "job-select-v2"
    _seed_three_variants(
        job_dir, [_WEAK_TITLE_VARIANT, _STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    fake_image_fn = _flat_color_image_fn({1: (10, 10, 10), 2: (200, 30, 30), 3: (10, 200, 10)})

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0))

    seo = json.loads((job_dir / "seo.json").read_text())
    assert seo["thumbnail_path"].endswith("thumbnail_2.jpg")

    thumbnail_jpg = job_dir / "outputs" / "thumbnail.jpg"
    variant_2_jpg = job_dir / "outputs" / "thumbnail_2.jpg"
    assert thumbnail_jpg.read_bytes() == variant_2_jpg.read_bytes()


def test_stage_does_not_publish_first_generated_candidate_when_it_hard_fails(tmp_path, channel_path):
    """Variant 1 would win on package score alone, but its OCR check hard
    fails — a later valid variant must be selected instead."""
    job_dir = tmp_path / "job-hard-fail-v1"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    fake_image_fn = _flat_color_image_fn({1: (10, 10, 10), 2: (200, 30, 30), 3: (10, 200, 10)})

    def fake_ocr(jpg_path, expected_text):
        from video_agent.qa.thumbnail_package_qa import OcrBox, ThumbnailOcrResult
        if str(jpg_path).endswith("thumbnail_1.jpg"):
            # Completely wrong OCR text -> hard fail on variant 1.
            return ThumbnailOcrResult(boxes=(OcrBox(text="TEXTO INCORRECTO", left=0, top=0, width=10, height=10),))
        return ThumbnailOcrResult(boxes=(OcrBox(text=expected_text, left=0, top=0, width=10, height=10),))

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(
                job_dir, channel_path, fake_image_fn, throttle_sec=0, thumbnail_ocr_fn=fake_ocr
            )
        )

    seo = json.loads((job_dir / "seo.json").read_text())
    assert not seo["thumbnail_path"].endswith("thumbnail_1.jpg")


def test_stage_fails_when_all_generated_candidates_fail_qa(tmp_path, channel_path):
    job_dir = tmp_path / "job-all-fail"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    fake_image_fn = _flat_color_image_fn({1: (10, 10, 10), 2: (200, 30, 30), 3: (10, 200, 10)})

    def fake_ocr_all_wrong(jpg_path, expected_text):
        from video_agent.qa.thumbnail_package_qa import OcrBox, ThumbnailOcrResult
        return ThumbnailOcrResult(boxes=(OcrBox(text="COMPLETAMENTE DISTINTO", left=0, top=0, width=10, height=10),))

    from video_agent.qa.thumbnail_package_qa import ThumbnailQualityError

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        with pytest.raises(ThumbnailQualityError) as exc_info:
            asyncio.run(
                auto_thumbnail_image_stage(
                    job_dir, channel_path, fake_image_fn, throttle_sec=0,
                    thumbnail_ocr_fn=fake_ocr_all_wrong,
                )
            )

    assert len(exc_info.value.reasons) == 3
    assert not (job_dir / "outputs" / "thumbnail.jpg").exists()
    seo = json.loads((job_dir / "seo.json").read_text())
    assert seo["thumbnail_path"] == ""


def test_stage_ocr_supplied_and_passing_is_reflected_in_quality_report(tmp_path, channel_path):
    job_dir = tmp_path / "job-ocr-pass"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    fake_image_fn = _flat_color_image_fn({1: (10, 10, 10), 2: (200, 30, 30), 3: (10, 200, 10)})

    def fake_ocr(jpg_path, expected_text):
        from video_agent.qa.thumbnail_package_qa import OcrBox, ThumbnailOcrResult
        return ThumbnailOcrResult(boxes=(OcrBox(text=expected_text, left=0, top=0, width=10, height=10),))

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(
                job_dir, channel_path, fake_image_fn, throttle_sec=0, thumbnail_ocr_fn=fake_ocr
            )
        )

    report = json.loads((job_dir / "json" / "thumbnail_quality_report.json").read_text())
    assert report["requires_manual_review"] is False
    winner = next(c for c in report["candidates"] if c["variant_index"] == report["selected_variant_index"])
    assert winner["ocr_check"]["status"] == "pass"


def test_stage_ocr_absent_produces_manual_review_report_but_still_selects(tmp_path, channel_path):
    job_dir = tmp_path / "job-ocr-absent"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    fake_image_fn = _flat_color_image_fn({1: (10, 10, 10), 2: (200, 30, 30), 3: (10, 200, 10)})

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0))

    report = json.loads((job_dir / "json" / "thumbnail_quality_report.json").read_text())
    assert report["requires_manual_review"] is True
    for candidate in report["candidates"]:
        assert candidate["ocr_check"]["status"] == "not_run"
    seo = json.loads((job_dir / "seo.json").read_text())
    assert seo["thumbnail_path"]


def test_quality_report_is_written_before_primary_alias_and_persistence(tmp_path, channel_path):
    """Prove ordering directly (mtime is unreliable here: shutil.copy2
    preserves the SOURCE candidate's mtime on the alias, not copy time)."""
    job_dir = tmp_path / "job-order"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    fake_image_fn = _flat_color_image_fn({1: (10, 10, 10), 2: (200, 30, 30), 3: (10, 200, 10)})

    import shutil as shutil_module
    original_copy2 = shutil_module.copy2
    report_path = job_dir / "json" / "thumbnail_quality_report.json"
    first_copy_seen: dict[str, bool] = {}

    def spy_copy2(src, dst, *args, **kwargs):
        if "report_existed" not in first_copy_seen:
            first_copy_seen["report_existed"] = report_path.exists()
        return original_copy2(src, dst, *args, **kwargs)

    with (
        patch("video_agent.contracts.repo_root", return_value=tmp_path),
        patch("shutil.copy2", side_effect=spy_copy2),
    ):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0))

    assert first_copy_seen.get("report_existed") is True


def test_selected_alias_is_copied_to_public_jobs_dir(tmp_path, channel_path):
    job_dir = tmp_path / "job-public-copy"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    fake_image_fn = _flat_color_image_fn({1: (10, 10, 10), 2: (200, 30, 30), 3: (10, 200, 10)})

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0))

    seo = json.loads((job_dir / "seo.json").read_text())
    public_ref = seo["thumbnail_path"]
    assert public_ref.startswith("jobs/")
    selected_name = public_ref.rsplit("/", 1)[-1]
    public_path = tmp_path / "remotion" / "public" / public_ref
    selected_jpg = job_dir / "outputs" / selected_name
    assert public_path.exists()
    assert public_path.read_bytes() == selected_jpg.read_bytes()
    assert (job_dir.parent / "remotion" / "public" / "jobs" / job_dir.name / "outputs" / "thumbnail.jpg").exists()


def test_variant_one_generation_failure_still_selects_best_valid_survivor(tmp_path, channel_path):
    job_dir = tmp_path / "job-v1-gen-fail"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    calls = {"n": 0}

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated generation failure for variant 1")
        _pattern_image(calls["n"]).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0))

    seo = json.loads((job_dir / "seo.json").read_text())
    assert not seo["thumbnail_path"].endswith("thumbnail_1.jpg")
    assert seo["thumbnail_path"]


def test_batch_generation_path_also_uses_qa_selection(tmp_path, channel_path):
    job_dir = tmp_path / "job-batch-select"
    _seed_three_variants(
        job_dir, [_WEAK_TITLE_VARIANT, _STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )

    class FakeBatchImageFn:
        async def generate_images(self, *, prompts, project_name, out_paths):
            for index, out_path in enumerate(out_paths, start=1):
                _pattern_image(index).save(out_path, format="PNG")

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(job_dir, channel_path, FakeBatchImageFn(), throttle_sec=0)
        )

    seo = json.loads((job_dir / "seo.json").read_text())
    assert seo["thumbnail_path"].endswith("thumbnail_2.jpg")


# ── provenance and no-false-pass regression (Task 7) ────────────────────────

def test_prompt_markdown_log_contains_the_exact_prompt_that_was_sent(tmp_path, channel_path):
    job_dir = tmp_path / "job-prompt-provenance"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    sent_prompts: dict[int, str] = {}
    calls = {"n": 0}

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        calls["n"] += 1
        sent_prompts[calls["n"]] = prompt
        _pattern_image(calls["n"]).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0))

    assert sent_prompts
    for index, prompt_text in sent_prompts.items():
        md_path = job_dir / "operator" / "chatgpt" / f"thumbnail_prompt_{index}.md"
        body = md_path.read_text(encoding="utf-8")
        assert prompt_text in body


def test_unified_image_prompt_audit_trail_matches_the_exact_sent_prompt(tmp_path, channel_path):
    import hashlib

    from video_agent.orchestrator.image_prompt_log import IMAGE_PROMPT_LOG_DIR, INDEX_NAME

    job_dir = tmp_path / "job-audit-provenance"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    sent_prompts: list[str] = []

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        sent_prompts.append(prompt)
        _pattern_image(len(sent_prompts)).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0))

    index_path = job_dir / IMAGE_PROMPT_LOG_DIR / INDEX_NAME
    records = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    thumbnail_records = [r for r in records if r["kind"] == "thumbnail"]
    assert len(thumbnail_records) == len(sent_prompts)
    for record in thumbnail_records:
        matching = sent_prompts[record["index"] - 1]
        assert record["prompt"] == matching
        assert record["prompt_sha256"] == hashlib.sha256(matching.encode("utf-8")).hexdigest()


def test_concept_signature_is_metadata_only_and_does_not_alter_the_sent_prompt(tmp_path, channel_path):
    """Removing concept_signature from a plan must not change the prompt text
    build_thumbnail_prompt() produces — it's selection metadata, not prompt
    content."""
    from video_agent.thumbnail_planner import build_thumbnail_prompt, plan_thumbnail_prompts

    seo = {
        "title": "Dormir mejor después de los 60",
        "title_variants": [{"title": "Dormir mejor", "thumbnail_text": "DUERME MEJOR"}],
    }
    plans = plan_thumbnail_prompts(seo, {})
    plan = plans[0]
    assert "concept_signature" in plan

    with_signature = build_thumbnail_prompt(plan)
    stripped_plan = {k: v for k, v in plan.items() if k != "concept_signature"}
    without_signature = build_thumbnail_prompt(stripped_plan)
    assert with_signature == without_signature


def test_selected_variant_index_matches_filename_public_path_bytes_and_event_log(tmp_path, channel_path):
    job_dir = tmp_path / "job-index-consistency"
    _seed_three_variants(
        job_dir, [_WEAK_TITLE_VARIANT, _STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    fake_image_fn = _flat_color_image_fn({1: (10, 10, 10), 2: (200, 30, 30), 3: (10, 200, 10)})

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0))

    report = json.loads((job_dir / "json" / "thumbnail_quality_report.json").read_text())
    selected_index = report["selected_variant_index"]

    seo = json.loads((job_dir / "seo.json").read_text())
    assert seo["thumbnail_path"].endswith(f"thumbnail_{selected_index}.jpg")

    variant_jpg = job_dir / "outputs" / f"thumbnail_{selected_index}.jpg"
    alias_jpg = job_dir / "outputs" / "thumbnail.jpg"
    assert alias_jpg.read_bytes() == variant_jpg.read_bytes()

    events = [
        json.loads(line)
        for line in (job_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected_events = [e for e in events if e.get("event") == "THUMBNAIL_PRIMARY_SELECTED"]
    assert selected_events
    assert selected_events[-1]["data"]["variant"] == selected_index


# ── recent-signature history actually reaches the generated prompt ─────────
# Codex verification catch: `plan_thumbnail_prompts(..., recent_signatures=)`
# was never called with real history, and the repetition-avoidance mutation
# only touched `concept_signature` metadata after the prompt was already
# built — a signature-only "fix" with zero effect on what ChatGPT receives.

def test_prior_job_signature_history_changes_the_generated_face_driven_prompt(tmp_path, channel_path):
    baseline_job = tmp_path / "job-baseline"
    _seed_three_variants(
        baseline_job, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    baseline_prompts: list[str] = []

    async def capture_image_fn(*, prompt, project_name, out_path, **kwargs):
        baseline_prompts.append(prompt)
        _pattern_image(len(baseline_prompts)).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(baseline_job, channel_path, capture_image_fn, throttle_sec=0)
        )

    baseline_plans = json.loads((baseline_job / "json" / "thumbnail_prompt_plans.json").read_text())
    baseline_face_plan = next(p for p in baseline_plans if p["visual_strategy"] == "face_driven")
    baseline_face_signature = baseline_face_plan["concept_signature"]
    baseline_face_prompt = baseline_prompts[baseline_face_plan["variant_index"] - 1]

    # A sibling job whose selected primary shares the exact same face-driven
    # signature (setting/action/text-zone) that this job would otherwise
    # default to — this is the repetition the history mechanism must break.
    _make_long_form_job(
        tmp_path, "sibling-job", signature=baseline_face_signature, mtime_offset=-5
    )

    adjusted_job = tmp_path / "job-adjusted"
    _seed_three_variants(
        adjusted_job, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    adjusted_prompts: list[str] = []

    async def capture_image_fn_2(*, prompt, project_name, out_path, **kwargs):
        adjusted_prompts.append(prompt)
        _pattern_image(len(adjusted_prompts)).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(adjusted_job, channel_path, capture_image_fn_2, throttle_sec=0)
        )

    adjusted_plans = json.loads((adjusted_job / "json" / "thumbnail_prompt_plans.json").read_text())
    adjusted_face_plan = next(p for p in adjusted_plans if p["visual_strategy"] == "face_driven")
    adjusted_face_prompt = adjusted_prompts[adjusted_face_plan["variant_index"] - 1]

    # The real point: the SENT prompt differs, not just the signature label.
    assert adjusted_face_prompt != baseline_face_prompt
    assert adjusted_face_plan["scene"] != baseline_face_plan["scene"]
    assert adjusted_face_plan["concept_signature"]["setting_family"] != baseline_face_signature["setting_family"]


def test_production_candidate_reports_include_structured_signature_gates(tmp_path, channel_path):
    """signature_difference_status() must actually be called for real
    candidates, not just exist as unit-tested dead code."""
    job_dir = tmp_path / "job-signature-gates"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    fake_image_fn = _flat_color_image_fn({1: (10, 10, 10), 2: (200, 30, 30), 3: (10, 200, 10)})

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0))

    report = json.loads((job_dir / "json" / "thumbnail_quality_report.json").read_text())
    for candidate in report["candidates"]:
        assert "sibling_signature_checks" in candidate
        assert len(candidate["sibling_signature_checks"]) == 2  # vs. the other 2 variants
        for check in candidate["sibling_signature_checks"]:
            assert check["status"] == "pass"  # siblings guarantee >=5 differences (Task 3)
            assert check["differences"] >= 5


# ── Codex verification round 3: design §10 combined history hard-fail,
# §5.1 not_available serialization, §13 events and aggregate report fields ──

def test_history_hard_fails_only_when_image_and_signature_both_insufficient(tmp_path, channel_path):
    """Design §10: hard fail requires a near-duplicate recent IMAGE *and*
    fewer than 3 signature dimensions differing — never either alone."""
    baseline_job = tmp_path / "job-combined-baseline"
    _seed_three_variants(
        baseline_job, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(baseline_job, channel_path, _flat_color_image_fn(), throttle_sec=0)
        )
    baseline_plans = json.loads((baseline_job / "json" / "thumbnail_prompt_plans.json").read_text())
    baseline_face_plan = next(p for p in baseline_plans if p["visual_strategy"] == "face_driven")
    face_index = baseline_face_plan["variant_index"]
    baseline_face_bytes = (baseline_job / "outputs" / f"thumbnail_{face_index}.jpg").read_bytes()

    # Sibling whose selected primary is BYTE-IDENTICAL (dHash distance 0) and
    # whose signature exactly matches the baseline's face-driven candidate —
    # both the image and the (pre-nudge) signature are maximally similar.
    sibling = tmp_path / "sibling-combined"
    (sibling / "json").mkdir(parents=True)
    (sibling / "json" / "seo.json").write_text(json.dumps({"job_id": "sibling-combined"}), encoding="utf-8")
    (sibling / "outputs").mkdir(parents=True)
    (sibling / "outputs" / "thumbnail.jpg").write_bytes(baseline_face_bytes)
    (sibling / "json" / "thumbnail_quality_report.json").write_text(
        json.dumps({"selected_signature": baseline_face_plan["concept_signature"]}), encoding="utf-8"
    )

    adjusted_job = tmp_path / "job-combined-adjusted"
    _seed_three_variants(
        adjusted_job, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(adjusted_job, channel_path, _flat_color_image_fn(), throttle_sec=0)
        )

    report = json.loads((adjusted_job / "json" / "thumbnail_quality_report.json").read_text())
    face_candidate = next(c for c in report["candidates"] if c["variant_index"] == face_index)
    combined = next(
        c for c in face_candidate["history_combined_checks"] if c["path"].endswith("thumbnail.jpg")
    )
    # The historical entry is a genuine repeat on BOTH axes, so it must be
    # the hard-failing kind, not a mere warning — and the still-valid
    # sibling variants must be the ones actually selected.
    assert combined["image_check"]["status"] == "fail"
    assert combined["signature_check"]["status"] == "fail"
    assert combined["status"] == "fail"
    assert report["selected_variant_index"] != face_index


def test_missing_history_signature_is_recorded_not_available_not_skipped(tmp_path, channel_path):
    """Design §5.1: a prior job with no signature metadata must still be
    compared (image-only) and recorded as not_available — never silently
    dropped from the report and never mislabeled as a pass."""
    job_dir = tmp_path / "job-missing-sig"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    _make_long_form_job(tmp_path, "no-signature-sibling", signature=None, mtime_offset=-5)

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(job_dir, channel_path, _flat_color_image_fn(), throttle_sec=0)
        )

    report = json.loads((job_dir / "json" / "thumbnail_quality_report.json").read_text())
    for candidate in report["candidates"]:
        assert candidate["history_signature_checks"], "must not be skipped"
        for check in candidate["history_signature_checks"]:
            assert check["status"] == "not_available"
            assert check["status"] != "pass"


def test_warning_event_is_emitted_for_a_candidate_with_warnings(tmp_path, channel_path):
    job_dir = tmp_path / "job-warning-event"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    calls = {"n": 0}

    async def fake_image_fn(*, prompt, project_name, out_path, **kwargs):
        from PIL import Image
        calls["n"] += 1
        # Variant 1 gets a flat, low-contrast image (a real WARN), the rest
        # get distinguishable patterns.
        if calls["n"] == 1:
            Image.new("RGB", (1920, 1080), (120, 120, 120)).save(out_path, format="PNG")
        else:
            _pattern_image(calls["n"]).save(out_path, format="PNG")
        return {"src": "x", "bytes": 9}

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0))

    events = [
        json.loads(line)
        for line in (job_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    warning_events = [e for e in events if e.get("event") == "THUMBNAIL_QA_WARNING"]
    assert any(e["data"]["variant"] == 1 for e in warning_events)
    assert all(e["data"]["warning_count"] > 0 for e in warning_events)


# ── Codex verification round 4: OCR compatibility mode report/event contract
# (design §5.2, §5.3, §11, §13) ─────────────────────────────────────────────

def test_no_ocr_fn_produces_not_run_aggregate_ocr_status(tmp_path, channel_path):
    job_dir = tmp_path / "job-ocr-status-aggregate"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(job_dir, channel_path, _flat_color_image_fn(), throttle_sec=0)
        )

    report = json.loads((job_dir / "json" / "thumbnail_quality_report.json").read_text())
    assert report["ocr_status"] == "not_run"
    assert report["ocr_provider"] == "none"


# ── Codex verification round 5: aggregate ocr_status must reflect actual
# candidate outcomes, not merely whether a provider callable was supplied ──

def test_ocr_provider_that_always_raises_reports_aggregate_not_run(tmp_path, channel_path):
    """A provider present but never actually producing a result must not be
    reported as 'ran' — that would be as false as claiming an OCR pass."""
    job_dir = tmp_path / "job-ocr-always-raises"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )

    def broken_ocr(jpg_path, expected_text):
        raise RuntimeError("OCR provider unavailable")

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(
                job_dir, channel_path, _flat_color_image_fn(), throttle_sec=0,
                thumbnail_ocr_fn=broken_ocr,
            )
        )

    report = json.loads((job_dir / "json" / "thumbnail_quality_report.json").read_text())
    assert report["ocr_provider"] == "injected"
    assert report["ocr_status"] == "not_run"
    for candidate in report["candidates"]:
        assert candidate["ocr_check"]["status"] == "not_run"
    assert report["requires_manual_review"] is True


def test_ocr_provider_that_always_returns_none_reports_aggregate_not_run(tmp_path, channel_path):
    job_dir = tmp_path / "job-ocr-returns-none"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )

    def empty_ocr(jpg_path, expected_text):
        return None

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(
                job_dir, channel_path, _flat_color_image_fn(), throttle_sec=0,
                thumbnail_ocr_fn=empty_ocr,
            )
        )

    report = json.loads((job_dir / "json" / "thumbnail_quality_report.json").read_text())
    assert report["ocr_status"] == "not_run"


def test_ocr_provider_with_mixed_outcomes_reports_aggregate_mixed(tmp_path, channel_path):
    """Variant 1 gets a real OCR result; the rest raise — the aggregate must
    show the partial truth, not collapse to either extreme."""
    job_dir = tmp_path / "job-ocr-mixed"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )

    def mixed_ocr(jpg_path, expected_text):
        from video_agent.qa.thumbnail_package_qa import OcrBox, ThumbnailOcrResult
        if str(jpg_path).endswith("thumbnail_1.jpg"):
            return ThumbnailOcrResult(
                boxes=(OcrBox(text=expected_text, left=0, top=0, width=10, height=10),)
            )
        raise RuntimeError("provider timed out")

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(
                job_dir, channel_path, _flat_color_image_fn(), throttle_sec=0,
                thumbnail_ocr_fn=mixed_ocr,
            )
        )

    report = json.loads((job_dir / "json" / "thumbnail_quality_report.json").read_text())
    assert report["ocr_status"] == "mixed"
    statuses = {c["variant_index"]: c["ocr_check"]["status"] for c in report["candidates"]}
    assert statuses[1] == "pass"
    assert statuses[2] == "not_run"
    assert statuses[3] == "not_run"


def test_no_ocr_fn_emits_a_warning_event_with_actionable_reason(tmp_path, channel_path):
    """§5.2: OCR-unavailable compatibility mode must produce a warning event,
    not silence — even when nothing else about the candidate is questionable."""
    job_dir = tmp_path / "job-ocr-warning-event"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(job_dir, channel_path, _flat_color_image_fn(), throttle_sec=0)
        )

    events = [
        json.loads(line)
        for line in (job_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    warning_events = [e for e in events if e.get("event") == "THUMBNAIL_QA_WARNING"]
    assert warning_events, "OCR-unavailable compatibility mode must still warn"
    for event in warning_events:
        assert event["data"]["reason_codes"], "warning event must carry an actionable reason"
        assert "ocr_not_run" in event["data"]["reason_codes"]


def test_no_ocr_fn_primary_selected_event_states_requires_manual_review(tmp_path, channel_path):
    job_dir = tmp_path / "job-ocr-manual-review-event"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(job_dir, channel_path, _flat_color_image_fn(), throttle_sec=0)
        )

    events = [
        json.loads(line)
        for line in (job_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected = next(e for e in events if e.get("event") == "THUMBNAIL_PRIMARY_SELECTED")
    assert selected["data"]["requires_manual_review"] is True


def test_history_only_partial_match_warning_has_non_empty_reason_codes(tmp_path, channel_path):
    """A history WARN (only image OR only signature insufficient, not both)
    must still surface an actionable reason — not silently pad warning_count."""
    baseline_job = tmp_path / "job-partial-baseline"
    _seed_three_variants(
        baseline_job, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(baseline_job, channel_path, _flat_color_image_fn(), throttle_sec=0)
        )
    baseline_plans = json.loads((baseline_job / "json" / "thumbnail_prompt_plans.json").read_text())
    baseline_face_plan = next(p for p in baseline_plans if p["visual_strategy"] == "face_driven")
    face_index = baseline_face_plan["variant_index"]
    baseline_face_bytes = (baseline_job / "outputs" / f"thumbnail_{face_index}.jpg").read_bytes()

    # Sibling with an IMAGE-identical primary but a signature that already
    # differs enough (>=3 dims) — image-only insufficiency, a WARN not a FAIL.
    sibling = tmp_path / "sibling-partial"
    (sibling / "json").mkdir(parents=True)
    (sibling / "json" / "seo.json").write_text(json.dumps({"job_id": "sibling-partial"}), encoding="utf-8")
    (sibling / "outputs").mkdir(parents=True)
    (sibling / "outputs" / "thumbnail.jpg").write_bytes(baseline_face_bytes)
    diverse_signature = dict(baseline_face_plan["concept_signature"])
    diverse_signature.update(
        setting_family="bedroom", action_archetype="side_by_side_contrast", emotion_mode="quiet_determination"
    )
    (sibling / "json" / "thumbnail_quality_report.json").write_text(
        json.dumps({"selected_signature": diverse_signature}), encoding="utf-8"
    )

    adjusted_job = tmp_path / "job-partial-adjusted"
    _seed_three_variants(
        adjusted_job, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )

    def passing_ocr(jpg_path, expected_text):
        from video_agent.qa.thumbnail_package_qa import OcrBox, ThumbnailOcrResult
        return ThumbnailOcrResult(boxes=(OcrBox(text=expected_text, left=0, top=0, width=10, height=10),))

    # Isolate the history-only warning: with OCR passing, "ocr_not_run" can't
    # piggyback a non-empty reason_codes onto an otherwise-silent warning.
    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(
            auto_thumbnail_image_stage(
                adjusted_job, channel_path, _flat_color_image_fn(), throttle_sec=0,
                thumbnail_ocr_fn=passing_ocr,
            )
        )

    report = json.loads((adjusted_job / "json" / "thumbnail_quality_report.json").read_text())
    face_candidate = next(c for c in report["candidates"] if c["variant_index"] == face_index)
    combined = next(
        c for c in face_candidate["history_combined_checks"] if c["path"].endswith("thumbnail.jpg")
    )
    assert combined["status"] == "warning"

    events = [
        json.loads(line)
        for line in (adjusted_job / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    face_warning = next(
        e for e in events
        if e.get("event") == "THUMBNAIL_QA_WARNING" and e["data"]["variant"] == face_index
    )
    assert face_warning["data"]["reason_codes"]
    assert "ocr_not_run" not in face_warning["data"]["reason_codes"]
    assert any(code.startswith("history_partial_match:") for code in face_warning["data"]["reason_codes"])


def test_primary_selected_event_carries_the_full_required_payload(tmp_path, channel_path):
    job_dir = tmp_path / "job-primary-selected-payload"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    fake_image_fn = _flat_color_image_fn({1: (10, 10, 10), 2: (200, 30, 30), 3: (10, 200, 10)})

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0))

    events = [
        json.loads(line)
        for line in (job_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected = next(e for e in events if e.get("event") == "THUMBNAIL_PRIMARY_SELECTED")
    data = selected["data"]
    for key in (
        "variant", "package_score", "visual_score", "final_score",
        "history_item_count", "ocr_status", "warning_count", "reason_codes",
    ):
        assert key in data, key


def test_quality_report_includes_required_aggregate_fields(tmp_path, channel_path):
    job_dir = tmp_path / "job-aggregate-fields"
    _seed_three_variants(
        job_dir, [_STRONG_TITLE_VARIANT, _WEAK_TITLE_VARIANT, _WEAK_TITLE_VARIANT_2]
    )
    fake_image_fn = _flat_color_image_fn({1: (10, 10, 10), 2: (200, 30, 30), 3: (10, 200, 10)})

    with patch("video_agent.contracts.repo_root", return_value=tmp_path):
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, fake_image_fn, throttle_sec=0))

    report = json.loads((job_dir / "json" / "thumbnail_quality_report.json").read_text())
    for key in ("history_items", "selected_path", "selection_reason", "ocr_provider", "thresholds"):
        assert key in report, key
    assert report["ocr_provider"] == "none"
    assert set(report["thresholds"]) == {
        "sibling_dhash_max", "history_dhash_max",
        "sibling_signature_min_differences", "history_signature_min_differences",
    }


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
