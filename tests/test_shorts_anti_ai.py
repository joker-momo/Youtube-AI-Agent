from __future__ import annotations

from pathlib import Path


def _job(tmp_path: Path) -> Path:
    job = tmp_path / "long-job"
    job.mkdir()
    return job


def _retention(question: str = "¿También lo mirabas así?") -> dict:
    return {
        "short_id": "short-01",
        "hook_pattern": "common_mistake",
        "viewer_pain": "elegir pan por color",
        "payoff_promise": "mirar ingredientes y fibra",
        "comment_trigger": {"question": question, "type": "personal_experience"},
        "identity_resonance": {"avoid_shame": True, "affirmation": "sin culpa", "audience_phrase": "después de los 45"},
    }


def _scenes() -> dict:
    return {
        "scenes": [
            {"id": "s01", "layout": "short_hook", "motion": "push_in", "narration": "No todo pan oscuro es integral.", "on_screen_text": "PAN OSCURO"},
            {"id": "s02", "layout": "short_tip", "motion": "crop_shift", "narration": "Mira el primer ingrediente.", "on_screen_text": "INGREDIENTES"},
            {"id": "s03", "layout": "short_cta", "motion": "slow_zoom", "narration": "Guárdalo para comprar.", "on_screen_text": "GUÁRDALO"},
        ]
    }


def test_anti_ai_generic_intro_fails(tmp_path: Path):
    from video_agent.shorts.anti_ai import run_anti_ai_review

    review = run_anti_ai_review(
        _job(tmp_path),
        "short-01",
        {"narration": "Hola, hoy vamos a ver consejos saludables para mantener hábitos saludables.", "hook": "Hoy vamos a ver"},
        _scenes(),
        _retention(""),
        {"shorts": {}},
    )

    assert review["verdict"] == "FAIL"
    assert "greeting_or_generic_intro" in review["robotic_patterns"]


def test_anti_ai_mild_generic_wording_warns(tmp_path: Path):
    from video_agent.shorts.anti_ai import run_anti_ai_review

    review = run_anti_ai_review(
        _job(tmp_path),
        "short-01",
        {"narration": "El color puede ayudarte a orientarte, pero mira la etiqueta.", "hook": "El color no basta", "comment_trigger": "¿Te pasaba?"},
        _scenes(),
        _retention(),
        {"shorts": {}},
    )

    assert review["verdict"] == "WARN"
    assert review["scores"]["human_naturalness"] < 80


def test_anti_ai_specific_human_script_passes(tmp_path: Path):
    from video_agent.shorts.anti_ai import run_anti_ai_review

    review = run_anti_ai_review(
        _job(tmp_path),
        "short-01",
        {
            "narration": "No todo pan oscuro es integral. Gira la bolsa y mira el primer ingrediente. Luego busca fibra por 100 gramos. Sin culpa: después de los 45, decidir rápido ayuda.",
            "hook": "No todo pan oscuro es integral.",
            "comment_trigger": "¿También mirabas solo el color?",
        },
        _scenes(),
        _retention(),
        {"shorts": {}},
    )

    assert review["verdict"] == "PASS"
    assert review["scores"]["hook_specificity"] >= 75
    assert review["scores"]["human_naturalness"] >= 75
