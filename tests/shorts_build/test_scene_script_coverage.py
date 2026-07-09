"""bug-503 / Codex task 20260709-202252: deterministic source-fidelity coverage guard.

The Shorts scene builder must PRESERVE the script's content, splitting long
sentences across scenes rather than dropping them. Whole-beat drops and CTA-topic
drops are objective, so a deterministic guard hard-fails them regardless of the
Gemini judge's mood; a faithful paraphrase that keeps the content PASSES.

Real incident: the café 7-day Short dropped the hook's second sentence, the payoff
'Tendrás tendencias más útiles.', and the CTA topic 'café sin azúcar y sueño',
landing at 23.2s (< 35s) — scene_qa hard-blocked. These tests lock the fix.
"""

from __future__ import annotations

from video_agent.shorts.validation.scene_structure import (
    _validate_script_coverage,
    validate_scene_structure,
)

CAFE_SCRIPT = {
    "hook": "PRUÉBALO 7 DÍAS",
    "narration": (
        "PRUÉBALO 7 DÍAS. Si no sabes si el café influye realmente, sigue este plan. "
        "Primero, registra sin cambiar nada. Después, usa siempre la misma taza y adelanta "
        "el último café. Luego, observa sueño, digestión y calma, revisando también los "
        "acompañamientos. Por último, mantén el resto de hábitos lo más constantes posible. "
        "Tendrás tendencias más útiles. Descubre el error del café sin azúcar y sueño en el canal."
    ),
    "cta": "Descubre el error del café sin azúcar y sueño en el canal.",
}

# The real failing scenes: hook 2nd sentence, payoff, and CTA topic all dropped.
BAD_SCENES = [
    {"id": "s01", "layout": "short_hook", "narration": "PRUÉBALO 7 DÍAS", "duration_sec": 2.4},
    {"id": "s02", "layout": "short_tip", "narration": "Primero, registra sin cambiar nada.", "duration_sec": 4.2},
    {"id": "s03", "layout": "short_tip", "narration": "Usa la misma taza y adelanta el último café.", "duration_sec": 4.5},
    {"id": "s04", "layout": "short_tip", "narration": "Observa sueño, digestión, calma y acompañamientos.", "duration_sec": 4.5},
    {"id": "s05", "layout": "short_checklist", "narration": "Mantén los otros hábitos lo más constantes posible.", "duration_sec": 4.8},
    {"id": "s06", "layout": "short_cta", "narration": "Descubre el error en el canal.", "duration_sec": 2.8},
]

# Content-complete scenes: every beat preserved (s04 lightly paraphrased) by splitting.
GOOD_SCENES = [
    {"id": "s01", "layout": "short_hook", "narration": "Pruébalo 7 días.", "duration_sec": 1.8},
    {"id": "s02", "layout": "short_hook", "narration": "Si no sabes si el café influye realmente, sigue este plan.", "duration_sec": 3.4},
    {"id": "s03", "layout": "short_tip", "narration": "Primero, registra sin cambiar nada.", "duration_sec": 3.2},
    # paraphrased (adelanta -> toma antes) but keeps siempre/misma/taza/último/café:
    {"id": "s04", "layout": "short_tip", "narration": "Después, usa siempre la misma taza y toma antes el último café.", "duration_sec": 4.2},
    {"id": "s05", "layout": "short_tip", "narration": "Luego, observa sueño, digestión y calma, revisando también los acompañamientos.", "duration_sec": 4.8},
    {"id": "s06", "layout": "short_checklist", "narration": "Por último, mantén el resto de hábitos lo más constantes posible.", "duration_sec": 4.5},
    {"id": "s07", "layout": "short_tip", "narration": "Tendrás tendencias más útiles.", "duration_sec": 2.6},
    {"id": "s08", "layout": "short_cta", "narration": "Descubre el error del café sin azúcar y sueño en el canal.", "duration_sec": 4.0},
]


def _fidelity(issues):
    return [i for i in issues if i.type == "source_fidelity"]


def test_valid_paraphrase_is_not_hard_failed():
    """A faithful paraphrase that keeps every beat must NOT be flagged (not exact-match)."""
    fid = _fidelity(_validate_script_coverage(GOOD_SCENES, CAFE_SCRIPT))
    assert fid == [], [i.detail for i in fid]


def test_dropping_hook_second_sentence_is_hard_failed():
    scenes = [s for s in GOOD_SCENES if s["id"] != "s02"]
    fid = _fidelity(_validate_script_coverage(scenes, CAFE_SCRIPT))
    assert any("influye" in i.detail or "sigue este plan" in i.detail for i in fid)


def test_dropping_payoff_sentence_is_hard_failed():
    scenes = [s for s in GOOD_SCENES if s["id"] != "s07"]
    fid = _fidelity(_validate_script_coverage(scenes, CAFE_SCRIPT))
    assert any("tendencias más útiles" in i.detail.lower() for i in fid)


def test_cta_losing_topic_phrase_is_hard_failed():
    scenes = [dict(s) for s in GOOD_SCENES]
    scenes[-1]["narration"] = "Descubre el error en el canal."  # drops café/azúcar/sueño
    fid = _fidelity(_validate_script_coverage(scenes, CAFE_SCRIPT))
    assert any("topic" in i.detail.lower() for i in fid)


def test_dropping_single_qualifier_realmente_is_hard_failed():
    """Codex reopen: a single dropped qualifier must fail even though 5/6 words
    survive. Scene truncates the script sentence, removing 'realmente'."""
    scenes = [dict(s) for s in GOOD_SCENES]
    # s02 speaks the hook 2nd sentence; drop only 'realmente'.
    scenes[1]["narration"] = "Si no sabes si el café influye, sigue este plan."
    fid = _fidelity(_validate_script_coverage(scenes, CAFE_SCRIPT))
    assert any("realmente" in i.detail.lower() for i in fid), [i.detail for i in fid]


def test_dropping_nada_qualifier_is_hard_failed():
    """'sin cambiar nada' -> 'sin cambiar' flips the meaning; must hard-fail."""
    scenes = [dict(s) for s in GOOD_SCENES]
    # s03 speaks 'Primero, registra sin cambiar nada.'; drop 'nada'.
    scenes[2]["narration"] = "Primero, registra sin cambiar."
    fid = _fidelity(_validate_script_coverage(scenes, CAFE_SCRIPT))
    assert any("nada" in i.detail.lower() for i in fid), [i.detail for i in fid]


def test_faithful_paraphrase_with_substitute_words_still_passes():
    """Guard against over-correction: swapping 'adelanta' -> 'toma antes' keeps the
    meaning and ADDS words, so it must NOT be flagged as a dropped qualifier."""
    fid = _fidelity(_validate_script_coverage(GOOD_SCENES, CAFE_SCRIPT))
    assert fid == [], [i.detail for i in fid]


def test_real_cafe_failure_flags_multiple_source_fidelity_drops():
    fid = _fidelity(_validate_script_coverage(BAD_SCENES, CAFE_SCRIPT))
    # hook 2nd sentence + payoff + CTA topic all missing -> at least 3 drops.
    assert len(fid) >= 3, [i.detail for i in fid]


def test_coverage_guard_is_wired_into_validate_scene_structure():
    doc = {"scenes": BAD_SCENES, "total_duration_sec": 23.2}
    issues = validate_scene_structure(BAD_SCENES, scenes_doc=doc, script=CAFE_SCRIPT, attempt=1)
    fid = [i for i in issues if i.type == "source_fidelity"]
    assert fid, "validate_scene_structure must surface the dropped-content source_fidelity issues"
    assert all(i.severity == "repairable_error" for i in fid)


def test_content_complete_scenes_recover_duration_above_28s():
    """Codex #4: duration recovers to ~28-35s by ADDING scenes, not stretching."""
    total = sum(s["duration_sec"] for s in GOOD_SCENES)
    assert 28.0 <= total <= 38.0
    assert _fidelity(_validate_script_coverage(GOOD_SCENES, CAFE_SCRIPT)) == []
