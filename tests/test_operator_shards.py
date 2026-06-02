from __future__ import annotations

import json

import pytest

from video_agent.operator_shards import (
    SCHEMA_VERSION,
    ShardValidationError,
    extract_json_envelope,
    merge_scene_batches,
    merge_scenes_qa_batches,
    validate_envelope,
    validate_scenes_batch,
    validate_scenes_plan,
)
from video_agent.operator import (
    _chatgpt_scenes_batch_prompt,
    _chatgpt_scenes_plan_prompt,
    _claude_scenes_qa_batch_prompt,
)


def _envelope(**overrides):
    payload = {
        "artifact_type": "scenes_batch",
        "schema_version": SCHEMA_VERSION,
        "job_id": "job-a",
        "channel_id": "vida-plena-45",
        "status": "complete",
        "batch_index": 1,
        "batch_total": 1,
        "data": {},
        "warnings": [],
    }
    payload.update(overrides)
    return payload


def _scene(scene_id: str, duration: int = 5) -> dict:
    return {
        "id": scene_id,
        "duration_sec": duration,
        "narration": f"Narration {scene_id}",
        "on_screen_text": "TEXTO",
        "caption": "Caption",
        "visual_prompt": "A calm wellness scene",
        "motion": "slow_zoom",
        "asset_refs": {},
    }


def test_extract_json_envelope_from_raw_text():
    raw = "```json\n" + json.dumps(_envelope()) + "\n```"
    assert extract_json_envelope(raw)["artifact_type"] == "scenes_batch"


def test_extract_json_envelope_prefers_envelope_when_truncated_with_inner_scenes():
    # ChatGPT truncates a long scenes_batch mid-stream: the inner scene objects
    # are complete and parse individually, but the outer envelope never closes.
    # extract_json_objects then yields the inner scenes plus a repaired root.
    # The envelope (the object carrying artifact_type) must win, not the last
    # complete inner scene object.
    full = _envelope(
        data={
            "scene_start": "scene-01",
            "scene_end": "scene-02",
            "scenes": [_scene("scene-01"), _scene("scene-02")],
        }
    )
    raw = json.dumps(full)
    truncated = raw[: raw.rfind("]")]  # cut before scenes-array close + trailing braces

    env = extract_json_envelope(truncated)

    assert env.get("artifact_type") == "scenes_batch"
    assert env.get("job_id") == "job-a"


def test_validate_envelope_rejects_wrong_job_id():
    env = _envelope(job_id="other")
    with pytest.raises(ShardValidationError, match="job_id"):
        validate_envelope(
            env,
            expected_artifact_type="scenes_batch",
            expected_job_id="job-a",
            expected_channel_id="vida-plena-45",
        )


def test_validate_envelope_rejects_wrong_artifact_type():
    env = _envelope(artifact_type="script")
    with pytest.raises(ShardValidationError, match="artifact_type"):
        validate_envelope(
            env,
            expected_artifact_type="scenes_batch",
            expected_job_id="job-a",
            expected_channel_id="vida-plena-45",
        )


def test_validate_envelope_rejects_partial_status():
    env = _envelope(status="partial", next_batch_hint="continue")
    with pytest.raises(ShardValidationError, match="partial"):
        validate_envelope(
            env,
            expected_artifact_type="scenes_batch",
            expected_job_id="job-a",
            expected_channel_id="vida-plena-45",
        )


def test_validate_scenes_plan_accepts_valid_plan():
    plan = _envelope(
        artifact_type="scenes_plan",
        batch_index=None,
        batch_total=None,
        data={
            "target_scene_count": 2,
            "target_total_duration_sec": 10,
            "batch_size": 2,
            "batches": [
                {
                    "batch_index": 1,
                    "scene_start": "scene-01",
                    "scene_end": "scene-02",
                    "purpose": "opening",
                    "script_sections": ["section-01"],
                }
            ],
        },
    )
    validate_scenes_plan(plan)


def test_validate_scenes_plan_rejects_non_contiguous_ranges():
    plan = _envelope(
        artifact_type="scenes_plan",
        batch_index=None,
        batch_total=None,
        data={
            "target_scene_count": 3,
            "target_total_duration_sec": 15,
            "batch_size": 2,
            "batches": [
                {
                    "batch_index": 1,
                    "scene_start": "scene-01",
                    "scene_end": "scene-01",
                    "purpose": "opening",
                    "script_sections": ["section-01"],
                },
                {
                    "batch_index": 2,
                    "scene_start": "scene-03",
                    "scene_end": "scene-03",
                    "purpose": "skip scene two",
                    "script_sections": ["section-01"],
                },
            ],
        },
    )

    with pytest.raises(ShardValidationError, match="contiguous"):
        validate_scenes_plan(plan)


def test_validate_scenes_batch_accepts_valid_batch():
    batch = _envelope(data={"scenes": [_scene("scene-01"), _scene("scene-02")]})
    validate_scenes_batch(
        batch,
        expected_batch_index=1,
        expected_batch_total=1,
        scene_start="scene-01",
        scene_end="scene-02",
    )


def test_validate_scenes_batch_rejects_wrong_range():
    batch = _envelope(data={"scenes": [_scene("scene-02"), _scene("scene-03")]})
    with pytest.raises(ShardValidationError, match="scene-01"):
        validate_scenes_batch(
            batch,
            expected_batch_index=1,
            expected_batch_total=1,
            scene_start="scene-01",
            scene_end="scene-02",
        )


def test_merge_scene_batches_rejects_duplicate_scene_ids():
    first = _envelope(data={"scenes": [_scene("scene-01")]})
    second = _envelope(batch_index=2, batch_total=2, data={"scenes": [_scene("scene-01")]})
    first["batch_total"] = 2

    with pytest.raises(ShardValidationError, match="Duplicate"):
        merge_scene_batches(
            job_id="job-a",
            channel_id="vida-plena-45",
            batch_envelopes=[first, second],
        )


def test_merge_scene_batches_produces_canonical_scenes_doc():
    first = _envelope(batch_total=2, data={"scenes": [_scene("scene-01", 4)]})
    second = _envelope(batch_index=2, batch_total=2, data={"scenes": [_scene("scene-02", 6)]})

    merged = merge_scene_batches(
        job_id="job-a",
        channel_id="vida-plena-45",
        batch_envelopes=[second, first],
    )

    assert [scene["id"] for scene in merged["scenes"]] == ["scene-01", "scene-02"]
    assert merged["total_duration_sec"] == 10
    assert merged["qa"]["verdict"] == "PENDING_CLAUDE_QA"


def test_merge_scene_batches_rejects_schema_invalid_final_doc():
    batch = _envelope(
        data={
            "scenes": [
                {
                    **_scene("scene-01", 4.5),
                }
            ]
        }
    )

    with pytest.raises(ShardValidationError, match="final scenes"):
        merge_scene_batches(
            job_id="job-a",
            channel_id="vida-plena-45",
            batch_envelopes=[batch],
        )


def test_merge_scene_batches_applies_retention_layout_planner_with_script_cta():
    first = _envelope(
        data={
            "scenes": [
                {
                    **_scene("scene-01", 5),
                    "layout": "checklist",
                    "layout_payload": {"title": "BAD", "body": "", "bullets": ["Proteína"], "cta": ""},
                }
            ]
        }
    )
    second = _envelope(
        batch_index=2,
        batch_total=2,
        data={"scenes": [{**_scene("scene-02", 5), "layout": "subtitle"}]},
    )
    first["batch_total"] = 2

    merged = merge_scene_batches(
        job_id="job-a",
        channel_id="vida-plena-45",
        batch_envelopes=[first, second],
        script={"cta": "Prueba esta rutina esta noche."},
    )

    assert merged["scenes"][0]["layout"] == "subtitle"
    assert merged["scenes"][0]["planner_warnings"]
    assert merged["scenes"][1]["layout"] == "cta"
    assert merged["scenes"][1]["layout_payload"]["cta"] == "Prueba esta rutina esta noche."


def test_merge_scenes_qa_batches_passes_when_all_pass():
    qa = _envelope(
        artifact_type="scenes_qa_batch",
        data={
            "verdict": "PASS",
            "youtube_policy": {"compliant": True, "risk_level": "none", "violations": []},
            "issues": [],
            "required_changes": [],
            "scores": {"schema_fit": 5, "safety": 5},
        },
    )

    merged = merge_scenes_qa_batches(
        job_id="job-a",
        channel_id="vida-plena-45",
        qa_batch_envelopes=[qa],
    )

    assert merged["artifact"] == "scenes"
    assert merged["verdict"] == "PASS"
    assert merged["youtube_policy"]["compliant"] is True


def test_merge_scenes_qa_batches_needs_rework_when_any_batch_fails():
    good = _envelope(
        artifact_type="scenes_qa_batch",
        batch_total=2,
        data={
            "verdict": "PASS",
            "youtube_policy": {"compliant": True, "risk_level": "none", "violations": []},
            "issues": [],
            "required_changes": [],
            "scores": {"schema_fit": 5, "safety": 5},
        },
    )
    bad = _envelope(
        artifact_type="scenes_qa_batch",
        batch_index=2,
        batch_total=2,
        data={
            "verdict": "NEEDS_REWORK",
            "youtube_policy": {"compliant": False, "risk_level": "medium", "violations": ["claim"]},
            "issues": ["too strong"],
            "required_changes": ["soften claim"],
            "scores": {"schema_fit": 4, "safety": 2},
        },
    )

    merged = merge_scenes_qa_batches(
        job_id="job-a",
        channel_id="vida-plena-45",
        qa_batch_envelopes=[good, bad],
    )

    assert merged["verdict"] == "NEEDS_REWORK"
    assert merged["youtube_policy"]["compliant"] is False
    assert merged["youtube_policy"]["risk_level"] == "medium"
    assert merged["issues"] == ["too strong"]
    assert merged["required_changes"] == ["soften claim"]
    assert merged["scores"]["safety"] == 2


def test_sharded_prompt_builders_require_json_envelopes():
    channel = {"channel": {"id": "vida-plena-45"}, "content_format": {"target_duration_sec": 840}}
    script = {"job_id": "job-a", "channel_id": "vida-plena-45", "narration": "Hola"}
    plan = {
        "data": {
            "batches": [
                {"batch_index": 1, "scene_start": "scene-01", "scene_end": "scene-02"}
            ]
        }
    }
    batch = plan["data"]["batches"][0]

    plan_prompt = _chatgpt_scenes_plan_prompt(channel, script)
    batch_prompt = _chatgpt_scenes_batch_prompt(channel, script, plan, batch)
    qa_prompt = _claude_scenes_qa_batch_prompt(channel, {"scenes": [_scene("scene-01")]}, 1, 1)

    assert '"artifact_type": "scenes_plan"' in plan_prompt
    assert '"artifact_type": "scenes_batch"' in batch_prompt
    assert '"artifact_type": "scenes_qa_batch"' in qa_prompt
    assert "// FILE: scenes_plan.json" in plan_prompt
    assert "// FILE: scenes_batch_01.json" in batch_prompt
    assert "EXACTLY ONE fenced ```json" in batch_prompt
    assert "scene_checks" in qa_prompt
    assert "layout_payload" in batch_prompt
    assert "layout_reason" in batch_prompt
    assert 'layout="checklist"' in batch_prompt
