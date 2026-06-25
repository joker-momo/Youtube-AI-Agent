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

from video_agent.orchestrator.orchestrator import create_job
from video_agent.orchestrator.stages.graphic_images import run_graphic_images_stage


def _make_job(tmp_path: Path, scenes: list[dict]) -> Path:
    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    (job_dir / "json" / "scenes.json").write_text(json.dumps({"job_id": "j1", "scenes": scenes}))
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
    assert by_id["scene-02"]["graphic"]["image_ref"] == "assets/graphic-scene-02.png"
    assert by_id["scene-04"]["graphic"]["image_ref"] == "assets/graphic-scene-04.png"
    # non-graphic scenes get no graphic image
    assert "graphic" not in by_id["scene-01"] or "image_ref" not in by_id["scene-01"].get("graphic", {})
    assert "graphic" not in by_id["scene-03"] or "image_ref" not in by_id["scene-03"].get("graphic", {})
    assert len(written) == 2


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
    assert by_id["scene-02"]["graphic"]["image_ref"] == "assets/graphic-scene-02.png"  # other scene ok
