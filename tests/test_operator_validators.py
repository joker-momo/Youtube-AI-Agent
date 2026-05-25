"""Spain-first SEO validator tests: dynamic language, placeholder social block, locale lexical."""

from __future__ import annotations

from video_agent.operator_validators import _validate_seo


def _spain_config(**overrides):
    cfg = {
        "seo": {"language": "es-ES", "min_tags": 5, "max_tags": 8},
        "audience": {"language": "es-ES"},
        "positioning": {
            "forbidden_phrases": ["adultos mayores", "tercera edad", "ancianos"],
            "preferred_phrases": ["personas de más de 45 años", "adultos 45+"],
        },
        "locale_style": {
            "language_code": "es-ES",
            "lexical_preferences": {
                "prefer": ["móvil", "ordenador"],
                "avoid": ["celular", "computadora", "adultos mayores"],
            },
        },
    }
    cfg.update(overrides)
    return cfg


def _valid_seo(**overrides):
    seo = {
        "language": "es-ES",
        "title": "Cómo dormir mejor después de los 45",
        "description": "Consejos prácticos para descansar mejor.",
        "tags": ["sueño", "bienestar", "rutina", "descanso", "salud"],
        "thumbnail_text": "DUERME MEJOR",
        "suggested_pinned_comments": "¿Qué consejo probarás esta noche?",
        "ai_disclosure": True,
        "thumbnail_path": "",
    }
    seo.update(overrides)
    return seo


def test_validator_warns_for_qa_reworkable_generic_spanish_language():
    cfg = _spain_config()
    seo = _valid_seo(language="es-419")
    result = _validate_seo(seo, cfg)
    report = result.format_report()
    assert result.is_valid
    assert "language should be 'es-ES'" in report
    assert "Claude QA can force ChatGPT rework" in report
    # Must NOT contain the legacy hard-coded Latin American Spanish phrasing.
    assert "Latin American Spanish" not in report


def test_validator_rejects_nonstandard_wrong_language_with_dynamic_wording():
    cfg = _spain_config()
    seo = _valid_seo(language="es-LA")
    result = _validate_seo(seo, cfg)
    report = result.format_report()
    assert not result.is_valid
    assert "language must be 'es-ES'" in report


def test_validator_blocks_placeholder_social_text_in_description():
    cfg = _spain_config()
    seo = _valid_seo(description="Suscríbete.\nRedes adicionales: no proporcionadas.")
    result = _validate_seo(seo, cfg)
    report = result.format_report().lower()
    assert not result.is_valid
    assert "placeholder social-link text" in report


def test_validator_blocks_placeholder_in_pinned_comment():
    cfg = _spain_config()
    seo = _valid_seo(suggested_pinned_comments="Comentario. Redes adicionales: no proporcionadas")
    result = _validate_seo(seo, cfg)
    assert not result.is_valid
    assert "placeholder social-link text" in result.format_report().lower()


def test_validator_blocks_english_not_provided_placeholder():
    cfg = _spain_config()
    seo = _valid_seo(description="Subscribe. Social links not provided.")
    result = _validate_seo(seo, cfg)
    assert not result.is_valid
    assert "placeholder social-link text" in result.format_report().lower()


def test_validator_warns_on_locale_avoid_terms_for_es_es():
    cfg = _spain_config()
    seo = _valid_seo(description="Usa tu celular y tu computadora.")
    result = _validate_seo(seo, cfg)
    # 'celular' is not in forbidden_phrases (positioning) and not a placeholder,
    # so the SEO can stay valid but should carry a locale warning.
    assert result.is_valid
    warnings = result.format_report().lower()
    assert "celular" in warnings


def test_validator_blocks_forbidden_age_phrase_and_suggests_preferred():
    cfg = _spain_config()
    seo = _valid_seo(description="Para adultos mayores que quieren dormir mejor.")
    result = _validate_seo(seo, cfg)
    report = result.format_report()
    assert not result.is_valid
    assert "Forbidden positioning" in report
    assert "personas de más de 45 años" in report or "adultos 45+" in report


def test_validator_passes_clean_spain_first_seo():
    cfg = _spain_config()
    seo = _valid_seo()
    result = _validate_seo(seo, cfg)
    assert result.is_valid, result.format_report()


def test_validator_uses_audience_language_fallback():
    cfg = {
        "audience": {"language": "es-ES"},
        "seo": {"min_tags": 5, "max_tags": 8},
    }
    seo = _valid_seo(language="es-419")
    result = _validate_seo(seo, cfg)
    assert result.is_valid
    assert "language should be 'es-ES'" in result.format_report()
