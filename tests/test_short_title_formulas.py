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
    p = short_seo_prompt(CFG, PLAN, SCRIPT).lower()
    assert "error al" in p                 # 1 Warning
    assert "60 segundos" in p              # 2 Quick Win
    assert "la verdad científica" in p     # 3 Myth-Buster
    assert "escucha esto" in p             # 4 Call Out


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
    fb = _fallback_title_from_hook("El pan y el título largo", 45)
    assert "título" in fb
    assert "titulo" not in fb.lower().replace("título", "")
