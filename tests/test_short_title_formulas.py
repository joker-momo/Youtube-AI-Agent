"""Shorts titles use the 4 scroll-stopper formulas, <=40 chars, age-framed.

A Shorts title's only job is to stop the scrolling thumb. It must be short
(<=40 chars, mobile truncates), hit the problem+solution directly (no cheap
sensationalism for adults 45+), and use one of 4 proven Spanish formulas:
Warning / Quick Win / Myth-Buster / Call Out. The age frame follows the
video's audience (channel floor, or an age the content targets).
"""

from __future__ import annotations

from video_agent.shorts.prompts import short_seo_prompt

CFG = {
    "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+"},
    "audience": {"language": "es-ES", "age_range": [45, 75]},
    "locale_style": {"target_locale": "Spain", "language_code": "es-ES"},
    "seo": {"language": "es-ES"},
}
PLAN = {"title": "dolor de espalda", "format": "pain_to_tip", "viewer_pain": "espalda"}
SCRIPT = {"hook": "Si te duele la espalda", "narration": "Haz esto cada mañana.", "cta": "Sigue"}


def test_prompt_teaches_scroll_stopper_and_40_char_limit():
    p = short_seo_prompt(CFG, PLAN, SCRIPT)
    low = p.lower()
    assert "scroll" in low  # scroll-stopper framing
    assert "40" in p  # 40-char limit present
    # schema title rule tightened to <= 40, not 60
    assert "<= 40" in p or "40 char" in low or "40 caracteres" in low


def test_prompt_lists_all_four_formulas():
    # Call Out marker changed 2026-07-12 (standalone micro-promise): the old
    # "escucha esto" example was a banned context-free fragment; the formula
    # now teaches a topic-named call out ("revisa tu sal").
    p = short_seo_prompt(CFG, PLAN, SCRIPT).lower()
    assert "error al" in p                 # 1 Warning
    assert "60 segundos" in p              # 2 Quick Win
    assert "la verdad científica" in p     # 3 Myth-Buster
    assert "revisa tu sal" in p            # 4 Call Out (topic-named)


def test_age_frame_defaults_to_channel_floor():
    p = short_seo_prompt(CFG, PLAN, SCRIPT)
    assert "45" in p  # channel floor age frame


def test_age_frame_follows_age_specific_content():
    plan = {"title": "alimentos después de los 60", "format": "checklist"}
    script = {"hook": "Si tienes más de 60 años", "narration": "come esto", "cta": ""}
    p = short_seo_prompt(CFG, plan, script)
    # The Call Out / Warning formulas should frame at 60, not a hardcoded 45.
    assert "más de 60" in p or "los 60" in p or "60+" in p


# ── deterministic title validation ────────────────────────────────────────

def test_title_issues_flags_over_40_chars():
    from video_agent.shorts.short_seo_builder import _title_issues

    long_t = "Este título tiene claramente más de cuarenta caracteres"
    issues = _title_issues(long_t, "Este título")
    assert any("40" in i for i in issues)


def test_title_issues_flags_non_formula_and_hook_mismatch():
    from video_agent.shorts.short_seo_builder import _title_issues

    # No formula signal + no shared content word with the hook.
    issues = _title_issues("Consejos generales de nutrición", "El insomnio tras la jubilación")
    assert any("formula" in i.lower() for i in issues)
    assert any("hook" in i.lower() for i in issues)


def test_title_issues_passes_good_formula_aligned_title():
    from video_agent.shorts.short_seo_builder import _title_issues

    # Warning formula, <=40, shares "fruta" with the hook.
    issues = _title_issues("¡Error al comer fruta! (A los 60+)", "Comer fruta así es un error")
    assert issues == []


def test_hard_trim_title_guarantees_40():
    from video_agent.shorts.short_seo_builder import _hard_trim_title

    out = _hard_trim_title("Este título tiene claramente más de cuarenta caracteres")
    assert len(out) <= 40


def test_bare_question_is_not_a_formula():
    """A plain '¿Café en ayunas?' is NOT a Myth-Buster (needs 'La verdad')."""
    from video_agent.shorts.short_seo_builder import _title_issues

    for q in ("¿Café en ayunas?", "¿Pan por la noche?"):
        issues = _title_issues(q, q)  # same text => hook aligns; only formula should flag
        assert any("formula" in i.lower() for i in issues), q


def test_myth_buster_with_la_verdad_passes():
    from video_agent.shorts.short_seo_builder import _title_issues

    issues = _title_issues("¿Café en ayunas? La verdad", "Café en ayunas y el café")
    assert not any("formula" in i.lower() for i in issues)


def test_fallback_title_is_valid_formula_from_hook():
    from video_agent.shorts.short_seo_builder import _fallback_title_from_hook, _title_issues

    hook = "El insomnio tras la jubilación"
    fb = _fallback_title_from_hook(hook, 60)
    assert len(fb) <= 40
    assert _title_issues(fb, hook) == []       # valid formula + aligned + <=40
    assert "insomnio" in fb.lower()            # carries a real hook word


def test_fallback_preserves_accents_from_hook():
    from video_agent.shorts.short_seo_builder import _fallback_title_from_hook

    # The recovered hook word must keep its accent ("título", not "titulo").
    # "título" leads the hook so it's the first qualifying content word even
    # after the >=3-char floor (short real words like "pan" now also count,
    # bug-523 follow-up — reordered so this test isn't confounded by that).
    fb = _fallback_title_from_hook("El título largo y el pan", 45)
    assert "título" in fb
    assert "titulo" not in fb.lower().replace("título", "")


# --- standalone micro-promise for Shorts titles (2026-07-12, mirrors bug-530) --


def test_prompt_call_out_example_names_a_topic_not_escucha_esto():
    """Call Out must end with the concrete topic; 'escucha esto' hides it."""
    p = short_seo_prompt(CFG, PLAN, SCRIPT)
    assert "escucha esto" not in p.lower()
    assert "deja de hacer esto" not in p.lower()
    low = p.lower()
    assert "esto" in low or "concrete topic" in low  # ban is explained
    assert "concrete topic" in low


def test_title_issues_flags_deictic_esto_titles():
    from video_agent.shorts.short_seo_builder import _title_issues

    issues = _title_issues(
        "Si tienes más de 60, escucha esto", "La sal oculta daña tu corazón"
    )
    assert any("esto" in i.lower() for i in issues)


def test_title_issues_passes_topic_named_call_out():
    from video_agent.shorts.short_seo_builder import _title_issues

    issues = _title_issues(
        "Si tienes más de 60, revisa tu sal", "La sal oculta daña tu corazón"
    )
    assert issues == []


def test_fallback_title_never_ships_deictic_esto():
    from video_agent.shorts.short_seo_builder import _fallback_title_from_hook

    # Pathological empty hook — the last-resort title must still name value,
    # never a context-free 'escucha esto'.
    fallback = _fallback_title_from_hook("", 60)
    assert "esto" not in fallback.lower()
    assert len(fallback) <= 40


def test_idea_prompt_requires_topic_object_in_hook_text():
    from video_agent.shorts.idea_prompts import short_ideas_prompt

    p = short_ideas_prompt(CFG, {"full_narration": "x", "title": "t"})
    low = p.lower()
    assert "hook_text" in low
    assert "concrete topic" in low
    assert "señales clave" in low  # named as the banned vague pattern
