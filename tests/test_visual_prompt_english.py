"""Tests for visual_prompt English enforcement (validator + query translation)."""

from __future__ import annotations

from video_agent.assets.service import (
    _is_likely_spanish_query,
    _translate_spanish_query_to_english,
)
from video_agent.operator_validators import (
    _looks_like_spanish_visual_prompt,
    _validate_visual_prompt,
)


# ---------- detector ----------


def test_detector_flags_accented_spanish():
    is_sp, reason = _looks_like_spanish_visual_prompt(
        "Habitación cálida al atardecer"
    )
    assert is_sp
    assert reason and "accent" in reason.lower()


def test_detector_flags_unaccented_spanish_by_stopwords():
    is_sp, reason = _looks_like_spanish_visual_prompt(
        "Persona acomoda una manta ligera junto a la cama"
    )
    assert is_sp
    assert reason and "stopwords" in reason.lower()


def test_detector_passes_english_prompt():
    is_sp, _ = _looks_like_spanish_visual_prompt(
        "Mature woman drinking herbal tea on a sofa at night, warm tungsten light"
    )
    assert not is_sp


def test_detector_passes_short_neutral_prompt():
    is_sp, _ = _looks_like_spanish_visual_prompt("clock on nightstand")
    assert not is_sp


# ---------- validator ----------


def test_validator_blocks_spanish_visual_prompt():
    result = _validate_visual_prompt(
        {"visual_prompt": "Persona apaga la lámpara en la habitación tranquila"},
        "scene-01",
    )
    assert not result.is_valid
    assert "visual_prompt must be ENGLISH" in result.errors[0]


def test_validator_accepts_english_visual_prompt():
    result = _validate_visual_prompt(
        {"visual_prompt": "Mature adult arranging pillows on a calm bedroom bed, warm light"},
        "scene-01",
    )
    assert result.is_valid


def test_validator_rejects_empty_visual_prompt():
    result = _validate_visual_prompt({"visual_prompt": ""}, "scene-01")
    assert not result.is_valid
    assert "missing or empty" in result.errors[0]


# ---------- query translator ----------


def test_is_likely_spanish_query_detects_stopwords():
    assert _is_likely_spanish_query("persona en la habitación")
    assert not _is_likely_spanish_query("person in bedroom evening")


def test_translate_passes_english_through_unchanged():
    text = "Mature woman drinking tea at night"
    assert _translate_spanish_query_to_english(text) == text


def test_translate_converts_spanish_query_to_english_keywords():
    out = _translate_spanish_query_to_english(
        "Persona apaga la lámpara en la habitación tranquila"
    )
    lowered = out.lower()
    assert "person" in lowered
    assert "lamp" in lowered
    assert "bedroom" in lowered
    assert "calm" in lowered
    # Stopwords stripped
    assert " la " not in f" {lowered} "
    assert " en " not in f" {lowered} "


def test_translate_handles_objects_sequence_prompt():
    out = _translate_spanish_query_to_english(
        "Secuencia de objetos: reloj, lámpara tenue, libro cerrado y cama preparada"
    )
    lowered = out.lower()
    assert "clock" in lowered
    assert "lamp" in lowered
    assert "book" in lowered
    assert "bed" in lowered


# ---------- orchestrator enforcement ----------


def test_enforce_scenes_visual_prompt_english_flips_verdict(tmp_path):
    import json
    from video_agent.orchestrator.stages import _enforce_scenes_visual_prompt_english

    job_dir = tmp_path / "job-test"
    job_dir.mkdir()
    scenes = {
        "scenes": [
            {"id": "scene-01", "visual_prompt": "Mature woman on calm bedroom bed at night"},  # OK
            {"id": "scene-02", "visual_prompt": "Persona apaga la lámpara en la habitación"},  # Spanish
            {"id": "scene-03", "visual_prompt": "Hombre lee un libro en el sofá tranquilo"},  # Spanish
        ]
    }
    (job_dir / "scenes.json").write_text(json.dumps(scenes), encoding="utf-8")

    qa_output = job_dir / "scenes_qa.json"
    initial_payload = {
        "verdict": "PASS",
        "issues": [],
        "required_changes": [],
        "scores": {"clarity": 5},
    }
    qa_output.write_text(json.dumps(initial_payload), encoding="utf-8")

    _enforce_scenes_visual_prompt_english(job_dir, qa_output, dict(initial_payload))

    updated = json.loads(qa_output.read_text(encoding="utf-8"))
    assert updated["verdict"] == "NEEDS_REWORK"
    assert any("Spanish" in i for i in updated["issues"])
    assert any("ENGLISH" in c for c in updated["required_changes"])
    assert updated["scores"]["clarity"] <= 3


def test_enforce_scenes_visual_prompt_english_no_op_when_all_english(tmp_path):
    import json
    from video_agent.orchestrator.stages import _enforce_scenes_visual_prompt_english

    job_dir = tmp_path / "job-test"
    job_dir.mkdir()
    scenes = {
        "scenes": [
            {"id": "scene-01", "visual_prompt": "Mature woman drinking tea on a sofa"},
            {"id": "scene-02", "visual_prompt": "Calm bedroom at night with soft lamp"},
        ]
    }
    (job_dir / "scenes.json").write_text(json.dumps(scenes), encoding="utf-8")

    qa_output = job_dir / "scenes_qa.json"
    initial_payload = {"verdict": "PASS", "issues": [], "required_changes": []}
    qa_output.write_text(json.dumps(initial_payload), encoding="utf-8")

    _enforce_scenes_visual_prompt_english(job_dir, qa_output, dict(initial_payload))

    updated = json.loads(qa_output.read_text(encoding="utf-8"))
    assert updated["verdict"] == "PASS"
