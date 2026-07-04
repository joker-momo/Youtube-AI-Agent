"""Tests for the long-form ``graphic_images`` stage.

Generates a ChatGPT image for each graphic-layout scene (checklist/warning/quote/
cta) and records ``scene.graphic.image_ref``. Non-graphic scenes are untouched; a
single image failure is non-fatal (the scene falls back, the pipeline continues).
The image generator is injected so tests run without the live browser provider.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from video_agent.color_mix import (
    resolve_topic_accent_color,
    resolve_topic_background_color,
    resolve_topic_text_color,
)
from video_agent.orchestrator.orchestrator import create_job
from video_agent.orchestrator.stages.graphic_images import run_graphic_images_stage
from video_agent.style_dna import DEFAULT_STYLE as _DEFAULT_STYLE


@pytest.fixture(autouse=True)
def _no_late_recovery_wait(monkeypatch):
    """Tests must not sit in the 180s late-recovery window on failures."""
    monkeypatch.setenv("GRAPHIC_LATE_RECOVERY_WINDOW_SEC", "0")


def _make_job(tmp_path: Path, scenes: list[dict], *, topic_accent_color: str | None = None) -> Path:
    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    (job_dir / "json" / "scenes.json").write_text(json.dumps({"job_id": "j1", "scenes": scenes}))
    if topic_accent_color:
        (job_dir / "json" / "seo.json").write_text(
            json.dumps({"topic_accent_color": topic_accent_color})
        )
    create_job(job_dir, "j1", "vida-plena-45", "json/idea.json", stages=["graphic_images"])
    return job_dir


def _fake_image_fn(written: list[str]):
    async def _fn(*, prompt: str, project_name: str, out_path: str):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG\r\n")  # minimal stand-in
        written.append(out_path)
        return {"src": "https://chatgpt/img", "bytes": 6}
    return _fn


def test_generates_images_for_graphic_scenes_only(tmp_path):
    scenes = [
        {"id": "scene-01", "layout": "hook", "visual_prompt": "p", "asset_refs": {"background": "a.mp4"}},
        {"id": "scene-02", "layout": "checklist", "graphic": {"needed": True, "prompt": "a checklist"}},
        {"id": "scene-03", "layout": "subtitle", "visual_prompt": "p"},
        {"id": "scene-04", "layout": "cta", "graphic": {"needed": True, "prompt": "a cta"}},
    ]
    job_dir = _make_job(tmp_path, scenes)
    written: list[str] = []
    out = asyncio.run(run_graphic_images_stage(job_dir, None, _fake_image_fn(written)))
    doc = json.loads(out.read_text())["scenes"]
    by_id = {s["id"]: s for s in doc}
    assert by_id["scene-02"]["graphic"]["image_ref"] == "jobs/j1/assets/graphic-scene-02.png"
    assert by_id["scene-04"]["graphic"]["image_ref"] == "jobs/j1/assets/graphic-scene-04.png"
    # hook is now a graphic layout too (gen image, attention-grabbing) — gets an image
    assert by_id["scene-01"]["graphic"]["image_ref"] == "jobs/j1/assets/graphic-scene-01.png"
    # subtitle (non-graphic) gets no graphic image
    assert "graphic" not in by_id["scene-03"] or "image_ref" not in by_id["scene-03"].get("graphic", {})
    assert len(written) == 3


def test_graphic_needed_false_is_skipped(tmp_path):
    scenes = [{"id": "scene-01", "layout": "warning", "graphic": {"needed": False, "prompt": "x"}}]
    job_dir = _make_job(tmp_path, scenes)
    written: list[str] = []
    out = asyncio.run(run_graphic_images_stage(job_dir, None, _fake_image_fn(written)))
    doc = json.loads(out.read_text())["scenes"]
    assert "image_ref" not in doc[0].get("graphic", {})
    assert written == []


def test_image_failure_is_non_fatal(tmp_path):
    scenes = [
        {"id": "scene-01", "layout": "checklist", "graphic": {"needed": True, "prompt": "boom"}},
        {"id": "scene-02", "layout": "quote", "graphic": {"needed": True, "prompt": "ok"}},
    ]
    job_dir = _make_job(tmp_path, scenes)
    written: list[str] = []
    good = _fake_image_fn(written)

    async def _flaky(*, prompt, project_name, out_path):
        if "boom" in prompt:
            raise RuntimeError("provider error")
        return await good(prompt=prompt, project_name=project_name, out_path=out_path)

    out = asyncio.run(run_graphic_images_stage(job_dir, None, _flaky))
    doc = json.loads(out.read_text())["scenes"]
    by_id = {s["id"]: s for s in doc}
    assert "image_ref" not in by_id["scene-01"].get("graphic", {})  # failed, fell back
    assert by_id["scene-02"]["graphic"]["image_ref"] == "jobs/j1/assets/graphic-scene-02.png"  # other scene ok


def test_topic_accent_color_flows_into_prompt_as_visible_treatment(tmp_path):
    """bug-465: the per-video topic accent must read in the prompt as a real,
    visible design element (ribbon/border/section fill), not the old wording
    that confined it to 'ONLY as a small accent (one word, an underline, or a
    marker icon)' -- which made every card collapse to the same fixed brand
    green/cream regardless of topic_accent_color."""
    scenes = [{
        "id": "scene-01", "layout": "checklist", "caption": "Reduce el azucar",
        "graphic": {"needed": True, "prompt": "x"},
    }]
    job_dir = _make_job(tmp_path, scenes, topic_accent_color="#A47A3F")
    captured_prompts: list[str] = []

    async def _capture(*, prompt: str, project_name: str, out_path: str):
        captured_prompts.append(prompt)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG\r\n")
        return {"src": "https://chatgpt/img", "bytes": 6}

    asyncio.run(run_graphic_images_stage(job_dir, None, _capture))

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    # bug-466: the RAW topic hex is no longer used as-is -- it's blended into the
    # channel's own brand anchor (OKLab) first, so a clashing topic colour can't
    # ship raw. The blended resolved_accent_color is what should reach the prompt.
    resolved = resolve_topic_accent_color(_DEFAULT_STYLE["palette"]["accent"], "#A47A3F")
    assert resolved["resolved_accent_color"] in prompt
    assert "ONLY as a small accent" not in prompt  # the old tiny-marker wording is gone
    # must instruct a real visible surface for the accent, not a marginal touch...
    assert any(kw in prompt for kw in ("rule", "underline", "border", "tag", "label"))
    # ...but a REFINED one -- user feedback (2026-07-03) on the first real
    # regeneration: a bold solid ribbon/banner block made the card look heavy/
    # cheap and cropped the background photo more than the original. The
    # instruction must now explicitly rule that out.
    assert "NEVER a bold solid block, banner, or ribbon" in prompt
    assert "must NOT crop or crowd the background photo" in prompt


def test_graphic_metadata_persisted_with_prompt_hash_and_palette(tmp_path):
    """bug-465/466: per-scene graphic metadata (effective palette + prompt hash
    + colour resolution) must be persisted so a future run can detect whether
    the prompt/palette that generated the cached PNG has since changed."""
    scenes = [{
        "id": "scene-01", "layout": "checklist", "caption": "Reduce el azucar",
        "graphic": {"needed": True, "prompt": "x"},
    }]
    job_dir = _make_job(tmp_path, scenes, topic_accent_color="#A47A3F")
    written: list[str] = []

    out = asyncio.run(run_graphic_images_stage(job_dir, None, _fake_image_fn(written)))

    graphic = json.loads(out.read_text())["scenes"][0]["graphic"]
    assert isinstance(graphic.get("prompt_hash"), str) and len(graphic["prompt_hash"]) > 0
    resolved = resolve_topic_accent_color(_DEFAULT_STYLE["palette"]["accent"], "#A47A3F")
    assert graphic["raw_topic_accent_color"] == "#A47A3F"
    assert graphic["brand_anchor_color"] == _DEFAULT_STYLE["palette"]["accent"]
    assert graphic["resolved_accent_color"] == resolved["resolved_accent_color"]
    assert graphic["mix_ratio"] == 0.3
    assert graphic.get("effective_palette", {}).get("accent") == resolved["resolved_accent_color"]

    # bug-467: background/text now flex per-video too, reusing the same raw
    # topic accent hex but blended much lighter (12%) so the card stays close
    # to the channel's cream/text identity.
    bg_resolved = resolve_topic_background_color(_DEFAULT_STYLE["palette"]["background"], "#A47A3F")
    text_resolved = resolve_topic_text_color(_DEFAULT_STYLE["palette"]["text"], "#A47A3F")
    assert graphic["brand_background_color"] == _DEFAULT_STYLE["palette"]["background"]
    assert graphic["resolved_background_color"] == bg_resolved["resolved_background_color"]
    assert graphic["background_mix_ratio"] == 0.12
    assert graphic["brand_text_color"] == _DEFAULT_STYLE["palette"]["text"]
    assert graphic["resolved_text_color"] == text_resolved["resolved_text_color"]
    assert graphic["text_mix_ratio"] == 0.12
    assert graphic.get("effective_palette", {}).get("background") == bg_resolved["resolved_background_color"]
    assert graphic.get("effective_palette", {}).get("text") == text_resolved["resolved_text_color"]


def test_stale_prompt_hash_forces_regeneration(tmp_path):
    """bug-465: a cached PNG generated under a DIFFERENT topic_accent_color
    must be regenerated, not silently reused with its stale colour treatment,
    when the prompt hash it was generated from no longer matches."""
    scenes = [{
        "id": "scene-01", "layout": "checklist", "caption": "Reduce el azucar",
        "graphic": {"needed": True, "prompt": "x"},
    }]
    job_dir = _make_job(tmp_path, scenes, topic_accent_color="#A47A3F")
    written: list[str] = []
    asyncio.run(run_graphic_images_stage(job_dir, None, _fake_image_fn(written)))
    assert len(written) == 1
    first_doc = json.loads((job_dir / "json" / "scenes.json").read_text())
    first_hash = first_doc["scenes"][0]["graphic"]["prompt_hash"]

    # Simulate a re-run of the stage after seo.topic_accent_color changed --
    # job.json must allow re-entering the stage, so reset its status.
    job_state = json.loads((job_dir / "job.json").read_text())
    for s in job_state["stages"]:
        if s["name"] == "graphic_images":
            s["status"] = "pending"
    (job_dir / "job.json").write_text(json.dumps(job_state))
    (job_dir / "json" / "seo.json").write_text(json.dumps({"topic_accent_color": "#2F6B57"}))

    asyncio.run(run_graphic_images_stage(job_dir, None, _fake_image_fn(written)))

    assert len(written) == 2  # regenerated, not reused
    second_doc = json.loads((job_dir / "json" / "scenes.json").read_text())
    second_graphic = second_doc["scenes"][0]["graphic"]
    second_hash = second_graphic["prompt_hash"]
    assert second_hash != first_hash
    resolved = resolve_topic_accent_color(_DEFAULT_STYLE["palette"]["accent"], "#2F6B57")
    assert second_graphic["raw_topic_accent_color"] == "#2F6B57"
    assert second_graphic["resolved_accent_color"] == resolved["resolved_accent_color"]
    assert second_graphic["effective_palette"]["accent"] == resolved["resolved_accent_color"]


def test_missing_prompt_hash_forces_regeneration(tmp_path):
    """bug-468: a real incident (2026-07-03) on a production job -- a scene
    whose cached PNG predates the prompt_hash feature (no stored hash at all,
    not just a mismatched one) was silently REUSED with its old pixels while
    scenes.json's resolved_accent/background/text_color fields were
    overwritten to reflect the CURRENT run's colours -- metadata claimed a
    colour treatment the actual image never got. This stage only re-enters a
    job when a human deliberately resets it (job.json otherwise skips
    "completed" stages), so a missing hash must be treated as stale too, not
    as an exemption from regeneration."""
    scenes = [{
        "id": "scene-01", "layout": "checklist", "caption": "Reduce el azucar",
        "graphic": {"needed": True, "prompt": "x"},
    }]
    job_dir = _make_job(tmp_path, scenes, topic_accent_color="#A47A3F")

    # Simulate a pre-bug-465 cached PNG: file exists on disk, but scenes.json
    # has no prompt_hash for it at all (the field never existed before bug-465).
    out_path = job_dir / "assets" / "graphic-scene-01.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"\x89PNG\r\n" + b"OLD-PRE-BUG-465-PIXELS")
    scenes_doc = json.loads((job_dir / "json" / "scenes.json").read_text())
    scenes_doc["scenes"][0]["graphic"] = {"needed": True, "prompt": "x"}  # no prompt_hash key
    (job_dir / "json" / "scenes.json").write_text(json.dumps(scenes_doc))

    written: list[str] = []
    asyncio.run(run_graphic_images_stage(job_dir, None, _fake_image_fn(written)))

    assert written == [str(out_path)]  # regenerated, not silently reused
    graphic = json.loads((job_dir / "json" / "scenes.json").read_text())["scenes"][0]["graphic"]
    assert isinstance(graphic.get("prompt_hash"), str) and len(graphic["prompt_hash"]) > 0


def test_late_written_graphic_is_adopted_after_client_failure(tmp_path, monkeypatch):
    """Codex 20260704-130051 (production scene-27): the browser-worker often
    finishes and writes the PNG minutes AFTER the client-side request already
    failed (observed lag 123s). The end-of-stage sweep must adopt that
    late-landed file instead of abandoning a finished card."""
    monkeypatch.setenv("GRAPHIC_LATE_RECOVERY_WINDOW_SEC", "5")
    scenes = [{
        "id": "scene-01", "layout": "recipe_snapshot", "caption": "Plato equilibrado",
        "graphic": {"needed": True, "prompt": "x"},
    }]
    job_dir = _make_job(tmp_path, scenes)
    out_path = job_dir / "assets" / "graphic-scene-01.png"

    async def _fails_but_writes_late(*, prompt, project_name, out_path):
        # Simulate the real browser-worker: the HTTP request errors client-side,
        # but the server-side generation completes and writes the file anyway.
        async def _late_write():
            await asyncio.sleep(0.3)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"\x89PNG\r\n" + b"LATE-SERVER-WRITE")
        asyncio.get_running_loop().create_task(_late_write())
        raise RuntimeError("browser-worker chatgpt/image request failed: ")

    out = asyncio.run(run_graphic_images_stage(job_dir, None, _fails_but_writes_late))

    graphic = json.loads(out.read_text())["scenes"][0]["graphic"]
    assert graphic.get("image_ref") == "jobs/j1/assets/graphic-scene-01.png"
    assert not graphic.get("failed")
    assert out_path.exists() and out_path.stat().st_size > 0


def test_unrecovered_failure_stamps_marker_and_removes_stale_orphan(tmp_path):
    """When generation truly fails (nothing lands within the recovery window):
    (a) a pre-existing STALE png (the reason regeneration was attempted) must
    be deleted so audits never find an orphan file unreferenced by metadata;
    (b) the scene must carry an explicit graphic.failed marker so visual
    review can flag the lost card instead of a silent downgrade."""
    scenes = [{
        "id": "scene-01", "layout": "recipe_snapshot", "caption": "Plato equilibrado",
        "graphic": {"needed": True, "prompt": "x"},  # no prompt_hash -> stale
    }]
    job_dir = _make_job(tmp_path, scenes)
    out_path = job_dir / "assets" / "graphic-scene-01.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"\x89PNG\r\n" + b"STALE-OLD-PROMPT-PIXELS")

    async def _always_fails(*, prompt, project_name, out_path):
        raise RuntimeError("browser-worker chatgpt/image request failed: ")

    out = asyncio.run(run_graphic_images_stage(job_dir, None, _always_fails))

    graphic = json.loads(out.read_text())["scenes"][0]["graphic"]
    assert graphic.get("failed") is True
    assert "failed" in str(graphic.get("error"))
    assert not graphic.get("image_ref")
    assert not out_path.exists()  # stale orphan removed
