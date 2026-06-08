from __future__ import annotations

from pathlib import Path


def _job(tmp_path: Path) -> Path:
    job = tmp_path / "long-job"
    job.mkdir()
    return job


def _retention() -> dict:
    return {
        "short_id": "short-01",
        "hook_pattern": "common_mistake",
        "retention_beats": [{"function": "hook", "tension_line": "No basta con que sea oscuro."}],
        "comment_trigger": {"question": "¿También lo mirabas así?", "type": "personal_experience"},
    }


def test_humanization_detects_configured_generic_phrases_and_emphasis(tmp_path: Path):
    from video_agent.shorts.humanization import build_spoken_humanization

    script = {
        "short_id": "short-01",
        "hook": "No todo pan oscuro es integral.",
        "narration": "Hoy vamos a ver consejos saludables. Mira ingredientes, fibra y azúcar.",
        "idea_contract": {"must_preserve_count": True, "original_count": 3, "final_count": 3},
        "idea_items": [
            {"item_id": 1, "label": "ingredientes", "required": True},
            {"item_id": 2, "label": "fibra", "required": True},
            {"item_id": 3, "label": "azúcar", "required": True},
        ],
    }

    doc = build_spoken_humanization(_job(tmp_path), "short-01", script, _retention(), {"shorts": {}})

    assert "hoy vamos a" in doc["forbidden_robotic_phrases_found"]
    assert "consejos saludables" in doc["forbidden_robotic_phrases_found"]
    assert doc["emphasis_map"]
    assert doc["tts_notes"]["avoid_flat_delivery"] is True
    assert doc["generation_mode"] == "deterministic"


def test_humanization_discards_rewrite_when_idea_count_changes(tmp_path: Path):
    from video_agent.shorts.humanization import build_spoken_humanization

    script = {
        "short_id": "short-01",
        "narration": "Uno: ingredientes. Dos: fibra. Tres: azúcar.",
        "idea_contract": {"must_preserve_count": True, "original_count": 3, "final_count": 3},
        "idea_items": [
            {"item_id": 1, "label": "ingredientes", "required": True},
            {"item_id": 2, "label": "fibra", "required": True},
            {"item_id": 3, "label": "azúcar", "required": True},
        ],
    }

    def llm_fn(kind: str, prompt: str) -> str:
        return '{"rewritten_narration":"Mira ingredientes y fibra.","delivery_style":"warm_direct"}'

    doc = build_spoken_humanization(
        _job(tmp_path),
        "short-01",
        script,
        _retention(),
        {"shorts": {"quality_layers": {"enable_llm_humanization": True, "max_new_quality_llm_calls_per_short": 1}}},
        llm_fn,
    )

    assert "rewritten_narration" not in doc
    assert doc["rewrite_discarded"] is True

