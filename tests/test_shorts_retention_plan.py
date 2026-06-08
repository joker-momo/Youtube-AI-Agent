from __future__ import annotations

import json
from pathlib import Path


def _job(tmp_path: Path) -> Path:
    job = tmp_path / "long-job"
    job.mkdir()
    return job


def _plan() -> dict:
    return {
        "short_id": "short-01",
        "source_long_job_id": "long-job",
        "format": "pain_to_tip",
        "hook_angle": "El pan oscuro no siempre es integral",
        "viewer_pain": "confundir color con calidad",
        "practical_payoff": "mirar ingredientes y fibra",
        "narration_seed": "Mira la etiqueta antes de comprar pan.",
    }


def test_retention_plan_fallback_writes_required_fields(tmp_path: Path):
    from video_agent.shorts import paths
    from video_agent.shorts.retention_plan import ALLOWED_HOOK_PATTERNS, build_retention_plan

    job = _job(tmp_path)
    plan = build_retention_plan(job, _plan(), {"shorts": {}})

    assert plan["short_id"] == "short-01"
    assert plan["source_long_job_id"] == "long-job"
    assert plan["hook_pattern"] in ALLOWED_HOOK_PATTERNS
    assert len(plan["retention_beats"]) >= 4
    assert len(plan["pattern_interrupts"]) >= 3
    assert plan["identity_resonance"]["avoid_shame"] is True
    assert plan["comment_trigger"]["question"]
    assert plan["qa"]["verdict"] == "PENDING"
    assert plan["generation_mode"] == "deterministic"
    assert plan["input_hash"]

    artifact = paths.short_json_dir(job, "short-01") / paths.SHORT_RETENTION_PLAN_FILE
    assert json.loads(artifact.read_text(encoding="utf-8"))["short_id"] == "short-01"


def test_retention_plan_parses_llm_json_when_enabled(tmp_path: Path):
    from video_agent.shorts.retention_plan import build_retention_plan

    payload = {
        "short_id": "short-01",
        "source_long_job_id": "long-job",
        "hook_pattern": "hidden_truth",
        "viewer_pain": "dolor",
        "curiosity_gap": "brecha",
        "payoff_promise": "promesa",
        "retention_beats": [
            {
                "start_sec": 0.0,
                "end_sec": 2.0,
                "function": "hook",
                "tension_line": "No mires solo el color.",
                "visual_interrupt": "text_pop",
                "expected_viewer_question": "Entonces, qué miro?",
            }
        ],
        "pattern_interrupts": [{"at_sec": 3.0, "type": "zoom", "purpose": "cambio"}],
        "identity_resonance": {"avoid_shame": True, "affirmation": "sin culpa", "audience_phrase": "después de los 45"},
        "comment_trigger": {"question": "¿Te pasaba?", "type": "personal_experience"},
        "qa": {"verdict": "PENDING"},
    }

    def llm_fn(kind: str, prompt: str) -> str:
        return json.dumps(payload)

    plan = build_retention_plan(
        _job(tmp_path),
        _plan(),
        {"shorts": {"quality_layers": {"enable_llm_retention_plan": True, "max_new_quality_llm_calls_per_short": 1}}},
        llm_fn,
    )

    assert plan["hook_pattern"] == "hidden_truth"
    assert plan["generation_mode"] == "llm"



def _toast_plan() -> dict:
    return {
        "short_id": "short-05",
        "source_long_job_id": "long-job",
        "format": "top_tips",
        "title": "3 piezas para que una tostada sacie más",
        "hook_angle": "Pan y hambre enseguida",
        "viewer_pain": "desayunar pan y tener hambre al rato",
        "practical_payoff": "montar la tostada con 3 piezas",
        "narration_seed": "No es solo el pan: son 3 piezas.",
    }


def test_retention_plan_tension_lines_are_distinct(tmp_path: Path):
    from video_agent.shorts.retention_plan import build_retention_plan

    plan = build_retention_plan(_job(tmp_path), _toast_plan(), {"shorts": {}})
    beats = plan["retention_beats"]
    assert len(beats) == 6
    lines = [b["tension_line"] for b in beats]
    assert len(set(lines)) >= 4, lines
    # no single line repeated more than twice
    from collections import Counter
    assert max(Counter(lines).values()) <= 2, lines


def test_retention_plan_expected_questions_vary(tmp_path: Path):
    from video_agent.shorts.retention_plan import build_retention_plan

    plan = build_retention_plan(_job(tmp_path), _toast_plan(), {"shorts": {}})
    questions = [b["expected_viewer_question"] for b in plan["retention_beats"]]
    assert len(set(questions)) >= 3, questions


def test_retention_plan_comment_trigger_topic_match(tmp_path: Path):
    from video_agent.shorts.retention_plan import build_retention_plan

    plan = build_retention_plan(_job(tmp_path), _toast_plan(), {"shorts": {}})
    q = plan["comment_trigger"]["question"].lower()
    # must not fall back to the generic shopping trigger for a breakfast topic
    assert "al comprar" not in q, q
    assert q.strip()
