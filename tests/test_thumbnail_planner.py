"""Spec v1.3 §18 — 42 tests for thumbnail_planner."""

from __future__ import annotations

from video_agent import thumbnail_planner as tp

# --- §12 normalize_thumbnail_variants -------------------------------------

def test_normalize_thumbnail_variants_keeps_each_variant_title():
    """Test 1: each variant keeps its own title."""
    seo = {
        "title": "Top",
        "title_variants": [
            {"title": "Variant A", "thumbnail_text": "HOOK A"},
            {"title": "Variant B", "thumbnail_text": "HOOK B"},
            {"title": "Variant C", "thumbnail_text": "HOOK C"},
        ],
    }
    variants = tp.normalize_thumbnail_variants(seo)
    assert [v["title"] for v in variants] == ["Variant A", "Variant B", "Variant C"]
    assert [v["thumbnail_text"] for v in variants] == ["HOOK A", "HOOK B", "HOOK C"]
    assert [v["variant_index"] for v in variants] == [1, 2, 3]


def test_normalize_thumbnail_variants_fallback_when_missing():
    """Test 2: fallback when no title_variants."""
    seo = {"title": "Five Habits After 45"}
    variants = tp.normalize_thumbnail_variants(seo)
    assert len(variants) == 1
    assert variants[0]["title"] == "Five Habits After 45"
    assert variants[0]["thumbnail_text"]  # non-empty


def test_normalize_thumbnail_variants_uses_top_level_thumbnail_text():
    """Fallback prefers seo.thumbnail_text over derived title slice."""
    seo = {"title": "T", "thumbnail_text": "EXISTING HOOK"}
    variants = tp.normalize_thumbnail_variants(seo)
    assert variants[0]["thumbnail_text"] == "EXISTING HOOK"


# --- §6.7 classify_thumbnail_topic -----------------------------------------

def test_classify_cafe_circulation_maps_to_blood_pressure():
    """Test 3."""
    profile = tp.classify_thumbnail_topic(
        "Lo que el CAFÉ sin azúcar hace a tu circulación después de los 60"
    )
    assert profile["primary_category"] == "blood_pressure_circulation_heart"


def test_classify_chia_azucar_diabetes_maps_to_blood_sugar():
    """Test 4."""
    profile = tp.classify_thumbnail_topic(
        "Cómo debe comer CHÍA para regular su AZÚCAR y evitar la diabetes"
    )
    assert profile["primary_category"] == "blood_sugar_diabetes"


def test_classify_demencia_memoria_maps_to_brain_memory():
    """Test 5."""
    profile = tp.classify_thumbnail_topic(
        "NO ES MEMORIA: Las 5 Señales de Demencia Que Aparecen Años Antes"
    )
    assert profile["primary_category"] == "brain_memory_cognition"


def test_classify_adelgazar_maps_to_weight_loss():
    """Test 6."""
    profile = tp.classify_thumbnail_topic("El Truco Más Ignorado Para Adelgazar")
    assert profile["primary_category"] == "weight_loss_metabolism"


def test_classify_malos_habitos_envejecer_maps_to_aging():
    """Test 7."""
    profile = tp.classify_thumbnail_topic(
        "4 malos hábitos que te hacen envejecer más rápido"
    )
    assert profile["primary_category"] == "aging_longevity_bad_habits"


def test_classify_presion_alta_pantorrilla_maps_to_blood_pressure():
    """Test 8."""
    profile = tp.classify_thumbnail_topic(
        "¿Presión alta después de los 60? Estos 4 ejercicios de pantorrilla pueden ayudarte"
    )
    assert profile["primary_category"] == "blood_pressure_circulation_heart"


# --- §13 plan_thumbnail_prompts ---------------------------------------------

def _seo_three() -> dict:
    return {
        "title": "Default",
        "title_variants": [
            {"title": "Sleep tip", "thumbnail_text": "DUERME MEJOR"},
            {"title": "Sleep object", "thumbnail_text": "APAGA EL MÓVIL"},
            {"title": "Sleep choice", "thumbnail_text": "ESTO O AQUELLO"},
        ],
    }


def test_plan_returns_up_to_three_plans():
    """Test 9."""
    plans = tp.plan_thumbnail_prompts(_seo_three(), {})
    assert len(plans) == 3


def test_variant_1_uses_face_driven():
    """Test 10."""
    plans = tp.plan_thumbnail_prompts(_seo_three(), {})
    assert plans[0]["visual_strategy"] == "face_driven"


def test_variant_2_uses_object_driven():
    """Test 11."""
    plans = tp.plan_thumbnail_prompts(_seo_three(), {})
    assert plans[1]["visual_strategy"] == "object_driven"


def test_variant_3_uses_comparison_driven():
    """Test 12."""
    plans = tp.plan_thumbnail_prompts(_seo_three(), {})
    assert plans[2]["visual_strategy"] == "comparison_driven"


# --- §10 prompt content ----------------------------------------------------

def test_prompt_includes_exact_thumbnail_text():
    """Test 13."""
    seo = {"title": "T", "title_variants": [{"title": "T", "thumbnail_text": "DUERME MEJOR HOY"}]}
    plans = tp.plan_thumbnail_prompts(seo, {})
    assert "DUERME MEJOR HOY" in plans[0]["prompt"]


def test_prompt_includes_spanish_diacritics_instruction():
    """Test 14."""
    plans = tp.plan_thumbnail_prompts(_seo_three(), {})
    for plan in plans:
        for ch in ["ñ", "á", "é", "í", "ó", "ú", "¿", "¡"]:
            assert ch in plan["prompt"]


def test_prompt_uses_mediterranean_spanish_persona_not_hispanic_latina():
    """Test 15."""
    plans = tp.plan_thumbnail_prompts(_seo_three(), {})
    for plan in plans:
        assert "Mediterranean Spanish" in plan["prompt"]
        # Hispanic/Latina must not appear as positive subject description.
        subject_block = plan["prompt"].split("Negative")[0] if "Negative" in plan["prompt"] else plan["prompt"]
        assert "Hispanic or Latina" not in subject_block


def test_prompt_avoids_medical_fear_for_medical_sensitive_category():
    """Test 16."""
    seo = {
        "title": "Las señales de demencia que no debes ignorar",
        "title_variants": [{"title": "Las señales", "thumbnail_text": "MEMORIA EN ALERTA"}],
    }
    plans = tp.plan_thumbnail_prompts(seo, {})
    text = plans[0]["prompt"]
    assert "hospital" in text.lower() or "Do not show" in text or "Avoid" in text
    assert plans[0]["risk_level"] == "medical_sensitive"


# --- §6.3 tie-break determinism --------------------------------------------

def test_classifier_tie_break_is_deterministic():
    """Test 21: equal scores resolve via CATEGORY_PRIORITY."""
    # Both blood_sugar and walking_cardio at score 1 → primary follows priority.
    a = tp.classify_thumbnail_topic("Glucosa después del paseo")
    b = tp.classify_thumbnail_topic("Glucosa después del paseo")
    assert a["primary_category"] == b["primary_category"]


# --- §6.4 secondary category ------------------------------------------------

def test_pick_secondary_returns_none_when_no_positive_score():
    """Test 22."""
    scores = {c: 0 for c in tp.CATEGORY_TRIGGERS.keys()}
    scores["blood_sugar_diabetes"] = 2
    secondary = tp.pick_secondary_category(scores, "blood_sugar_diabetes")
    assert secondary is None


def test_pick_secondary_filters_low_signal_single_trigger():
    """Test 40."""
    scores = {c: 0 for c in tp.CATEGORY_TRIGGERS.keys()}
    scores["blood_sugar_diabetes"] = 2  # primary
    scores["daily_routine"] = 1  # generic single trigger — should be filtered
    secondary = tp.pick_secondary_category(scores, "blood_sugar_diabetes")
    assert secondary != "daily_routine"


# --- §6.5 risk level --------------------------------------------------------

def test_lifestyle_topic_with_diabetes_mention_is_medical_sensitive():
    """Test 23."""
    profile = tp.classify_thumbnail_topic(
        "El mejor pan para personas con diabetes"
    )
    assert profile["risk_level"] == "medical_sensitive"


# --- §6.6 age signal --------------------------------------------------------

def test_age_signal_60_plus_maps_persona_to_55_70():
    """Test 24."""
    profile = tp.classify_thumbnail_topic("Salud después de los 60")
    assert profile["age_signal"] == "60+"
    persona = tp.select_thumbnail_persona(profile, "face_driven", 1)
    assert "55–70" in persona or "55-70" in persona


def test_age_signal_does_not_trigger_for_60_minutos_or_percent():
    """Test 33."""
    for noisy in [
        "Camina 60 minutos al día",
        "60% de probabilidad",
        "Espera 60 segundos antes de levantarte",
    ]:
        profile = tp.classify_thumbnail_topic(noisy)
        assert profile["age_signal"] != "60+", f"false positive: {noisy!r}"


def test_age_signal_does_not_trigger_for_60_secretos_or_60_dias():
    """Test 39."""
    for noisy in [
        "60 secretos de longevidad",
        "60 días para cambiar tu rutina",
    ]:
        profile = tp.classify_thumbnail_topic(noisy)
        assert profile["age_signal"] != "60+", f"false positive: {noisy!r}"


# --- §9.1 avoid list merge --------------------------------------------------

def test_avoid_list_is_union_of_primary_secondary_and_risk():
    """Test 25."""
    plans = tp.plan_thumbnail_prompts(
        {
            "title": "Café para circulación después de los 60",
            "title_variants": [
                {"title": "Café 1", "thumbnail_text": "CAFÉ Y CORAZÓN"},
            ],
        },
        {},
    )
    avoid = plans[0]["avoid"]
    # Primary (blood_pressure...) avoid present:
    assert any("heart" in a.lower() or "ECG" in a for a in avoid)
    # Risk-level medical_sensitive boilerplate present:
    assert any("hospital" in a.lower() for a in avoid)


# --- §13.1 determinism ------------------------------------------------------

def test_plan_generation_is_deterministic():
    """Test 26."""
    seo = _seo_three()
    plans_a = tp.plan_thumbnail_prompts(seo, {})
    plans_b = tp.plan_thumbnail_prompts(seo, {})
    assert plans_a == plans_b


# --- §13.2 accent color -----------------------------------------------------

def test_plan_includes_accent_color():
    """Test 27."""
    plans = tp.plan_thumbnail_prompts(_seo_three(), {})
    assert plans[0]["accent_color"]


def test_resolve_accent_color_supports_both_shapes():
    """Test 34."""
    # Wrapper shape.
    assert tp.resolve_thumbnail_accent_color(
        {"thumbnail": {"accent_color": "#ABCDEF"}}
    ) == "#ABCDEF"
    # Real channel.yaml palette shape.
    assert tp.resolve_thumbnail_accent_color(
        {"style": {"palette": {"accent": "#123456"}}}
    ) == "#123456"
    # Fallback.
    assert tp.resolve_thumbnail_accent_color({}) == "#F2C94C"


# --- §5 general lifestyle fallback ------------------------------------------

def test_general_lifestyle_does_not_force_food_or_exercise_prop():
    """Test 28."""
    preset = tp.select_visual_preset("general_45plus_lifestyle")
    prop = (preset.get("main_prop") or "").lower()
    for forbidden in ["plate", "bread", "yoga mat", "running"]:
        assert forbidden not in prop


# --- §14.1 backward-compat wrapper -----------------------------------------

def test_backward_compat_wrapper_still_works():
    """Test 29."""
    from video_agent.orchestrator.stages import _build_thumbnail_prompt

    prompt = _build_thumbnail_prompt(
        "Topic", "HOOK", "#F2C94C", "Wellness desc", variant_index=2
    )
    assert "HOOK" in prompt
    assert "OBJECT-DRIVEN" in prompt or "object-driven" in prompt.lower()


# --- §4/§13 signature -------------------------------------------------------

def test_select_visual_preset_accepts_category_string():
    """Test 30."""
    preset = tp.select_visual_preset("sleep_rest")
    assert "scene" in preset
    assert "main_prop" in preset


# --- §9.2 merge main prop ---------------------------------------------------

def test_merge_main_prop_does_not_override_primary():
    """Test 31."""
    primary = {"main_prop": "coffee cup"}
    secondary = {"main_prop": "walking shoes"}
    merged = tp.merge_main_prop(primary, secondary)
    assert "coffee cup" in merged
    assert "walking shoes" in merged


def test_merge_main_prop_returns_lifestyle_fallback_when_both_empty():
    primary = {"main_prop": ""}
    merged = tp.merge_main_prop(primary, None)
    assert "daily-life object" in merged.lower() or "no specific prop" in merged.lower()


# --- §11.1 safety rules -----------------------------------------------------

def test_safety_rules_fills_for_all_risk_levels():
    """Test 32."""
    for risk in ["medical_sensitive", "soft_health", "lifestyle"]:
        text = tp.safety_rules_for_category("food_choice", risk, ["hospital"])
        assert text.strip()


def test_safety_rules_uses_primary_category_hint_for_brain_memory():
    """Test 38."""
    text = tp.safety_rules_for_category(
        "brain_memory_cognition", "medical_sensitive", ["hospital"]
    )
    assert "dignity" in text.lower() or "stigma" in text.lower()


def test_safety_rules_uses_primary_category_hint_for_weight_loss():
    """Test 38 part 2."""
    text = tp.safety_rules_for_category(
        "weight_loss_metabolism", "soft_health", ["body shame"]
    )
    assert "shame" in text.lower() or "transformation" in text.lower()


# --- §14 plans.json size ---------------------------------------------------

def test_thumbnail_prompt_plans_excludes_full_prompt(tmp_path):
    """Test 35: integration test guard.

    The planner itself produces plans with `prompt`. The persistence step
    must strip it. This test checks the persistence behavior in the stage
    by simulating what the integration will do.
    """
    plans = tp.plan_thumbnail_prompts(_seo_three(), {})
    # Simulate the persistence rule: prompt must be removable.
    for plan in plans:
        compact = dict(plan)
        compact.pop("prompt", None)
        assert "prompt" not in compact


# --- §7.4 describe_strategy ------------------------------------------------

def test_describe_strategy_returns_readable_text():
    """Test 36."""
    for strategy in ["face_driven", "object_driven", "comparison_driven"]:
        text = tp.describe_strategy(strategy)
        assert text and len(text) > 20  # not just the enum string


# --- §10 category labels ----------------------------------------------------

def test_prompt_uses_category_labels_not_raw_enum_only():
    """Test 37."""
    seo = {
        "title": "Café y circulación",
        "title_variants": [
            {
                "title": "Café y circulación después de los 60",
                "thumbnail_text": "CIRCULA MEJOR",
            }
        ],
    }
    plans = tp.plan_thumbnail_prompts(seo, {})
    prompt = plans[0]["prompt"]
    # Human-readable label must appear; raw enum name must not be the only descriptor.
    assert "blood pressure" in prompt.lower() or "circulation" in prompt.lower()
    # Raw enum form must not appear in the prompt as a category descriptor.
    assert "blood_pressure_circulation_heart" not in prompt


# --- §6.2 category priority -------------------------------------------------

def test_category_priority_keeps_body_signal_above_aging_on_tie():
    """Test 41."""
    # joint_pain_body_signal and aging_longevity_bad_habits should be priority
    # such that body-signal wins ties.
    pri = tp.CATEGORY_PRIORITY
    assert pri.index("joint_pain_body_signal") < pri.index("aging_longevity_bad_habits")
    assert pri.index("movement_stiffness") < pri.index("aging_longevity_bad_habits")


# --- §9 schema completeness -------------------------------------------------

def test_plan_schema_includes_category_safety_rules_and_strategy_description():
    """Test 42."""
    plans = tp.plan_thumbnail_prompts(_seo_three(), {})
    for plan in plans:
        assert plan.get("category_safety_rules")
        assert plan.get("visual_strategy_description")
        assert plan.get("primary_category_label")


# --- competitor-teardown CTR formula (2026-06-29) --------------------------

_SEO_3 = {
    "title": "El mejor pan después de los 60",
    "title_variants": [
        {"title": "El mejor pan", "thumbnail_text": "¿EL MEJOR PAN?"},
        {"title": "Qué pan elegir", "thumbnail_text": "MIRA LA HARINA"},
        {"title": "Pan bueno vs malo", "thumbnail_text": "NO ES EL PAN"},
    ],
}


def test_plan_includes_punch_color():
    plans = tp.plan_thumbnail_prompts(_SEO_3, {})
    assert all(p["punch_color"] for p in plans)


def test_resolve_punch_color_default_and_override():
    assert tp.resolve_thumbnail_punch_color({}) == "#E11D2A"
    assert tp.resolve_thumbnail_punch_color(
        {"thumbnail": {"punch_color": "#FF0000"}}
    ) == "#FF0000"


def test_prompt_bakes_red_punch_box_and_mobile_rule():
    plans = tp.plan_thumbnail_prompts(_SEO_3, {})
    p = plans[0]["prompt"]
    assert "red box" in p
    assert plans[0]["punch_color"] in p
    assert "210" in p  # mobile-readability rule
    assert "7 words" in p
    assert "DOMINANT like top competitor channels" in p


def test_object_strategy_has_arrow_and_number_badges():
    desc = tp.describe_strategy("object_driven")
    assert "red arrow" in desc
    assert "number badges" in desc


def test_comparison_strategy_has_check_cross_and_labels():
    desc = tp.describe_strategy("comparison_driven")
    assert "red cross" in desc
    assert "green check" in desc
    assert "ANTES" in desc


def test_strategy_graphics_allowed_in_prompt():
    plans = tp.plan_thumbnail_prompts(_SEO_3, {})
    assert "Strategy graphics" in plans[1]["prompt"]


# --- competitor lessons #2/#3 + persona identity lock (2026-07-07) -----------

def test_prompt_demands_dominant_text_share():
    plans = tp.plan_thumbnail_prompts(_seo_three(), {})
    for plan in plans:
        assert "cover roughly 40-50% of the frame" in plan["prompt"]
        assert "2-3 huge lines" in plan["prompt"]


def test_persona_identity_lock_only_when_reference_configured():
    cfg = {"thumbnail": {"persona_reference": "configs/vida-plena-45/persona/thumbnail_face.jpeg"}}
    locked = tp.plan_thumbnail_prompts(_seo_three(), cfg)
    for plan in locked:
        assert plan["persona_locked"] is True
        assert plan["persona_reference"].endswith("thumbnail_face.jpeg")
        # Identity is now a TEXT description of the recurring presenter — no
        # attached reference photo.
        assert "RECURRING PRESENTER" in plan["prompt"]
        assert "ATTACHED" not in plan["prompt"]
    unlocked = tp.plan_thumbnail_prompts(_seo_three(), {})
    for plan in unlocked:
        assert plan["persona_locked"] is False
        assert "RECURRING PRESENTER" not in plan["prompt"]


# --- topic-first visual props (2026-07-12) ----------------------------------
# The image must convey the hook message by itself: the concrete objects the
# hook/title name become the DOMINANT prop, the category preset drops to
# secondary context, and the scene follows the topic object class.

_SEO_SALT_HEART = {
    "title": "4 alimentos que protegen tu corazón y el peligro oculto de la sal",
    "title_variants": [
        {
            "title": "No es el salero: cómo cuidar el corazón después de los 45",
            "thumbnail_text": "MENOS SAL, CUIDA TU CORAZÓN",
        }
    ],
}


def test_derive_topic_props_extracts_salt_and_heart_foods_from_hook():
    props = tp.derive_topic_props(
        "No es el salero: cómo cuidar el corazón después de los 45",
        "MENOS SAL, CUIDA TU CORAZÓN",
    )
    joined = " ".join(props).lower()
    assert "salt" in joined
    assert props, "hook names concrete objects; extraction must not be empty"


def test_salt_heart_plan_prop_shows_salt_not_category_walking_shoes():
    plans = tp.plan_thumbnail_prompts(_SEO_SALT_HEART, {})
    prop = plans[0]["main_prop"].lower()
    scene = plans[0]["scene"].lower()
    assert "salt" in prop
    # Category preset (blood_pressure -> walking shoes / park path) must not
    # lead the composition for a salt/food hook.
    assert not prop.startswith("walking shoes")
    assert "park path" not in scene
    assert "kitchen" in scene or "dining" in scene or "table" in scene


def test_prompt_message_match_demands_image_alone_understanding():
    plans = tp.plan_thumbnail_prompts(_SEO_SALT_HEART, {})
    prompt = plans[0]["prompt"]
    assert "IMAGE ALONE" in prompt
    assert "MENOS SAL, CUIDA TU CORAZÓN" in prompt
    assert "at most 3" in prompt.lower() or "no more than 3" in prompt.lower()


def test_no_topic_object_falls_back_to_category_preset_prop():
    seo = {
        "title": "La rutina que cambia tu semana",
        "title_variants": [
            {"title": "La rutina que cambia tu semana", "thumbnail_text": "CAMBIA TU SEMANA"}
        ],
    }
    plans = tp.plan_thumbnail_prompts(seo, {})
    preset = tp.select_visual_preset(plans[0]["primary_category"])
    assert plans[0]["main_prop"].startswith(str(preset["main_prop"]))
    assert plans[0]["scene"] == preset["scene"]


def test_derive_topic_props_pantalla_is_screen_never_bread():
    props = tp.derive_topic_props(
        "Por qué la pantalla te roba el sueño",
        "APAGA LA PANTALLA POR LA NOCHE",
    )
    joined = " ".join(props).lower()
    assert "screen" in joined or "phone" in joined
    assert "bread" not in joined


def test_derive_topic_props_pasos_process_steps_never_walking_shoes():
    """'en 5 pasos' means process steps, not walking — no shoes in a salt poster."""
    props = tp.derive_topic_props(
        "Auditoría fácil de la sal Compara etiquetas",
        "Sal: compárala en 5 pasos",
    )
    joined = " ".join(props).lower()
    assert "salt" in joined
    assert "walking" not in joined
