"""Unit tests for the per-job ChatGPT image-prompt audit log.

Every ChatGPT image-generation prompt (long-form thumbnail, generated scene
asset, graphic/card) must be recorded exactly and completely under
``job_dir/operator/chatgpt/image_prompts/`` so a human can inspect and compare
the prompts sent to ChatGPT after a run. The logger must never truncate the
prompt and must never lose a previously recorded prompt when appending.
"""

from __future__ import annotations

import json
from pathlib import Path

from video_agent.orchestrator.image_prompt_log import (
    IMAGE_PROMPT_LOG_DIR,
    log_image_prompt,
    read_image_prompt_index,
)


def _read_index(job_dir: Path) -> list[dict]:
    return read_image_prompt_index(job_dir)


def test_records_exact_full_prompt_without_truncation(tmp_path):
    job_dir = tmp_path / "job"
    # A deliberately huge prompt with unicode + newlines + code fences: the log
    # must preserve every byte, not summarise or truncate.
    prompt = "PROMPT\n" + ("níño áéíóú ¿¡ 段落 ```fence``` " * 500) + "\nEND"

    log_path = log_image_prompt(
        job_dir,
        stage="graphic_images",
        kind="graphic",
        prompt=prompt,
        scene_id="scene-07",
        out_path="assets/graphic-scene-07.png",
    )

    # Index record carries the exact prompt (JSON is lossless).
    index = _read_index(job_dir)
    assert len(index) == 1
    rec = index[0]
    assert rec["prompt"] == prompt
    assert rec["prompt_chars"] == len(prompt)
    assert rec["stage"] == "graphic_images"
    assert rec["kind"] == "graphic"
    assert rec["scene_id"] == "scene-07"
    assert rec["out_path"] == "assets/graphic-scene-07.png"
    assert rec["created_at"].endswith("Z")
    assert len(rec["prompt_sha256"]) == 64

    # The human-readable per-prompt file also holds the full prompt verbatim.
    assert log_path.exists()
    md = log_path.read_text(encoding="utf-8")
    assert prompt in md


def test_appends_without_losing_prior_records(tmp_path):
    job_dir = tmp_path / "job"
    log_image_prompt(job_dir, stage="thumbnail_image", kind="thumbnail", prompt="first", index=1)
    log_image_prompt(job_dir, stage="assets_chatgpt", kind="scene_asset", prompt="second", scene_id="s2")
    log_image_prompt(job_dir, stage="graphic_images", kind="graphic", prompt="third", scene_id="s3")

    index = _read_index(job_dir)
    assert [r["prompt"] for r in index] == ["first", "second", "third"]
    assert [r["seq"] for r in index] == [1, 2, 3]
    # One markdown artifact per prompt, all distinct.
    log_dir = job_dir / IMAGE_PROMPT_LOG_DIR
    md_files = sorted(p.name for p in log_dir.glob("*.md"))
    assert len(md_files) == 3
    assert len(set(md_files)) == 3


def test_index_is_valid_jsonl(tmp_path):
    job_dir = tmp_path / "job"
    log_image_prompt(job_dir, stage="thumbnail_image", kind="thumbnail", prompt="a", index=1)
    log_image_prompt(job_dir, stage="thumbnail_image", kind="thumbnail", prompt="b", index=2)
    index_path = job_dir / IMAGE_PROMPT_LOG_DIR / "index.jsonl"
    lines = [ln for ln in index_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
    for ln in lines:
        json.loads(ln)  # each line is standalone JSON


def test_optional_metadata_recorded(tmp_path):
    job_dir = tmp_path / "job"
    log_image_prompt(
        job_dir,
        stage="thumbnail_image",
        kind="thumbnail",
        prompt="p",
        index=2,
        strategy="object_driven",
        project_name="job-thumb2",
        aspect_ratio="16:9",
    )
    rec = _read_index(job_dir)[0]
    assert rec["index"] == 2
    assert rec["strategy"] == "object_driven"
    assert rec["project_name"] == "job-thumb2"
    assert rec["aspect_ratio"] == "16:9"
