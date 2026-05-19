import json
from pathlib import Path

import pytest

from video_agent.operator import (
    assert_operator_qa_passed,
    extract_json_object,
    promote_operator_artifact,
    promote_operator_qa,
    write_operator_prompts,
)

ROOT = Path(__file__).resolve().parents[1]


VALID_SCRIPT = {
    "channel_id": "vida-plena-45",
    "job_id": "operator-test",
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
