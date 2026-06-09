"""Regression tests for SEO context-leak fix (spec v1.2).

Covers two system bugs in the bread/pan SEO path:
1. The SEO prompt hardcoded a "5 errores" title and a fixed hashtag set for
   every bread/pan Short, leaking an error-list framing onto checklist /
   label-reading / purchase-rule Shorts.
2. The SEO validator returned early when ``must_preserve_count`` was false,
   so format/topic mismatches were never caught.
"""
from __future__ import annotations

import json

from video_agent.shorts import prompts, short_seo_builder
from video_agent.shorts.idea_preservation import validate_seo_idea_consistency


def _cfg() -> dict:
    return {
        "channel": {"id": "vida-plena-45", "pillar": "nutricion"},
        "shorts": {
            "duration": {"min_sec": 20, "target_max_sec": 60},
            "funnel": {"cta_max_words": 8},
        },
    }


def _checklist_plan() -> dict:
    return {
        "short_id": "short-05",
        "title": "La regla de compra para no equivocarte con el pan",
        "format": "checklist",
        "viewer_pain": "el frontal del envase confunde al comprar pan",
        "practical_payoff": "girar el paquete y leer la etiqueta",
    }


def _severities(issues) -> set[str]:
    return {issue.severity for issue in issues}


def _types(issues) -> set[str]:
    return {issue.type for issue in issues}


# --- Prompt: hardcodes removed -------------------------------------------------

def test_seo_prompt_drops_hardcoded_error_title_rule():
    prompt = prompts.short_seo_prompt(_cfg(), _checklist_plan(), {"hook": "GIRA EL PAQUETE", "narration": "n"})
    assert "you MUST use exactly one of these two titles" not in prompt
    assert "5 errores con el pan después de los 45" not in prompt


def test_seo_prompt_drops_hardcoded_hashtag_rule():
    prompt = prompts.short_seo_prompt(_cfg(), _checklist_plan(), {"hook": "GIRA EL PAQUETE", "narration": "n"})
    assert "you MUST use exactly these hashtags" not in prompt


def test_seo_prompt_includes_retry_feedback():
    prompt = prompts.short_seo_prompt(
        _cfg(), _checklist_plan(), {"hook": "GIRA EL PAQUETE", "narration": "n"},
        retry_feedback="SEO RETRY FEEDBACK\nFix the title.",
    )
    assert "SEO RETRY FEEDBACK" in prompt
    assert "Fix the title." in prompt


# --- Validator: format-aware error-title block ---------------------------------

def test_checklist_bread_short_rejects_error_title():
    script = {
        "short_format": "checklist",
        "hook": "GIRA EL PAQUETE",
        "narration": "Usa esta lista de 4 puntos para comprar pan.",
        "idea_contract": {
            "must_preserve_count": True,
            "original_count": 4,
            "final_count": 4,
            "count_label": "items",
        },
    }
    seo = {"title": "5 errores con el pan después de los 45"}
    issues = validate_seo_idea_consistency(seo, script)
    assert "seo_title_wrong_format_error_promise" in _types(issues)
    assert "repairable_error" in _severities(issues)


def test_format_check_runs_even_when_must_preserve_count_false():
    """The key early-return regression: format mismatch must be caught
    regardless of idea-count preservation."""
    script = {
        "short_format": "checklist",
        "hook": "GIRA EL PAQUETE",
        "narration": "Usa esta lista de compra para el pan.",
        "idea_contract": {"must_preserve_count": False},
    }
    seo = {"title": "5 errores con el pan después de los 45"}
    issues = validate_seo_idea_consistency(seo, script)
    assert "seo_title_wrong_format_error_promise" in _types(issues)


def test_spelled_out_error_count_is_detected():
    script = {
        "short_format": "checklist",
        "hook": "GIRA EL PAQUETE",
        "narration": "Lista de compra del pan.",
        "idea_contract": {"must_preserve_count": False},
    }
    seo = {"title": "Tres errores al comprar pan después de los 45"}
    issues = validate_seo_idea_consistency(seo, script)
    assert "seo_title_wrong_format_error_promise" in _types(issues)


def test_mistake_list_bread_short_allows_error_title():
    script = {
        "short_format": "mistake_list",
        "hook": "NO ES EL PAN",
        "narration": "Cinco errores que cometes con el pan.",
        "idea_contract": {
            "must_preserve_count": True,
            "original_count": 5,
            "final_count": 5,
            "count_label": "errores",
        },
    }
    seo = {"title": "5 errores con el pan después de los 45"}
    issues = validate_seo_idea_consistency(seo, script)
    assert not any(
        issue.severity in {"blocking_error", "repairable_error"} for issue in issues
    )


def test_label_reading_title_passes():
    script = {
        "short_format": "checklist",
        "hook": "GIRA EL PAQUETE",
        "narration": "Mira harina integral, fibra e ingredientes antes de comprar.",
    }
    seo = {"title": "Gira el paquete: regla para comprar pan"}
    issues = validate_seo_idea_consistency(seo, script)
    assert not any(
        issue.severity in {"blocking_error", "repairable_error"} for issue in issues
    )


def test_label_reading_title_missing_core_action_blocks():
    script = {
        "short_format": "checklist",
        "hook": "GIRA EL PAQUETE",
        "narration": "Mira harina integral, fibra e ingredientes antes de comprar.",
    }
    seo = {"title": "Pan después de los 45: lo que nadie cuenta"}
    issues = validate_seo_idea_consistency(seo, script)
    assert "seo_title_misses_core_action" in _types(issues)


def test_hashtag_specificity_passes_without_nutricion():
    script = {
        "short_format": "checklist",
        "hook": "GIRA EL PAQUETE",
        "narration": "Mira harina integral, fibra e ingredientes antes de comprar.",
    }
    seo = {
        "title": "Gira el paquete: regla para comprar pan",
        "hashtags": ["#alimentacionsaludable", "#comerpan", "#panintegral", "#vida45plus", "#shorts"],
    }
    issues = validate_seo_idea_consistency(seo, script)
    assert not any(
        issue.severity in {"blocking_error", "repairable_error"} for issue in issues
    )


# --- Builder: SEO retry loop ---------------------------------------------------

def test_seo_retry_regenerates_on_repairable_issue(tmp_path):
    """First LLM SEO leaks an error title; builder must retry and accept the
    corrected SEO instead of crashing."""
    plan = _checklist_plan()
    script = {
        "short_format": "checklist",
        "hook": "GIRA EL PAQUETE",
        "narration": "Mira harina integral, fibra e ingredientes antes de comprar.",
    }
    bad = json.dumps({
        "title": "5 errores con el pan después de los 45",
        "description": "Qué pan es mejor después de los 45.",
        "hashtags": ["#alimentacionsaludable", "#comerpan", "#nutricion", "#vida45plus", "#shorts"],
    })
    good = json.dumps({
        "title": "Gira el paquete: regla para comprar pan",
        "description": "Gira el paquete y revisa harina integral, fibra e ingredientes.",
        "hashtags": ["#alimentacionsaludable", "#comerpan", "#panintegral", "#vida45plus", "#shorts"],
    })
    calls = {"n": 0}

    def fake_llm(prompt: str) -> str:
        calls["n"] += 1
        return bad if calls["n"] == 1 else good

    seo = short_seo_builder.build_short_seo(
        tmp_path, "short-05", plan, script, _cfg(), fake_llm,
    )
    assert calls["n"] >= 2
    assert "errores" not in seo["title"].lower()
    assert "gira" in seo["title"].lower()
