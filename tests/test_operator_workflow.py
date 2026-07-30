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
    assert "// FILE: script.json" in script_prompt
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


def test_promote_operator_artifact_normalizes_compact_script_shape(tmp_path):
    raw_path = tmp_path / "script.raw.txt"
    compact = {
        "channel_id": "vida-plena-45",
        "job_id": "operator-job",
        "hook": "Habitos simples para dormir mejor.",
        "title": "10 habitos nocturnos",
        "narration": "Dormir bien empieza con una rutina simple.",
        "tts": {"estimated_duration_seconds": 75},
    }
    raw_path.write_text(json.dumps(compact), encoding="utf-8")

    result = promote_operator_artifact(tmp_path / "operator-job", "script", raw_path)
    promoted = json.loads(result.output_path.read_text(encoding="utf-8"))

    assert isinstance(promoted["sections"], list)
    assert promoted["sections"]
    assert isinstance(promoted["cta"], str)
    assert promoted["cta"]
    assert promoted["qa"]["verdict"] == "PASS"


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
    # asset_refs as a list is now silently coerced to {} by
    # _normalize_scenes_candidate, and prefilled qa verdict is rewritten
    # to pending so validation focuses on structural shape.
    assert not (tmp_path / "operator-job/scenes.json").exists()


def test_promote_operator_artifact_normalizes_compact_scenes_shape(tmp_path):
    raw_path = tmp_path / "scenes.raw.txt"
    raw_path.write_text(
        json.dumps(
            {
                "channel_id": "vida-plena-45",
                "job_id": "operator-job",
                "scenes": [
                    {
                        "scene_id": "scene-01",
                        "duration_sec": 12,
                        "visual": "Warm bedroom at night",
                        "narration": "Baja la luz antes de dormir.",
                        "caption": "Baja la luz",
                        "asset_refs": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = promote_operator_artifact(tmp_path / "operator-job", "scenes", raw_path)
    promoted = json.loads(result.output_path.read_text(encoding="utf-8"))

    assert promoted["scenes"][0]["id"] == "scene-01"
    assert promoted["scenes"][0]["visual_prompt"] == "Warm bedroom at night"
    assert promoted["scenes"][0]["on_screen_text"] == "Baja la luz"
    assert promoted["qa"]["verdict"] == "PENDING_GEMINI_QA"


def test_promote_operator_artifact_unwraps_scenes_rework_envelope(tmp_path):
    raw_path = tmp_path / "scenes.raw.txt"
    raw_path.write_text(
        json.dumps(
            {
                "artifact_type": "scenes",
                "channel_id": "vida-plena-45",
                "job_id": "operator-job",
                "data": {
                    "total_duration_sec": 12,
                    "scenes": [
                        {
                            "scene_id": "scene-01",
                            "duration_sec": 12,
                            "narration": "Baja la luz antes de dormir.",
                            "on_screen_text": "Baja la luz",
                            "visual_direction": "Warm bedroom at night",
                            "camera_notes": "Slow push-in.",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    result = promote_operator_artifact(tmp_path / "operator-job", "scenes", raw_path)
    promoted = json.loads(result.output_path.read_text(encoding="utf-8"))

    assert promoted["total_duration_sec"] == 12
    assert promoted["scenes"][0]["id"] == "scene-01"
    assert promoted["scenes"][0]["visual_prompt"] == "Warm bedroom at night"
    assert promoted["scenes"][0]["motion"] == "Slow push-in."
    assert promoted["qa"]["verdict"] == "PENDING_GEMINI_QA"


def test_promote_operator_artifact_rewrites_scenes_prefilled_qa(tmp_path):
    raw_path = tmp_path / "scenes.raw.txt"
    raw_path.write_text(
        json.dumps(
            {
                "channel_id": "vida-plena-45",
                "job_id": "operator-job",
                "total_duration_sec": 12,
                "scenes": [
                    {
                        "id": "scene-01",
                        "duration_sec": 12,
                        "narration": "Respira con calma.",
                        "on_screen_text": "Respira con calma",
                        "caption": "Respira",
                        "visual_prompt": "Calm bedroom",
                        "motion": "slow push-in",
                        "asset_refs": {},
                        "layout": "hook",
                        "layout_payload": {
                            "title": "Respira con calma",
                            "body": "",
                            "bullets": [],
                            "cta": "",
                        },
                    }
                ],
                "qa": {"verdict": "PASS"},
            }
        ),
        encoding="utf-8",
    )

    result = promote_operator_artifact(tmp_path / "operator-job", "scenes", raw_path)
    promoted = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert promoted["qa"]["verdict"] == "PENDING_GEMINI_QA"


def test_promote_operator_artifact_promotes_final_scene_cta_from_script(tmp_path):
    job_dir = tmp_path / "operator-job"
    job_dir.mkdir()
    (job_dir / "script.json").write_text(json.dumps(VALID_SCRIPT), encoding="utf-8")
    raw_path = tmp_path / "scenes.raw.txt"
    raw_path.write_text(
        json.dumps(
            {
                "channel_id": "vida-plena-45",
                "job_id": "operator-job",
                "total_duration_sec": 24,
                "scenes": [
                    {
                        "id": "scene-01",
                        "duration_sec": 12,
                        "narration": "Baja la luz antes de dormir.",
                        "on_screen_text": "BAJA LA LUZ",
                        "caption": "Baja la luz antes de dormir.",
                        "visual_prompt": "Calm bedroom",
                        "motion": "slow_zoom",
                        "asset_refs": {},
                        "layout": "subtitle",
                    },
                    {
                        "id": "scene-02",
                        "duration_sec": 12,
                        "narration": "Puedes probar este hábito esta noche.",
                        "on_screen_text": "PRUÉBALO HOY",
                        "caption": "Puedes probar este hábito esta noche.",
                        "visual_prompt": "Calm morning",
                        "motion": "pan_left",
                        "asset_refs": {},
                        "layout": "subtitle",
                    },
                ],
                "qa": {"verdict": "PENDING_GEMINI_QA"},
            }
        ),
        encoding="utf-8",
    )

    result = promote_operator_artifact(job_dir, "scenes", raw_path)
    promoted = json.loads(result.output_path.read_text(encoding="utf-8"))

    assert promoted["scenes"][0]["layout"] != "cta"
    assert promoted["scenes"][1]["layout"] == "cta"
    assert promoted["scenes"][1]["layout_payload"]["cta"] == VALID_SCRIPT["cta"]


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
                "thumbnail_text": "DUERME MEJOR HOY",
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
    assert "language should be 'es-ES'" in message
    assert "Too many tags" in message
    assert "Forbidden positioning" in message
    assert not (tmp_path / "operator-job/seo.json").exists()


def test_promote_operator_artifact_preserves_wrong_language_for_qa_rework(tmp_path):
    """Language mismatches promote with a warning so Gemini QA can force rework."""
    raw_path = tmp_path / "seo.raw.txt"
    raw_path.write_text(
        json.dumps(
            {
                "job_id": "operator-job",
                "title": "Cómo dormir mejor después de los 45",
                "description": "Una guía práctica para crear una rutina nocturna más tranquila y descansar mejor.",
                "tags": [
                    "sueño",
                    "descanso",
                    "rutina nocturna",
                    "bienestar",
                    "hábitos saludables",
                    "dormir mejor",
                ],
                "language": "es-MX",
                "ai_disclosure": True,
                "thumbnail_path": "thumbnail.jpg",
                "thumbnail_text": "DUERME MEJOR HOY",
                "suggested_pinned_comments": "¿Qué hábito probarás esta noche?",
            }
        ),
        encoding="utf-8",
    )

    result = promote_operator_artifact(
        tmp_path / "operator-job",
        "seo",
        raw_path,
        channel_path=ROOT / "configs/vida-plena-45/channel.yaml",
    )
    promoted = json.loads(result.output_path.read_text(encoding="utf-8"))

    assert promoted["language"] == "es-MX"


def test_promote_operator_artifact_repairs_channel_name_line_break_in_description(tmp_path):
    raw_path = tmp_path / "seo.raw.txt"
    raw_path.write_text(
        json.dumps(
            {
                "job_id": "operator-job",
                "title_variants": [
                    {
                        "title": "Dormir mejor despues de los 45 con una rutina",
                        "thumbnail_text": "DUERME MEJOR HOY",
                    }
                ],
                "title": "Dormir mejor despues de los 45 con una rutina",
                "description": (
                    "Dormir mejor despues de los 45 puede empezar con una rutina sencilla.\n\n"
                    "00:00 - Inicio\n"
                    "01:30 - Rutina tranquila\n\n"
                    "Vida\n"
                    "Plena 45+ comparte habitos sencillos para descansar mejor."
                ),
                "tags": [
                    "dormir mejor",
                    "descanso",
                    "rutina nocturna",
                    "bienestar 45",
                    "vida plena 45",
                ],
                "language": "es-ES",
                "ai_disclosure": True,
                "thumbnail_path": "thumbnail.jpg",
                "thumbnail_text": "DUERME MEJOR HOY",
                "suggested_pinned_comments": "Que habito probaras esta noche? Suscribete a Vida Plena 45+.",
            }
        ),
        encoding="utf-8",
    )

    result = promote_operator_artifact(
        tmp_path / "operator-job",
        "seo",
        raw_path,
        channel_path=ROOT / "configs/vida-plena-45/channel.yaml",
    )
    promoted = json.loads(result.output_path.read_text(encoding="utf-8"))

    assert "Vida Plena 45+ comparte" in promoted["description"]
    assert "Vida\nPlena" not in promoted["description"]
    assert "00:00 - Inicio\n01:30 - Rutina tranquila" in promoted["description"]


def test_canonicalize_channel_name_repairs_display_prefix_of_tagged_name():
    """Channel configs use 'Name: Tagline' as the official name, but prose in
    descriptions refers to the channel by the display prefix ('Vida Plena
    45+'). Wrap repair must cover both forms, not just the full name."""
    from video_agent.operator import _canonicalize_channel_name_whitespace

    cfg = {"channel": {"name": "Vida Plena 45+: Salud y Bienestar"}}

    fixed = _canonicalize_channel_name_whitespace(
        "En Vida\nPlena 45+ comparte habitos sencillos.", cfg
    )
    assert "Vida Plena 45+ comparte" in fixed
    assert "Vida\nPlena" not in fixed

    fixed_full = _canonicalize_channel_name_whitespace(
        "Vida Plena\n45+:\nSalud y Bienestar ofrece consejos.", cfg
    )
    assert "Vida Plena 45+: Salud y Bienestar ofrece" in fixed_full


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
        f"python -m video_agent.cli operator-promote --job-dir {job_dir} --artifact script --raw-file {raw_path} --channel {ROOT / 'configs/vida-plena-45/channel.yaml'}"
    ]


def test_extract_json_objects_robustness_with_preamble():
    from video_agent.operator import extract_json_objects
    raw_text = 'Gemini responded: {\n\n{\n  "title": "Uno"\n}\n'
    candidates = extract_json_objects(raw_text)
    assert len(candidates) == 1
    assert candidates[0] == {"title": "Uno"}


def test_extract_json_objects_recovers_truncated_root_object():
    """Model output cut off before the final closing brace must still be
    recoverable by balancing the open braces/brackets."""
    from video_agent.operator import extract_json_objects
    # Root object truncated: missing the final '}'. Inner objects close fine.
    raw_text = (
        '{\n'
        '"channel_id": "ch",\n'
        '"sections": [{"title": "A"}, {"title": "B"}],\n'
        '"qa": {\n'
        '"verdict": "PENDING_GEMINI_QA"\n'
        '}\n'
    )
    candidates = extract_json_objects(raw_text)
    assert any(
        c.get("channel_id") == "ch" and c.get("qa") == {"verdict": "PENDING_GEMINI_QA"}
        for c in candidates
    ), candidates


def test_extract_json_objects_leaves_complete_objects_untouched():
    from video_agent.operator import extract_json_objects
    raw_text = '{"a": 1}\n{"b": 2}'
    candidates = extract_json_objects(raw_text)
    assert candidates == [{"a": 1}, {"b": 2}]


def test_extract_json_objects_handles_raw_newline_inside_string_value():
    # Pretty-printed object (structural newlines between fields) whose
    # description value contains a RAW newline — a common ChatGPT failure.
    # Must parse as ONE object with all keys, not split into inner objects.
    from video_agent.operator import extract_json_objects
    raw_text = (
        "{\n"
        '  "job_id": "job-a",\n'
        '  "title": "Hello",\n'
        '  "description": "First line\nsecond line\twith tab",\n'
        '  "variants": [{"title": "x"}, {"title": "y"}]\n'
        "}"
    )
    candidates = extract_json_objects(raw_text)
    top = [c for c in candidates if "job_id" in c]
    assert len(top) == 1
    assert top[0]["job_id"] == "job-a"
    assert "second line" in top[0]["description"]
