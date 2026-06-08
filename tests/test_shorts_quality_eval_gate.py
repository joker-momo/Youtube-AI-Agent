from __future__ import annotations

import json
from pathlib import Path


def _write_fixture(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_quality_eval_gate_improves_heuristic_scores(tmp_path: Path):
    from video_agent.shorts.anti_ai import run_anti_ai_review
    from video_agent.shorts.visual_rhythm import apply_visual_rhythm_to_scenes, build_visual_rhythm_plan

    baseline_dir = tmp_path / "fixtures" / "baseline"
    upgraded_dir = tmp_path / "fixtures" / "upgraded"
    baseline_script = {
        "short_id": "short-01",
        "hook": "Consejos saludables para el pan.",
        "narration": "Es importante recordar consejos saludables para mantener hábitos saludables de forma equilibrada.",
        "cta": "Guarda esto.",
    }
    upgraded_script = {
        "short_id": "short-01",
        "hook": "No todo pan oscuro es integral.",
        "narration": "No todo pan oscuro es integral. Pero la etiqueta sí te da la pista. Mira ingrediente y fibra. Sin culpa después de los 45.",
        "micro_tension_lines": ["No basta con el color.", "Pero la etiqueta sí te da la pista."],
        "comment_trigger": "¿También mirabas solo el color?",
    }
    scenes = {
        "short_id": "short-01",
        "total_duration_sec": 24.0,
        "scenes": [
            {"id": "s01", "layout": "short_hook", "duration_sec": 2.4, "motion": "none", "narration": "Pan oscuro.", "on_screen_text": "PAN OSCURO"},
            {"id": "s02", "layout": "short_tip", "duration_sec": 4.0, "motion": "none", "narration": "Mira etiqueta.", "on_screen_text": "ETIQUETA"},
            {"id": "s03", "layout": "short_cta", "duration_sec": 2.4, "motion": "none", "narration": "Guárdalo.", "on_screen_text": "GUÁRDALO"},
        ],
    }
    retention = {
        "short_id": "short-01",
        "hook_pattern": "common_mistake",
        "retention_beats": [{"function": "hook", "tension_line": "No basta con el color."}, {"function": "proof", "tension_line": "Pero la etiqueta sí te da la pista."}],
        "comment_trigger": {"question": "¿También mirabas solo el color?", "type": "personal_experience"},
        "identity_resonance": {"avoid_shame": True, "affirmation": "sin culpa", "audience_phrase": "después de los 45"},
    }
    _write_fixture(baseline_dir / "short-01.json", {"script": baseline_script, "scenes": scenes, "retention": retention})
    _write_fixture(upgraded_dir / "short-01.json", {"script": upgraded_script, "scenes": scenes, "retention": retention})

    baseline = run_anti_ai_review(tmp_path / "baseline-job", "short-01", baseline_script, scenes, retention, {"shorts": {"quality_layers": {"reuse_existing_artifacts": False}}})
    rhythm = build_visual_rhythm_plan(tmp_path / "upgraded-job", "short-01", scenes, retention, {"shorts": {}})
    upgraded_scenes = apply_visual_rhythm_to_scenes(scenes, rhythm)
    upgraded = run_anti_ai_review(tmp_path / "upgraded-job", "short-01", upgraded_script, upgraded_scenes, retention, {"shorts": {"quality_layers": {"reuse_existing_artifacts": False}}})

    assert upgraded["scores"]["human_naturalness"] - baseline["scores"]["human_naturalness"] >= 8
    assert upgraded["scores"]["hook_specificity"] - baseline["scores"]["hook_specificity"] >= 8
    assert upgraded["scores"]["visual_rhythm"] - baseline["scores"]["visual_rhythm"] >= 6
    assert baseline["verdict"] == "FAIL"
    assert upgraded["verdict"] in {"PASS", "WARN"}

