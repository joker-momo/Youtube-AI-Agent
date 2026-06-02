from __future__ import annotations

import json

from video_agent.operator import (
    _chatgpt_scenes_batch_prompt,
    _chatgpt_scenes_plan_prompt,
    _gemini_scenes_qa_batch_prompt,
    _chatgpt_script_prompt,
    _chatgpt_seo_prompt,
    _json_file_directive,
    extract_json_objects,
)
from video_agent.operator_shards import extract_json_envelope


def test_directive_contains_filename_and_single_block_rule():
    d = _json_file_directive("scenes_batch_01.json")
    assert "// FILE: scenes_batch_01.json" in d
    assert "```json" in d
    assert "NEVER truncate" in d


def test_extractor_ignores_fence_and_file_marker():
    obj = {"artifact_type": "scenes_batch", "data": {"scenes": []}}
    raw = "```json\n// FILE: scenes_batch_01.json\n" + json.dumps(obj) + "\n```"
    got = extract_json_objects(raw)
    assert got and got[-1]["artifact_type"] == "scenes_batch"


def test_envelope_extractor_handles_fence_and_marker():
    obj = {
        "artifact_type": "scenes_batch",
        "schema_version": "2026-05-json-shards-v1",
        "job_id": "job-a",
        "channel_id": "vida-plena-45",
        "status": "complete",
        "data": {"scenes": []},
        "warnings": [],
    }
    raw = "```json\n// FILE: scenes_batch_02.json\n" + json.dumps(obj) + "\n```"
    assert extract_json_envelope(raw)["artifact_type"] == "scenes_batch"


def test_scenes_plan_prompt_requests_named_file():
    p = _chatgpt_scenes_plan_prompt({}, {"job_id": "job-a"})
    assert "// FILE: scenes_plan.json" in p


def test_scenes_batch_prompt_requests_named_file_with_index():
    p = _chatgpt_scenes_batch_prompt({}, {"job_id": "job-a"}, {"data": {"batches": [1, 2]}}, {"batch_index": 3})
    assert "// FILE: scenes_batch_03.json" in p


def test_scenes_qa_batch_prompt_requests_named_file_with_index():
    p = _gemini_scenes_qa_batch_prompt({}, {"job_id": "job-a"}, 2, 8)
    assert "// FILE: scenes_qa_batch_02.json" in p


def test_script_prompt_requests_named_file():
    p = _chatgpt_script_prompt({}, {"title_seed": "x"})
    assert "// FILE: script.json" in p


def test_seo_prompt_requests_named_file():
    p = _chatgpt_seo_prompt({}, {"job_id": "job-a"}, {"scenes": []})
    assert "// FILE: seo.json" in p
