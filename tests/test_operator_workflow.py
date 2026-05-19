import json
from pathlib import Path

import pytest

from video_agent.operator import (
    assert_operator_qa_passed,
    build_operator_next,
    build_operator_status,
    extract_json_object,
    write_operator_review,
    promote_operator_artifact,
    promote_operator_qa,
    write_operator_prompts,
)

ROOT = Path(__file__).resolve().parents[1]


VALID_SCRIPT = {
    "channel_id": "vida-plena-45",
    "job_id": "operator-job",
    "hook": "Dormir mejor puede empezar con una decision simple.",
    "sections": [{"title": "Calma", "text": "Baja el ritmo una hora antes de acostarte."}],
    "narration": "Dormir mejor puede empezar con una decision simple. Baja el ritmo una hora antes de acostarte.",
    "cta": "Prueba un habito esta noche.",
    "qa": {"verdict": "PASS"},
}


def test_extract_json_object_ignores_model_wrapper_text():
    parsed = extract_json_object(
        'Here is the JSON:\n{"title": "Uno", "nested": {"text": "brace } inside string"}}\nDone.'
    )

    assert parsed == {"title": "Uno", "nested": {"text": "brace } inside string"}}


def test_write_operator_prompts_writes_script_prompts(tmp_path):
    result = write_operator_prompts(
        channel_path=ROOT / "configs/vida-plena-45/channel.yaml",
        idea_path=ROOT / "inputs/manual_idea.json",
        job_dir=tmp_path / "operator-job",
        stage="script",
    )

    paths = {path.name for path in result.paths}
    assert paths == {"script_prompt.md", "script_qa_prompt.md"}
    script_prompt = (tmp_path / "operator-job/operator/chatgpt/script_prompt.md").read_text(encoding="utf-8")
    assert "Return exactly one valid JSON object" in script_prompt
    assert "Video idea:" in script_prompt


def test_promote_operator_artifact_extracts_and_validates_raw_json(tmp_path):
    raw_path = tmp_path / "script.raw.txt"
    raw_path.write_text(f"```json\n{json.dumps(VALID_SCRIPT)}\n```", encoding="utf-8")

    result = promote_operator_artifact(tmp_path / "operator-job", "script", raw_path)

    assert result.output_path == tmp_path / "operator-job/script.json"
    assert json.loads(result.output_path.read_text(encoding="utf-8"))["qa"]["verdict"] == "PASS"


def test_promote_operator_artifact_rejects_stale_job_id(tmp_path):
    raw_path = tmp_path / "script.raw.txt"
    stale_script = {**VALID_SCRIPT, "job_id": "old-chatgpt-job"}
    raw_path.write_text(json.dumps(stale_script), encoding="utf-8")

    with pytest.raises(ValueError, match="job_id mismatch"):
        promote_operator_artifact(tmp_path / "operator-job", "script", raw_path)

    assert not (tmp_path / "operator-job/script.json").exists()


def test_promote_operator_artifact_rejects_invalid_scenes_contract(tmp_path):
    raw_path = tmp_path / "scenes.raw.txt"
    raw_path.write_text(
        json.dumps(
            {
                "channel_id": "vida-plena-45",
                "job_id": "operator-job",
                "total_duration_sec": 10,
                "scenes": [
                    {
                        "id": "scene_01",
                        "duration_sec": 10,
                        "narration": "Respira con calma durante la noche.",
                        "on_screen_text": "Respira con calma",
                        "caption": "Respira con calma",
                        "visual_prompt": "Persona relajada en una habitación tranquila",
                        "motion": "slow_zoom",
                        "asset_refs": [],
                    }
                ],
                "qa": {"verdict": "PASS"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as excinfo:
        promote_operator_artifact(tmp_path / "operator-job", "scenes", raw_path)

    message = str(excinfo.value)
    assert "scene-NN" in message
    assert "asset_refs" in message
    assert "ChatGPT prefilled" in message
    assert not (tmp_path / "operator-job/scenes.json").exists()


def test_promote_operator_artifact_rejects_invalid_seo_contract(tmp_path):
    raw_path = tmp_path / "seo.raw.txt"
    raw_path.write_text(
        json.dumps(
            {
                "job_id": "operator-job",
                "title": "Dormir mejor para adultos mayores",
                "description": "Consejos para adultos mayores que quieren descansar mejor despues de los 45.",
                "tags": [
                    "sueño",
                    "bienestar",
                    "adultos mayores",
                    "rutina nocturna",
                    "descanso",
                    "salud",
                    "vida plena",
                    "hábitos",
                    "extra tag",
                ],
                "language": "es-LA",
                "ai_disclosure": True,
                "thumbnail_path": "thumbnail.jpg",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as excinfo:
        promote_operator_artifact(
            tmp_path / "operator-job",
            "seo",
            raw_path,
            channel_path=ROOT / "configs/vida-plena-45/channel.yaml",
        )

    message = str(excinfo.value)
    assert "language must be 'es-419'" in message
    assert "Too many tags" in message
    assert "Forbidden positioning" in message
    assert not (tmp_path / "operator-job/seo.json").exists()


def test_promote_operator_qa_normalizes_gemini_response(tmp_path):
    raw_path = tmp_path / "script_qa.raw.txt"
    raw_path.write_text(
        """
Gemini said
```json
{
  "verdict": "PASS",
  "issues": [],
  "suggested_fixes": [],
  "scores": {"safety": 10}
}
```
""".strip(),
        encoding="utf-8",
    )

    result = promote_operator_qa(tmp_path / "operator-job", "script", raw_path)

    assert result.output_path == tmp_path / "operator-job/operator/gemini/script_qa.json"
    promoted = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert promoted["verdict"] == "PASS"
    assert promoted["required_changes"] == []
    assert promoted["artifact"] == "script"


def test_promote_operator_qa_rejects_non_pass_verdict(tmp_path):
    raw_path = tmp_path / "script_qa.raw.txt"
    raw_path.write_text('{"verdict": "REVISE", "issues": [{"message": "Too broad"}], "scores": {}}', encoding="utf-8")

    with pytest.raises(ValueError, match="QA verdict must be PASS"):
        promote_operator_qa(tmp_path / "operator-job", "script", raw_path)


def test_assert_operator_qa_passed_requires_all_artifact_qas(tmp_path):
    job_dir = tmp_path / "operator-job"
    qa_dir = job_dir / "operator/gemini"
    qa_dir.mkdir(parents=True)
    for artifact in ["script", "scenes"]:
        (qa_dir / f"{artifact}_qa.json").write_text(
            json.dumps({"artifact": artifact, "verdict": "PASS", "issues": [], "required_changes": [], "scores": {}}),
            encoding="utf-8",
        )

    with pytest.raises(FileNotFoundError, match="seo_qa.json"):
        assert_operator_qa_passed(job_dir)


def test_write_operator_review_summarizes_artifacts_and_qa(tmp_path):
    job_dir = tmp_path / "operator-job"
    qa_dir = job_dir / "operator/gemini"
    qa_dir.mkdir(parents=True)
    (job_dir / "script.json").write_text(json.dumps(VALID_SCRIPT), encoding="utf-8")
    (job_dir / "scenes.json").write_text(
        json.dumps({"scenes": [{"id": "scene-01"}, {"id": "scene-02"}], "total_duration_sec": 42}),
        encoding="utf-8",
    )
    (job_dir / "seo.json").write_text(
        json.dumps({"title": "Dormir mejor despues de los 45", "description": "Desc.", "tags": ["sueño"]}),
        encoding="utf-8",
    )
    (job_dir / "video.mp4").write_bytes(b"video")
    (job_dir / "thumbnail.jpg").write_bytes(b"image")
    for artifact in ["script", "scenes", "seo"]:
        (qa_dir / f"{artifact}_qa.json").write_text(
            json.dumps({"artifact": artifact, "verdict": "PASS", "issues": [], "required_changes": [], "scores": {}}),
            encoding="utf-8",
        )

    output_path = write_operator_review(job_dir)
    html = output_path.read_text(encoding="utf-8")

    assert output_path == job_dir / "operator_review.html"
    assert "Operator Review" in html
    assert "Dormir mejor despues de los 45" in html
    assert "video.mp4" in html
    assert html.count("PASS") >= 3
    assert "2 scenes" in html


def test_build_operator_status_reports_next_missing_step(tmp_path):
    job_dir = tmp_path / "operator-job"
    (job_dir / "operator" / "gemini").mkdir(parents=True)
    (job_dir / "script.json").write_text(json.dumps(VALID_SCRIPT), encoding="utf-8")
    (job_dir / "operator" / "gemini" / "script_qa.json").write_text(
        json.dumps({"artifact": "script", "verdict": "PASS", "issues": [], "required_changes": [], "scores": {}}),
        encoding="utf-8",
    )

    status = build_operator_status(job_dir)

    assert status["overall"] == "IN_PROGRESS"
    assert status["artifacts"]["script"]["artifact"] == "present"
    assert status["artifacts"]["script"]["qa"] == "PASS"
    assert status["artifacts"]["scenes"]["artifact"] == "missing"
    assert status["next_step"] == "Generate and promote scenes.json, then run Gemini QA for scenes."


def test_build_operator_next_writes_script_prompt_for_empty_job(tmp_path):
    job_dir = tmp_path / "operator-job"

    result = build_operator_next(
        channel_path=ROOT / "configs/vida-plena-45/channel.yaml",
        idea_path=ROOT / "inputs/manual_idea.json",
        job_dir=job_dir,
    )

    assert result.step == "chatgpt-script"
    assert result.prompt_paths == [job_dir / "operator/chatgpt/script_prompt.md"]
    assert result.prompt_paths[0].exists()
    assert "save the response" in result.message
    assert "operator-promote" in result.commands[0]
    assert "script.raw.txt" in result.commands[0]


def test_build_operator_next_promotes_existing_raw_before_prompting(tmp_path):
    job_dir = tmp_path / "operator-job"
    raw_path = job_dir / "operator/chatgpt/script.raw.txt"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text("{}", encoding="utf-8")

    result = build_operator_next(
        channel_path=ROOT / "configs/vida-plena-45/channel.yaml",
        idea_path=ROOT / "inputs/manual_idea.json",
        job_dir=job_dir,
    )

    assert result.step == "promote-script"
    assert result.prompt_paths == []
    assert "Raw ChatGPT response exists" in result.message
    assert result.commands == [
        f"docker compose run --rm video-agent python -m video_agent.cli operator-promote --job-dir {job_dir} --artifact script --raw-file {raw_path} --channel {ROOT / 'configs/vida-plena-45/channel.yaml'}"
    ]
