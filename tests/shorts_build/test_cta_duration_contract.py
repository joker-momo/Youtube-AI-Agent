"""bug-505 / Codex 20260709-214518: CTA duration and funnel contracts must agree.

Real incident (café narrated short): the funnel contract allows a 12-word
topic-carrying CTA (~5.3s at 2.25 wps), but short_cta's hard cap was 2.8s with
0.3s fit tolerance — no scene could satisfy both, so scene_narration_fit failed
forever, the repair plan suggested OFF-TOPIC shopping CTAs ("Guárdalo para la
compra"), and script compression looped to failed_hard_blocker.

Contract now: short_cta hard cap (5.5s) fits the max funnel CTA; repairs size
the DURATION to the spoken CTA instead of rewriting its topic; repair headers
no longer blanket-coerce valid graphic scenes.
"""

from __future__ import annotations

from video_agent.shorts.validation.issues import LAYOUT_DURATION_TARGETS, SceneValidationIssue
from video_agent.shorts.validation.repairs import deterministic_scene_fit_repair
from video_agent.shorts.validation.scene_structure import (
    build_scene_repair_plan,
    validate_scene_structure,
)

CAFE_CTA = "Descubre el error del café sin azúcar y sueño en el canal."  # 12 words


def _cta_scene(dur: float) -> dict:
    return {
        "id": "s06",
        "layout": "short_cta",
        "duration_sec": dur,
        "narration": CAFE_CTA,
        "on_screen_text": "EN EL CANAL",
        "caption": "c",
        "visual_prompt": "vertical warm close up",
    }


def test_short_cta_hard_cap_fits_a_12_word_funnel_cta():
    """12 words / 2.25 wps ≈ 5.3s — the hard cap must accommodate it."""
    hard = LAYOUT_DURATION_TARGETS["short_cta"][2]
    assert hard >= 5.4, hard


def test_full_funnel_cta_scene_passes_fit_at_cap():
    scenes = [
        {
            "id": "s01",
            "layout": "short_hook",
            "duration_sec": 2.4,
            "narration": "PRUÉBALO 7 DÍAS.",
            "on_screen_text": "7 DÍAS",
            "caption": "c",
            "visual_prompt": "v vertical",
        },
        _cta_scene(5.4),
    ]
    doc = {"scenes": scenes, "total_duration_sec": 7.8}
    issues = validate_scene_structure(scenes, scenes_doc=doc, script=None, attempt=1)
    hard = [
        i
        for i in issues
        if i.severity in {"blocking_error", "repairable_error"}
        and i.type in {"scene_narration_fit", "duration_cap"}
    ]
    assert hard == [], [f"{i.type}: {i.detail}" for i in hard]


def test_mechanical_repair_extends_cta_duration_instead_of_rewriting_topic():
    """An overflowing CTA scene (2.8s planned, ~5.3s spoken) is EXTENDED within
    the cap — the topic wording is never replaced."""
    scenes = [_cta_scene(2.8)]
    result = deterministic_scene_fit_repair(scenes, script=None)
    assert "extend" in result.get("modes", []), result
    assert scenes[0]["narration"] == CAFE_CTA  # wording untouched
    assert scenes[0]["duration_sec"] <= LAYOUT_DURATION_TARGETS["short_cta"][2]


def test_repair_plan_never_suggests_off_topic_shopping_ctas():
    issue = SceneValidationIssue(
        type="scene_narration_fit",
        scene_id="s06",
        severity="repairable_error",
        detail="Scene s06 narration estimates 5.3s for 2.8s scene (exceeds 0.3s tolerance).",
    )
    plan = build_scene_repair_plan([_cta_scene(2.8)], [issue], script=None)
    text = "\n".join(plan["instructions"])
    assert "Guárdalo para la compra" not in text
    assert "Úsalo en el súper" not in text
    # And it must instruct keeping the funnel wording + fitting the duration.
    assert "Keep the CTA wording" in text
    assert "duration" in text.lower()


def test_repair_header_does_not_blanket_coerce_graphic_scenes():
    issue = SceneValidationIssue(
        type="scene_narration_fit",
        scene_id="s03",
        severity="repairable_error",
        detail="Scene s03 narration estimates 5.0s for 4.0s scene (exceeds 0.3s tolerance).",
    )
    scenes = [
        {
            "id": "s03",
            "layout": "graphic_step_list",
            "duration_sec": 4.0,
            "narration": "Uno: registra sin cambiar nada.",
            "layout_payload": {"steps": []},
        },
    ]
    plan = build_scene_repair_plan(scenes, [issue], script=None)
    text = "\n".join(plan["instructions"])
    assert "Keep s02-s06 as realistic short_tip/short_pain scenes" not in text
    assert "existing valid graphic_* scenes may stay as graphics" in text


# ── Codex live repro 2: payoff sentence merged INTO the short_cta scene ──────
# The approved script had payoff beat "No obtendrás una sentencia, pero sí
# tendencias útiles." and CTA beat "Descubre el error del café sin azúcar en el
# canal." — the scene builder merged BOTH into s08 short_cta (18 words ≈ 8.4s),
# which cannot fit any sane CTA cap. Normalization must relocate the non-CTA
# sentences to their own prior scene (coverage preserved), leaving short_cta
# speaking ONLY the funnel CTA.

PAYOFF = "No obtendrás una sentencia, pero sí tendencias útiles."
MERGED = PAYOFF + " Descubre el error del café sin azúcar en el canal."


def _merged_cta_scenes() -> list[dict]:
    return [
        {
            "id": "s07",
            "layout": "short_tip",
            "duration_sec": 3.5,
            "narration": "Cuatro: mantén los demás hábitos constantes.",
            "on_screen_text": "CONSTANTE",
            "caption": "c",
            "visual_prompt": "v vertical",
        },
        {
            "id": "s08",
            "layout": "short_cta",
            "duration_sec": 2.8,
            "narration": MERGED,
            "on_screen_text": "EN EL CANAL",
            "caption": "c",
            "visual_prompt": "vertical warm close up",
        },
    ]


def test_merged_payoff_cta_scene_is_normalized_not_hard_blocked():
    scenes = _merged_cta_scenes()
    result = deterministic_scene_fit_repair(scenes, script=None)
    out = result["scenes"]
    # exactly ONE short_cta scene, and it speaks ONLY the funnel CTA
    ctas = [s for s in out if s.get("layout") == "short_cta"]
    assert len(ctas) == 1, [s.get("layout") for s in out]
    assert "canal" in ctas[0]["narration"].lower()
    assert "sentencia" not in ctas[0]["narration"].lower()
    # the payoff sentence survives in a NON-cta scene (coverage preserved)
    joined = " ".join(s.get("narration") or "" for s in out if s.get("layout") != "short_cta")
    assert "tendencias útiles" in joined
    relocated = next(s for s in out if s.get("id") == "s08pre")
    assert relocated["retention_function"] == "payoff"
    assert relocated["rhythm_tag"] == "payoff"
    assert relocated["pattern_interrupt"] != "graphic_burst"
    from video_agent.shorts.visual_beat_planner import _is_cta_scene

    assert _is_cta_scene(relocated) is False
    # The CTA end-frame visual must not follow the payoff into mid-video: the
    # relocated scene needs its own non-end-card visual prompt (codex reopen).
    assert relocated.get("visual_prompt") != ctas[0].get("visual_prompt")
    assert str(relocated.get("visual_prompt") or "").strip()
    # everything fits its layout cap now
    from video_agent.shorts.validation.repairs import estimate_fits, scene_hard_cap

    for s in out:
        assert estimate_fits(s.get("narration") or "", float(s["duration_sec"])), s["id"]
        assert float(s["duration_sec"]) <= scene_hard_cap(str(s.get("layout"))), s["id"]
    assert not result.get("regen_required"), result.get("modes")


def test_cta_only_scene_is_left_untouched_by_normalization():
    scenes = [_cta_scene(5.4)]
    result = deterministic_scene_fit_repair(scenes, script=None)
    out = result["scenes"]
    assert len(out) == 1
    assert out[0]["narration"] == CAFE_CTA


def test_resumed_relocated_payoff_drops_stale_cta_metadata():
    scenes = [
        {
            "id": "s08pre",
            "layout": "short_tip",
            "duration_sec": 1.8,
            "narration": "¿También te pasa?",
            "retention_function": "cta",
            "rhythm_tag": "comment",
            "pattern_interrupt": "graphic_burst",
            # resumed artifact cloned the CTA end-frame visual too
            "visual_prompt": _cta_scene(5.4)["visual_prompt"],
        },
        _cta_scene(5.4),
    ]

    deterministic_scene_fit_repair(scenes, script=None)

    from video_agent.shorts.visual_beat_planner import _is_cta_scene

    assert scenes[0]["retention_function"] == "payoff"
    assert scenes[0]["rhythm_tag"] == "payoff"
    assert scenes[0]["pattern_interrupt"] != "graphic_burst"
    assert _is_cta_scene(scenes[0]) is False
    # cloned end-card visual replaced by a payoff-appropriate one
    assert scenes[0]["visual_prompt"] != scenes[1].get("visual_prompt")
    assert str(scenes[0]["visual_prompt"] or "").strip()


# ── single long sentence in a footage scene: split at clause boundary ────────
# Run-4 live repro: "Si dudas si el café te influye, no lo decidas por
# intuición." (12 words ≈ 5.3s) in a 3.2s short_pain scene. Sentence-boundary
# split can't help (one sentence), extend can't reach (cap 4.5), so the old
# repair escalated to script compression — burning LLM attempts on something a
# comma split fixes deterministically, exactly as the builder prompt teaches.


def test_single_long_sentence_footage_scene_splits_at_comma():
    scenes = [
        {
            "id": "s02",
            "layout": "short_pain",
            "duration_sec": 3.2,
            "narration": "Si dudas si el café te influye, no lo decidas por intuición.",
            "on_screen_text": "NO ADIVINES",
            "caption": "c",
            "visual_prompt": "vertical pensive adult kitchen",
            "motion": "push_in",
        }
    ]
    result = deterministic_scene_fit_repair(scenes, script=None)
    out = result["scenes"]
    assert not result.get("regen_required"), result.get("modes")
    # split into two scenes, every word preserved, both fit
    assert len(out) == 2, [s.get("narration") for s in out]
    rejoined = " ".join(s["narration"] for s in out)
    assert (
        rejoined.replace("  ", " ")
        == "Si dudas si el café te influye, no lo decidas por intuición."
    )
    from video_agent.shorts.validation.repairs import estimate_fits

    for s in out:
        assert estimate_fits(s["narration"], float(s["duration_sec"])), s


# ── slideshow_risk deadlock: densest scene is short_checklist ────────────────
# Run-5 live repro: layouts [graphic_step_list, graphic_checklist,
# short_checklist] adjacent → consecutive_dense → slideshow_risk repairable at
# attempt 1. repair_slideshow_density only stripped items payload from
# short_tip/short_pain, so a short_checklist target was unrepairable and the
# run escalated to script compression until the budget died.


def test_slideshow_repair_demotes_short_checklist_target_to_tip():
    from video_agent.shorts.validation.repairs import repair_slideshow_density

    scenes = [
        {
            "id": "s04",
            "layout": "graphic_step_list",
            "duration_sec": 4.4,
            "narration": "Uno: registra sin cambiar nada.",
            "layout_payload": {"steps": [{"label": "1", "text": "registra"}]},
        },
        {
            "id": "s05",
            "layout": "graphic_checklist",
            "duration_sec": 4.9,
            "narration": "Dos: usa la misma taza.",
            "layout_payload": {"items": ["misma taza"]},
        },
        {
            "id": "s06",
            "layout": "short_checklist",
            "duration_sec": 4.6,
            "narration": "Tres: observa calma, digestión y acompañamientos.",
            "on_screen_text": "OBSERVA",
            "layout_payload": {"items": ["calma", "digestión", "acompañamientos"]},
        },
    ]
    issue = SceneValidationIssue(
        type="slideshow_risk",
        scene_id="s06",
        severity="repairable_error",
        detail="Short is too text/list heavy: graphics=2, short_checklist=1, checklist_like=3. Densest checklist/graphic scene: s06 (short_checklist).",
    )
    changed = repair_slideshow_density(scenes, [issue])
    assert changed is True
    s06 = scenes[2]
    assert s06["layout"] == "short_tip"  # no longer checklist-like
    assert "layout_payload" not in s06  # list decoration gone
    assert s06["narration"].startswith("Tres: observa")  # coverage untouched


def test_slideshow_repair_demotes_duplicate_graphic_checklist_target():
    """Run-7 live repro: TWO graphic_checklist scenes => hard_dense slideshow_risk
    with the densest graphic named as target. Demote the named graphic to a
    footage short_tip (narration/coverage kept) so one varied graphic remains."""
    from video_agent.shorts.validation.repairs import repair_slideshow_density

    scenes = [
        {"id": "s03", "layout": "graphic_checklist", "duration_sec": 4.5,
         "narration": "Uno: registra sin cambiar nada.",
         "on_screen_text": "REGISTRA", "visual_prompt": "graphic card",
         "layout_payload": {"items": ["bebida", "tamaño", "hora"]}},
        {"id": "s05", "layout": "graphic_checklist", "duration_sec": 4.9,
         "narration": "Dos: usa la misma taza y adelanta el último café.",
         "on_screen_text": "MISMA TAZA", "visual_prompt": "graphic card",
         "layout_payload": {"items": ["misma taza", "último café"]}},
    ]
    issue = SceneValidationIssue(
        type="slideshow_risk",
        scene_id="s05",
        severity="repairable_error",
        detail="Short is too text/list heavy: graphics=2, graphic_checklist=2, checklist_like=3. Densest checklist/graphic scene: s05 (graphic_checklist).",
    )
    changed = repair_slideshow_density(scenes, [issue])
    assert changed is True
    assert scenes[1]["layout"] == "short_tip"
    assert scenes[1]["narration"].startswith("Dos: usa la misma taza")
    assert scenes[0]["layout"] == "graphic_checklist"  # the other graphic survives


def test_over_cap_scene_is_repaired_without_regen():
    """Run-7b live repro: 'Dos: usa la misma taza y adelanta el último café.'
    (10 words ≈ 4.6s) planned at 4.9s in a short_tip capped at 4.5s. The fit
    repair used to SKIP it (narration fit the planned 4.9s) leaving the
    duration_cap blocker unrepaired -> run terminal. Now the scene is clamped
    to the cap (narration fits within tolerance); every word is preserved."""
    scenes = [{
        "id": "s04", "layout": "short_tip", "duration_sec": 4.9,
        "narration": "Dos: usa la misma taza y adelanta el último café.",
        "on_screen_text": "MISMA TAZA", "caption": "c",
        "visual_prompt": "vertical kitchen cup", "motion": "push_in",
    }]
    result = deterministic_scene_fit_repair(scenes, script=None)
    out = result["scenes"]
    assert not result.get("regen_required"), result.get("modes")
    rejoined = " ".join(s["narration"] for s in out)
    assert rejoined == "Dos: usa la misma taza y adelanta el último café."
    from video_agent.shorts.validation.repairs import scene_hard_cap
    for s in out:
        assert float(s["duration_sec"]) <= scene_hard_cap(str(s.get("layout"))), s


def test_connector_split_when_sentence_cannot_fit_any_cap():
    """A no-comma single sentence too long even for the clamped cap splits at a
    coordinating connector (' y '), keeping every word."""
    from video_agent.shorts.validation.repairs import try_mechanical_split

    long_sent = ("Dos: usa siempre exactamente la misma taza de siempre "
                 "y adelanta bastante el último café con cafeína del día.")
    scene = {"id": "s04", "layout": "short_tip", "duration_sec": 4.5,
             "narration": long_sent, "visual_prompt": "v", "motion": "push_in"}
    parts = try_mechanical_split(scene)
    assert parts is not None and len(parts) == 2
    assert " ".join(p["narration"] for p in parts) == long_sent
    assert parts[1]["narration"].startswith("y ")


def test_fit_tolerance_is_not_defeated_by_float_dust():
    """Run-8 live repro: est 4.8044s vs (4.5 cap + 0.3 tol)=4.8 — the scene
    failed by 4 MILLISECONDS of float dust and the whole run went terminal.
    Fit comparisons must round to the 0.1s the pipeline actually plans in."""
    from video_agent.shorts.validation.repairs import estimate_fits

    n = "Observa sensaciones y revisa acompañamientos. Mantén tus otros hábitos constantes."
    # est(n) ≈ 4.8044 — must count as fitting a 4.5s scene with 0.3 tolerance.
    assert estimate_fits(n, 4.5)


def test_validator_fit_check_uses_same_rounding():
    from video_agent.shorts.validate_scenes import validate_scene_structure

    scenes = [{
        "id": "s06", "layout": "graphic_step_list", "duration_sec": 4.5,
        "narration": "Observa sensaciones y revisa acompañamientos. Mantén tus otros hábitos constantes.",
        "on_screen_text": "MÉTODO", "visual_prompt": "graphic card",
        "layout_payload": {"steps": [{"label": "1", "text": "observa"}]},
    }]
    issues = validate_scene_structure(scenes, scenes_doc={"scenes": scenes}, script=None, attempt=1)
    fit_hard = [i for i in issues if i.type == "scene_narration_fit"
                and i.severity in {"blocking_error", "repairable_error"}]
    assert fit_hard == [], [i.detail for i in fit_hard]


def test_missing_graphic_softens_to_warning_when_no_scene_qualifies():
    """Run-9 live deadlock (bug-509): the validator DEMANDED a graphic for a
    checklist Short, but no scene passed _missing_graphic_candidate, so the
    deterministic promoter could not convert anything without inventing
    content — unrepairable hard issue. When no candidate exists the
    requirement must be a WARNING (clean footage beats a fabricated card)."""
    script = {"short_format": "checklist", "narration": "n", "idea_contract": {"original_count": 4}}
    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.4,
         "narration": "PRUÉBALO 7 DÍAS.", "on_screen_text": "7 DÍAS", "visual_prompt": "v"},
        {"id": "s02", "layout": "short_checklist", "duration_sec": 4.0,
         "narration": "Registra bebidas, tamaño y hora sin cambiar nada.",
         "on_screen_text": "REGISTRA", "visual_prompt": "v"},
        {"id": "s03", "layout": "short_tip", "duration_sec": 4.0,
         "narration": "Usa una taza estable y adelanta el último café.",
         "on_screen_text": "TAZA", "visual_prompt": "v"},
        {"id": "s04", "layout": "short_cta", "duration_sec": 4.9,
         "narration": "Descubre el error del café sin azúcar en el canal.",
         "on_screen_text": "CANAL", "visual_prompt": "v"},
    ]
    from video_agent.shorts.validation.graphic_checks import _missing_graphic_candidate
    assert not any(_missing_graphic_candidate(s) for s in scenes)  # precondition
    issues = validate_scene_structure(scenes, scenes_doc={"scenes": scenes}, script=script, attempt=1)
    hard = [i for i in issues if i.type == "missing_graphic_required"
            and i.severity in {"blocking_error", "repairable_error"}]
    assert hard == [], [i.detail for i in hard]


def test_all_scene_fit_failure_increments_are_guarded_per_attempt():
    """bug-510: one counting site lacked the fit_failure_counted_this_attempt
    guard, so a single scenes attempt was double-counted (build-time
    must_split_or_compress + validation hit) and instantly tripped the >=2
    script-compression escalation — the scene-repair budget never engaged."""
    import inspect
    import re

    from video_agent.shorts.builder.stages import scenes as scenes_stage

    src = inspect.getsource(scenes_stage)
    # Every increment must appear within a few lines AFTER a guard check.
    for m in re.finditer(r"loop\.scene_fit_failures \+= 1", src):
        window = src[max(0, m.start() - 1000): m.start()]
        assert "fit_failure_counted_this_attempt" in window, (
            f"unguarded loop.scene_fit_failures increment at offset {m.start()}"
        )


def test_over_cap_single_sentence_graphic_demotes_to_tip_and_splits():
    """idea-02 live repro: 'Uno cuenta todas las bebidas con cafeína, no solo
    el café.' (~5.1s) in a graphic_step_list capped at 4.5s. Graphics cannot
    split (two identical cards) and the estimate exceeds cap+tolerance so the
    clamp refuses — the run went terminal after burning its scene budget.
    Deterministic fix: demote the over-cap graphic to a footage short_tip and
    split at the comma, keeping every word."""
    scenes = [{
        "id": "s03", "layout": "graphic_step_list", "duration_sec": 4.0,
        "narration": "Uno cuenta todas las bebidas con cafeína, no solo el café.",
        "on_screen_text": "CUENTA TODO", "caption": "c",
        "visual_prompt": "graphic card steps", "layout_payload": {"steps": []},
    }]
    result = deterministic_scene_fit_repair(scenes, script=None)
    out = result["scenes"]
    assert not result.get("regen_required"), result.get("modes")
    rejoined = " ".join(s["narration"] for s in out)
    assert rejoined == "Uno cuenta todas las bebidas con cafeína, no solo el café."
    from video_agent.shorts.validation.repairs import estimate_fits, scene_hard_cap
    for s in out:
        assert not str(s.get("layout")).startswith("graphic_"), s  # demoted
        assert estimate_fits(s.get("narration") or "", float(s["duration_sec"])), s
        assert float(s["duration_sec"]) <= scene_hard_cap(str(s.get("layout"))), s
