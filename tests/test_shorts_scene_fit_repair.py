"""Regression tests for QA storm fix v2.2.

Two mechanisms:
1. Deterministic scene_narration_fit repair (extend -> mechanical split ->
   conservative micro-condense) that runs BEFORE any LLM scene regeneration,
   preserving source fidelity, idea count, covers_items, source_scene_ids,
   and scene/graphic caps.
2. A tiered product-score gate that replaces the 9.0-across-the-board hard
   wall so an ~8/10 Short no longer triggers endless regeneration.
"""
from __future__ import annotations

from video_agent.shorts import qa
from video_agent.shorts.validate_scenes import (
    SceneValidationIssue,
    estimate_spanish_narration_sec,
    estimate_fits,
    scene_hard_cap,
    try_mechanical_split,
    try_micro_condense,
    deterministic_scene_fit_repair,
    build_scene_repair_plan,
    strip_numbered_on_screen_text,
)


def test_strip_numbered_on_screen_text_removes_list_numbering():
    scenes = [
        {"id": "s1", "on_screen_text": "1. MÓVIL PARA RELAJARTE"},
        {"id": "s2", "on_screen_text": "5. SIN OTRA OBLIGACIÓN"},
        {"id": "s3", "on_screen_text": "2) ALGO"},
        {"id": "s4", "on_screen_text": "3: OTRO"},
        {"id": "s5", "on_screen_text": "HAZ UNA PARTE"},  # untouched
    ]
    changed = strip_numbered_on_screen_text(scenes)
    assert changed is True
    assert [s["on_screen_text"] for s in scenes] == [
        "MÓVIL PARA RELAJARTE",
        "SIN OTRA OBLIGACIÓN",
        "ALGO",
        "OTRO",
        "HAZ UNA PARTE",
    ]
    # idempotent: a second pass changes nothing
    assert strip_numbered_on_screen_text(scenes) is False


def test_short_myth_cap_fits_a_natural_two_sentence_myth():
    # short_myth's 3.2s cap only allowed ~7 words, so a natural myth line
    # ("Don't chase the perfect bread. Look at the label.") always overflowed
    # and could not be repaired mechanically. The cap now gives it room.
    assert scene_hard_cap("short_myth") >= 4.0
    myth = "No busques el pan perfecto. Mira la etiqueta."
    assert estimate_fits(myth, scene_hard_cap("short_myth"))


def test_repair_plan_gives_concrete_shrink_target_for_myth_fit():
    issue = SceneValidationIssue(
        type="scene_narration_fit", scene_id="s02", severity="repairable_error",
        detail="Scene s02 narration estimates 5.2s for 3.0s scene (exceeds 0.3s tolerance).",
    )
    scenes = [{"id": "s02", "layout": "short_myth", "duration_sec": 3.0,
               "narration": "No busques el pan perfecto. Usa esta regla simple antes de comprar."}]
    plan = build_scene_repair_plan(scenes, [issue], script={})  # non-checklist
    text = " ".join(plan["instructions"]).lower()
    assert "short sentence" in text
    assert "next scene" in text or "adjacent" in text


def _checklist_script():
    return {
        "idea_contract": {
            "must_preserve_count": True,
            "original_count": 4,
            "final_count": 4,
            "count_label": "items",
        },
        "key_points": [
            {"point": "desconfía del frontal"},
            {"point": "mira harina integral y fibra"},
            {"point": "ingredientes claros"},
            {"point": "el gusto importa"},
        ],
    }


def test_repair_plan_adds_anti_cram_for_checklist_narration_fit():
    issue = SceneValidationIssue(
        type="scene_narration_fit", scene_id="s02", severity="repairable_error",
        detail="Scene s02 narration estimates 8.5s for 3.0s scene (exceeds 0.3s tolerance).",
    )
    scenes = [{"id": "s02", "layout": "short_myth", "duration_sec": 3.0,
               "narration": "No busques el pan perfecto. Usa esta checklist. Uno: desconfía."}]
    plan = build_scene_repair_plan(scenes, [issue], script=_checklist_script())
    text = " ".join(plan["instructions"]).lower()
    assert "cram" in text  # explicit anti-cram guidance
    assert "scene" in text


def test_repair_plan_handles_missing_item_coverage():
    issue = SceneValidationIssue(
        type="missing_item_coverage", scene_id=None, severity="repairable_error",
        detail="Required idea item 4 is not covered by any scene.",
    )
    scenes = [{"id": "s02", "layout": "short_myth", "duration_sec": 3.0, "narration": "x."}]
    plan = build_scene_repair_plan(scenes, [issue], script=_checklist_script())
    text = " ".join(plan["instructions"]).lower()
    assert "dedicated scene" in text
    assert "4" in text  # names the uncovered item


# --- A. estimator guard --------------------------------------------------------

def test_short_narration_does_not_overflow_3s_scene():
    est = estimate_spanish_narration_sec("No busques el pan perfecto.")
    assert est <= 3.3
    assert estimate_fits("No busques el pan perfecto.", 3.0)


# --- C / F. mechanical split, no invent, preserves coverage --------------------

def _scene(**kw):
    base = {
        "id": "s02",
        "layout": "short_myth",
        "duration_sec": 3.0,
        "narration": "No busques el pan perfecto. Mira la etiqueta.",
        "on_screen_text": "NO EL PERFECTO",
        "caption": "Busca una regla simple.",
        "covers_items": [1],
        "source_scene_ids": ["scene-46"],
    }
    base.update(kw)
    return base


def test_mechanical_split_uses_exact_sentences_no_invent():
    parts = try_mechanical_split(_scene())
    assert parts is not None and len(parts) == 2
    assert parts[0]["narration"] == "No busques el pan perfecto."
    assert parts[1]["narration"] == "Mira la etiqueta."
    # No invented on_screen_text: each part's text came from the original fields.
    original_texts = {"NO EL PERFECTO", "Busca una regla simple.",
                      "No busques el pan perfecto.", "Mira la etiqueta."}
    for p in parts:
        assert p.get("on_screen_text", "") in original_texts or p.get("on_screen_text", "") == ""


def test_mechanical_split_preserves_coverage_and_sources():
    parts = try_mechanical_split(_scene())
    assert parts is not None
    union = set()
    for p in parts:
        union |= set(p["covers_items"])
        assert p["source_scene_ids"] == ["scene-46"]
    assert union == {1}


def test_each_split_segment_fits_timing():
    parts = try_mechanical_split(_scene())
    assert parts is not None
    for p in parts:
        assert estimate_fits(p["narration"], p["duration_sec"])


# --- Split rules around a real visual_prompt -----------------------------------

def test_split_rejected_for_graphic_scene_with_visual_prompt():
    # A GRAPHIC scene carries one rendered card; splitting would copy the SAME
    # graphic to both halves (redundant + double-counts the graphic cap) and a
    # distinct graphic cannot be invented. Reject the split for graphic layouts.
    scene = _scene(
        layout="graphic_checklist",
        visual_prompt="Vertical 9:16 checklist card with three bread-label rules.",
        motion="Slow crop shift from front claims to back label.",
    )
    assert try_mechanical_split(scene) is None


def test_split_allowed_for_footage_scene_with_visual_prompt():
    # Real-world s06 storm case: a 2-sentence short_tip footage scene that carries
    # a visual_prompt and overflows its cap. Footage scenes CAN split — both halves
    # share the same b-roll location; the second beat just gets a distinct camera
    # motion so the pair isn't a static slideshow. This kills the recurring
    # scene_narration_fit hard blocker (no LLM regen needed).
    scene = _scene(
        layout="short_tip",
        duration_sec=3.8,
        narration="Si no te gusta, no dura. Prepara un pan base antes del hambre.",
        on_screen_text="QUE TE GUSTE",
        visual_prompt="Vertical 9:16 warm kitchen, adult 45+ tasting toast with tomato.",
        motion="Face cut from toast close-up to a small approving reaction.",
        covers_items=[3],
    )
    parts = try_mechanical_split(scene)
    assert parts is not None and len(parts) == 2
    # Each half fits its cap, all narration preserved, coverage kept on both.
    for p in parts:
        assert estimate_fits(p["narration"], p["duration_sec"])
        assert p["covers_items"] == [3]
    assert parts[0]["narration"] == "Si no te gusta, no dura."
    assert parts[1]["narration"] == "Prepara un pan base antes del hambre."
    # Both halves keep the same b-roll visual; the second beat gets a DISTINCT
    # motion so adjacent scenes don't read as a static slideshow.
    assert parts[0]["visual_prompt"] == parts[1]["visual_prompt"]
    assert parts[1]["motion"] != parts[0]["motion"]


def test_repair_splits_footage_overflow_without_calling_llm():
    # The 2-sentence footage overflow now resolves by mechanical split before any
    # LLM regeneration: a new scene is added and the LLM is never called.
    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.4, "narration": "¿Lo eliges por delante?"},
        {"id": "s02", "layout": "short_tip", "duration_sec": 3.8,
         "narration": "Si no te gusta, no dura. Prepara un pan base antes del hambre.",
         "visual_prompt": "Vertical 9:16 warm kitchen, adult 45+ tasting toast.",
         "covers_items": [3], "source_scene_ids": ["scene-46"]},
        {"id": "s03", "layout": "short_cta", "duration_sec": 2.6, "narration": "Guárdalo."},
    ]
    before = len(scenes)
    calls, regen_fn = _calls_list()
    result = deterministic_scene_fit_repair(scenes, regen_fn=regen_fn)
    assert "split" in result["modes"]
    assert result["regen_called"] is False
    assert calls == []
    assert len(result["scenes"]) == before + 1
    for sc in result["scenes"]:
        assert estimate_fits(sc.get("narration", ""), sc["duration_sec"])


def test_repair_falls_back_to_regen_for_single_sentence_visual_overflow():
    # A single-sentence footage overflow cannot be split (one sentence) and has no
    # whitelisted filler to condense -> defer to LLM regeneration, no new scene.
    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.4, "narration": "¿Lo eliges por delante?"},
        {"id": "s02", "layout": "short_myth", "duration_sec": 3.0,
         "narration": "No busques nunca el pan perfecto del supermercado tradicional moderno.",
         "visual_prompt": "Vertical supermarket close-up of a bread package.",
         "covers_items": [1], "source_scene_ids": ["scene-46"]},
        {"id": "s03", "layout": "short_cta", "duration_sec": 2.6, "narration": "Guárdalo."},
    ]
    before = len(scenes)
    calls, regen_fn = _calls_list()
    result = deterministic_scene_fit_repair(scenes, regen_fn=regen_fn)
    assert "split" not in result["modes"]
    assert result["regen_called"] is True
    assert len(result["scenes"]) == before


# --- D. split must fail when a single sentence still overflows ------------------

def test_split_fails_when_segment_still_overflows():
    long_scene = _scene(
        narration=(
            "No busques el pan perfecto. Usa esta checklist. "
            "Uno: desconfía un poco del frontal y vuelve a lo básico."
        ),
    )
    # The third sentence alone overflows a 3.0s short_myth scene, so no
    # sentence-boundary split can make every segment fit.
    assert try_mechanical_split(long_scene) is None


# --- G / H. micro-condense whitelist + reject ----------------------------------

def test_micro_condense_removes_filler_when_safe():
    scene = {
        "id": "s04",
        "layout": "short_tip",
        "duration_sec": 4.5,
        "narration": "Revisa también la harina integral y un poco la fibra de verdad importante.",
        "covers_items": [2],
        "source_scene_ids": ["scene-50"],
    }
    out = try_micro_condense(scene, idea_labels=["harina", "fibra"])
    assert out is not None
    # Filler tokens gone, idea words kept.
    assert "un poco" not in out["narration"]
    assert "de verdad" not in out["narration"]
    assert "harina" in out["narration"].lower()
    assert "fibra" in out["narration"].lower()
    assert out["covers_items"] == [2]
    assert out["source_scene_ids"] == ["scene-50"]


def test_micro_condense_rejected_when_it_would_drop_idea_item():
    # No whitelisted filler to remove; the only way to shrink would be deleting
    # a sentence that carries an idea item -> must reject (return None).
    scene = {
        "id": "s05",
        "layout": "short_myth",
        "duration_sec": 3.0,
        "narration": "Mira la fibra del pan. Compara los ingredientes del paquete con calma total.",
        "covers_items": [3],
        "source_scene_ids": ["scene-51"],
    }
    assert try_micro_condense(scene, idea_labels=["fibra", "ingredientes"]) is None


# --- B. deterministic repair succeeds without calling the LLM ------------------

def _calls_list():
    calls = []
    def regen_fn(*a, **k):
        calls.append(1)
        return None
    return calls, regen_fn


def test_deterministic_repair_extends_without_calling_llm():
    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.4, "narration": "¿Lo eliges por delante?"},
        {"id": "s02", "layout": "short_tip", "duration_sec": 3.2,
         "narration": "Revisa la harina, la fibra y los ingredientes.",
         "covers_items": [1], "source_scene_ids": ["scene-46"]},
        {"id": "s03", "layout": "short_cta", "duration_sec": 2.6, "narration": "Guárdalo."},
    ]
    # s02 overflows 3.2s but fits within the 4.5s short_tip cap -> extend.
    assert not estimate_fits(scenes[1]["narration"], 3.2)
    calls, regen_fn = _calls_list()
    result = deterministic_scene_fit_repair(scenes, regen_fn=regen_fn)
    assert calls == []  # LLM not called
    assert result["regen_called"] is False
    for sc in result["scenes"]:
        assert estimate_fits(sc.get("narration", ""), sc["duration_sec"])


# --- D (orchestration). unfixable scene falls back to LLM ----------------------

def test_deterministic_repair_falls_back_to_llm_when_unfixable():
    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.4, "narration": "¿Lo eliges por delante?"},
        {"id": "s02", "layout": "short_myth", "duration_sec": 3.0,
         "narration": ("No busques el pan perfecto. Usa esta checklist. "
                       "Uno: desconfía del frontal y vuelve siempre a lo básico esencial."),
         "covers_items": [1], "source_scene_ids": ["scene-46"]},
        {"id": "s03", "layout": "short_cta", "duration_sec": 2.6, "narration": "Guárdalo."},
    ]
    calls, regen_fn = _calls_list()
    result = deterministic_scene_fit_repair(scenes, regen_fn=regen_fn)
    assert result["regen_called"] is True
    assert calls == [1]


# --- E. split respects scene-count cap -----------------------------------------

def test_split_not_attempted_when_at_scene_cap():
    # 12 scenes == max_count; an overflowing scene cannot be split (would exceed
    # the cap) so the repair must not add a scene.
    scenes = [{"id": "s01", "layout": "short_hook", "duration_sec": 2.4, "narration": "¿Lo eliges por delante?"}]
    for i in range(2, 13):
        scenes.append({"id": f"s{i:02d}", "layout": "short_tip", "duration_sec": 4.0, "narration": "Mira la etiqueta."})
    # Make one scene overflow with a clean 2-sentence split available.
    scenes[1]["narration"] = "No busques el pan perfecto. Mira la etiqueta."
    scenes[1]["duration_sec"] = 3.0
    scenes[1]["covers_items"] = [1]
    scenes[1]["source_scene_ids"] = ["scene-46"]
    before = len(scenes)
    calls, regen_fn = _calls_list()
    result = deterministic_scene_fit_repair(scenes, regen_fn=regen_fn)
    assert len(result["scenes"]) == before  # no scene added by split
    assert "split" not in result["modes"]


# --- I / J. tiered product gate ------------------------------------------------

def test_product_score_8_is_not_hard_fail():
    scores = {
        "hook_strength": 8.3, "clarity": 8.3, "retention_pacing": 8.3,
        "visual_specificity": 8.3, "audience_fit_45_plus": 8.3,
        "natural_spanish": 8.3, "saveability": 8.3,
    }
    tier = qa.classify_product_scores(scores)
    assert tier != "hard_block"
    assert tier == "pass_with_warning"


def test_retention_7_hard_blocks():
    scores = {
        "hook_strength": 9.0, "clarity": 9.0, "retention_pacing": 7.0,
        "visual_specificity": 9.0, "audience_fit_45_plus": 9.0,
        "natural_spanish": 9.0, "saveability": 9.0,
    }
    assert qa.classify_product_scores(scores) == "hard_block"


def test_any_dimension_below_7_hard_blocks():
    scores = {
        "hook_strength": 6.5, "clarity": 9.0, "retention_pacing": 9.0,
        "visual_specificity": 9.0, "audience_fit_45_plus": 9.0,
        "natural_spanish": 9.0, "saveability": 9.0,
    }
    assert qa.classify_product_scores(scores) == "hard_block"


def test_publish_target_passes_clean():
    scores = {
        "hook_strength": 8.6, "clarity": 8.6, "retention_pacing": 8.6,
        "visual_specificity": 8.6, "audience_fit_45_plus": 8.6,
        "natural_spanish": 9.0, "saveability": 8.6,
    }
    assert qa.classify_product_scores(scores) == "pass"


def test_visual_first_blocks_low_visual_specificity():
    scores = {
        "hook_strength": 8.5, "clarity": 8.5, "retention_pacing": 8.5,
        "visual_specificity": 7.2, "audience_fit_45_plus": 8.5,
        "natural_spanish": 8.5, "saveability": 8.5,
    }
    assert qa.classify_product_scores(scores, visual_first=True) == "hard_block"
    # Same scores on a non-visual-first Short only need repair, not hard block.
    assert qa.classify_product_scores(scores, visual_first=False) != "hard_block"

def test_repair_visual_only_unreadable_does_not_corrupt_narration_fit():
    from video_agent.shorts.validate_scenes import repair_visual_only_unreadable, deterministic_scene_fit_repair
    
    scene = {
        "id": "s04",
        "layout": "short_tip",
        "duration_sec": 3.6,
        "narration": "Elige uno que te guste.",
        "caption": "Si no gusta, no dura.",
        "on_screen_text": "EL GUSTO CUENTA",
        "covers_items": [2]
    }
    idea_item = {"id": 2, "label": "El gusto también importa, porque comer mejor necesita placer suficiente para sostenerse."}
    
    # Run the coverage repair which previously corrupted scene["narration"]
    repair_visual_only_unreadable([scene], idea_item)
    
    # 1. Spoken text should remain exactly what it was (no label appended)
    assert scene["narration"] == "Elige uno que te guste."
    
    # 2. Run deterministic fit repair to verify estimation
    res = deterministic_scene_fit_repair([scene])
    
    # 3. Must not trigger any regeneration or overflow because 5 words fits easily in 3.6s
    assert res["regen_required"] is False
    assert len(res["logs"]) == 0, "No repair should be attempted since duration fits narration perfectly"

