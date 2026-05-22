from __future__ import annotations

import json
from pathlib import Path
import pytest

from video_agent.contracts import repo_root
from video_agent.orchestrator import create_job
from video_agent.orchestrator.stages import (
    SCENES_PROMPT_PATH,
    run_scenes_stage,
    run_script_stage,
)
from video_agent.operator import (
    write_operator_prompts,
    get_scenes_qa_feedback,
)


@pytest.fixture
def channel_path() -> Path:
    return repo_root() / "configs/vida-plena-45/channel.yaml"


@pytest.fixture
def idea_payload() -> dict:
    return {
        "channel_id": "vida-plena-45",
        "topic": "Cómo dormir profundamente después de los 45 años",
        "hook": "Dormir bien a los 45 años no es imposible.",
        "narration_outline": "Outline narration outline outline.",
        "target_duration_sec": 45,
    }


@pytest.fixture
def valid_script_payload() -> dict:
    return {
        "channel_id": "vida-plena-45",
        "job_id": "job-s1",
        "hook": "Dormir mejor empieza con una decisión simple.",
        "sections": [
            {
                "title": "Calma",
                "text": "Baja el ritmo una hora antes de acostarte.",
            }
        ],
        "narration": "Dormir mejor empieza con una decisión simple. Baja el ritmo una hora antes de acostarte.",
        "cta": "Prueba este hábito esta noche.",
        "qa": {"verdict": "PENDING_GEMINI_QA"},
    }


def _fake_pass_idea_research(job_dir: Path) -> None:
    from video_agent.orchestrator.job_state import load_job, save_job
    state = load_job(job_dir)
    stage = state.stage("idea_research")
    stage.status = "completed"
    nxt = next((s for s in state.stages if s.status == "pending"), None)
    if nxt is not None:
        state.current_stage = nxt.name
    save_job(job_dir, state)


def _prepare_promoted_script(
    job_dir: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
) -> None:
    create_job(job_dir, job_id="job-s1", channel_id="vida-plena-45", idea_path="idea.json")
    (job_dir / "idea.json").write_text(
        json.dumps(idea_payload, ensure_ascii=False), encoding="utf-8"
    )
    _fake_pass_idea_research(job_dir)
    run_script_stage(job_dir, channel_path)
    # Write approved script to disk
    (job_dir / "script.json").write_text(
        json.dumps(valid_script_payload, ensure_ascii=False), encoding="utf-8"
    )
    
    # Fake script qa passes and advance state to scenes stage
    from video_agent.orchestrator.job_state import load_job, save_job
    state = load_job(job_dir)
    state.stage("script_promote").status = "completed"
    state.stage("script_qa").status = "completed"
    state.current_stage = "scenes"
    save_job(job_dir, state)


def test_rework_feedback_injected_on_needs_rework(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    job_dir = tmp_path / "job-s1"
    _prepare_promoted_script(job_dir, channel_path, idea_payload, valid_script_payload)

    # Write a mock scenes_qa.json with verdict NEEDS_REWORK
    qa_dir = job_dir / "operator" / "claude"
    qa_dir.mkdir(parents=True, exist_ok=True)
    qa_data = {
        "verdict": "NEEDS_REWORK",
        "scores": {"schema_fit": 2, "channel_fit": 3, "safety": 5, "clarity": 4},
        "issues": [
            "Scene 2 visual prompt is too vague.",
            "Scene 3 duration is too short."
        ],
        "required_changes": [
            "Clarify visual prompts for all outdoor scenes.",
            "Adjust durations to meet channel requirements."
        ]
    }
    (qa_dir / "scenes_qa.json").write_text(json.dumps(qa_data), encoding="utf-8")

    # Run the scenes stage
    output = run_scenes_stage(job_dir, channel_path)
    assert output.exists()
    
    prompt_text = output.read_text(encoding="utf-8")
    
    # Assertions
    assert "⚠️ CRITICAL REWORK FEEDBACK FROM PREVIOUS QA REVIEW:" in prompt_text
    assert "Issues found in previous version:" in prompt_text
    assert "- Scene 2 visual prompt is too vague." in prompt_text
    assert "- Scene 3 duration is too short." in prompt_text
    assert "Required changes for this revision:" in prompt_text
    assert "- Clarify visual prompts for all outdoor scenes." in prompt_text
    assert "- Adjust durations to meet channel requirements." in prompt_text


def test_rework_feedback_not_injected_on_pass(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    job_dir = tmp_path / "job-s1"
    _prepare_promoted_script(job_dir, channel_path, idea_payload, valid_script_payload)

    # Write a mock scenes_qa.json with verdict PASS
    qa_dir = job_dir / "operator" / "claude"
    qa_dir.mkdir(parents=True, exist_ok=True)
    qa_data = {
        "verdict": "PASS",
        "scores": {"schema_fit": 5, "channel_fit": 5, "safety": 5, "clarity": 5},
        "issues": [],
        "required_changes": []
    }
    (qa_dir / "scenes_qa.json").write_text(json.dumps(qa_data), encoding="utf-8")

    # Run the scenes stage
    output = run_scenes_stage(job_dir, channel_path)
    assert output.exists()
    
    prompt_text = output.read_text(encoding="utf-8")
    
    # Assertions
    assert "⚠️ CRITICAL REWORK FEEDBACK FROM PREVIOUS QA REVIEW" not in prompt_text
    assert "Issues found in previous version:" not in prompt_text


def test_rework_feedback_not_injected_when_qa_missing(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    job_dir = tmp_path / "job-s1"
    _prepare_promoted_script(job_dir, channel_path, idea_payload, valid_script_payload)

    # Run the scenes stage without writing any scenes_qa.json
    output = run_scenes_stage(job_dir, channel_path)
    assert output.exists()
    
    prompt_text = output.read_text(encoding="utf-8")
    
    # Assertions
    assert "⚠️ CRITICAL REWORK FEEDBACK FROM PREVIOUS QA REVIEW" not in prompt_text
    assert "Issues found in previous version:" not in prompt_text


def test_write_operator_prompts_injects_rework_feedback(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    job_dir = tmp_path / "job-s1"
    _prepare_promoted_script(job_dir, channel_path, idea_payload, valid_script_payload)

    # Write a mock scenes_qa.json with verdict NEEDS_REWORK
    qa_dir = job_dir / "operator" / "claude"
    qa_dir.mkdir(parents=True, exist_ok=True)
    qa_data = {
        "verdict": "NEEDS_REWORK",
        "scores": {"schema_fit": 2, "channel_fit": 3, "safety": 5, "clarity": 4},
        "issues": ["Issue A"],
        "required_changes": ["Change B"]
    }
    (qa_dir / "scenes_qa.json").write_text(json.dumps(qa_data), encoding="utf-8")

    # Manually write scenes.json because write_operator_prompts expects scenes for QA prompt
    (job_dir / "scenes.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")

    # Call write_operator_prompts for scenes stage
    write_operator_prompts(channel_path, job_dir / "idea.json", job_dir, stage="scenes")

    prompt_file = job_dir / "operator" / "chatgpt" / "scenes_prompt.md"
    assert prompt_file.exists()
    prompt_text = prompt_file.read_text(encoding="utf-8")

    assert "⚠️ CRITICAL REWORK FEEDBACK FROM PREVIOUS QA REVIEW:" in prompt_text
    assert "- Issue A" in prompt_text
    assert "- Change B" in prompt_text
