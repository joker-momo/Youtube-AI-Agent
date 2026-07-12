"""Acceptance tests for the Vida Plena 45+ long-form thumbnail copy contract.

The regression is not typography. Existing copy can be readable yet too vague
to communicate a reason to click without the title. Clear, dignified practical
value must outrank context-free curiosity fragments.
"""

from __future__ import annotations

import pytest

from video_agent.operator import _chatgpt_seo_prompt
from video_agent.seo.title_scorer import score_variant

CFG = {
    "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+"},
    "audience": {"language": "es-ES", "age_range": [45, 75]},
    "seo": {"language": "es-ES", "min_tags": 5, "max_tags": 8},
}
SCRIPT = {
    "job_id": "thumbnail-copy-acceptance",
    "hook": "Consejos prácticos para mantener autonomía después de los 45.",
    "sections": [],
    "narration": "Contenido práctico, proporcionado y sin promesas médicas.",
    "cta": "Suscríbete al canal.",
}
SCENES = {
    "total_duration_sec": 600,
    "scenes": [{"duration_sec": 20, "narration": "Contenido", "visual_prompt": "home"}],
}


def _prompt() -> str:
    return _chatgpt_seo_prompt(CFG, SCRIPT, SCENES)


def _score(title: str, thumbnail_text: str) -> dict:
    return score_variant({"title": title, "thumbnail_text": thumbnail_text})


def test_prompt_requires_a_standalone_micro_promise_with_semantic_payload():
    prompt = _prompt()

    assert "STANDALONE MICRO-PROMISE" in prompt
    assert "without reading the YouTube title" in prompt
    assert "at least TWO" in prompt
    for signal in ("concrete topic", "pain/problem", "practical outcome", "action/decision"):
        assert signal in prompt


def test_prompt_rejects_context_free_curiosity_and_shows_real_topic_repairs():
    prompt = _prompt()

    for vague in (
        "5 GESTOS CLAVE",
        "¿DUERMES PEOR DESPUÉS?",
        "TU SEMANA TIENE HUECOS",
        "NO ES POR LA HORA",
    ):
        assert vague in prompt
    for clear in (
        "DUERME MEJOR TRAS EL PARTIDO",
        "¿TU CAFÉ EMPEORA EL SUEÑO?",
        "5 ALIMENTOS PARA CUIDAR TUS MÚSCULOS",
        "ACEITE DE OLIVA: CUÁNDO TOMARLO",
    ):
        assert clear in prompt
    assert "context-free" in prompt


def test_prompt_uses_three_audience_fit_angles_not_three_cryptic_rewrites():
    prompt = _prompt()

    assert "pain-led clarity" in prompt
    assert "outcome-led practical hope" in prompt
    assert "action/decision-led specificity" in prompt
    assert "dignity" in prompt
    assert "autonomy" in prompt
    assert "imperative + age" in prompt.lower()
    assert "not mandatory" in prompt.lower()


@pytest.mark.parametrize(
    ("title", "vague", "clear"),
    [
        (
            "Cómo dormir mejor después de los partidos nocturnos del Mundial",
            "5 GESTOS CLAVE",
            "DUERME MEJOR TRAS EL PARTIDO",
        ),
        (
            "Cómo saber si el café sin azúcar afecta tu sueño",
            "¿DUERMES PEOR DESPUÉS?",
            "¿TU CAFÉ EMPEORA EL SUEÑO?",
        ),
        (
            "Alimentos para proteger músculos y huesos después de los 60",
            "TU SEMANA TIENE HUECOS",
            "5 ALIMENTOS PARA CUIDAR TUS MÚSCULOS",
        ),
        (
            "Cómo tomar aceite de oliva cada mañana de forma práctica",
            "NO ES POR LA HORA",
            "ACEITE DE OLIVA: CUÁNDO TOMARLO",
        ),
    ],
)
def test_clear_real_topic_copy_materially_outranks_vague_copy(
    title: str, vague: str, clear: str
):
    vague_result = _score(title, vague)
    clear_result = _score(title, clear)

    assert clear_result["score"] >= vague_result["score"] + 10, (
        vague_result,
        clear_result,
    )
    vague_detail = vague_result["breakdown"]["thumbnail_detail"]
    clear_detail = clear_result["breakdown"]["thumbnail_detail"]
    assert vague_detail["vagueness_penalty"] > clear_detail["vagueness_penalty"]
    assert clear_detail["standalone_value_score"] > vague_detail["standalone_value_score"]


def test_score_breakdown_exposes_auditable_audience_quality_components():
    result = _score(
        "Cómo proteger los músculos después de los 60",
        "5 ALIMENTOS PARA CUIDAR TUS MÚSCULOS",
    )
    detail = result["breakdown"]["thumbnail_detail"]

    assert set(
        (
            "standalone_value_score",
            "audience_fit_score",
            "vagueness_penalty",
            "trust_penalty",
        )
    ).issubset(detail)
    assert 0 <= result["score"] <= 100


def test_proportionate_practical_copy_beats_unsupported_fear():
    title = "Cómo influye el café de la tarde en el descanso"

    practical = _score(title, "CAFÉ: CUÁNDO PUEDE AFECTAR TU SUEÑO")
    fear = _score(title, "EL CAFÉ ARRUINA TU SALUD")

    assert practical["score"] > fear["score"]
    assert fear["breakdown"]["thumbnail_detail"]["trust_penalty"] > 0
    assert practical["breakdown"]["thumbnail_detail"]["audience_fit_score"] > 0


def test_existing_score_variant_contract_remains_backward_compatible():
    result = _score(
        "5 hábitos para dormir mejor después de los 45",
        "5 HÁBITOS PARA DORMIR MEJOR",
    )

    assert isinstance(result["score"], int)
    assert 0 <= result["score"] <= 100
    assert "title_score" in result["breakdown"]
    assert "thumbnail_score" in result["breakdown"]


@pytest.mark.parametrize(
    ("title", "clear"),
    [
        (
            "Cómo cuidar la memoria después de los 60 con hábitos sencillos",
            "CUIDA TU MEMORIA DESPUÉS DE LOS 60",
        ),
        (
            "Cómo aliviar la rigidez de rodillas después de los 45",
            "ALIVIA LA RIGIDEZ DE TUS RODILLAS",
        ),
        (
            "Alimentos con colágeno para cuidar tus articulaciones",
            "COLÁGENO PARA CUIDAR TUS ARTICULACIONES",
        ),
        (
            "Qué pasa si comes avena todos los días después de los 45",
            "AVENA: QUÉ PASA SI LA COMES",
        ),
    ],
)
def test_semantic_quality_generalizes_beyond_the_four_acceptance_fixtures(
    title: str, clear: str
):
    """The scorer must infer concrete topic alignment from the paired title;
    it cannot recognize only the nouns present in the original four fixtures."""
    clear_result = _score(title, clear)
    vague_result = _score(title, "5 SEÑALES CLAVE")

    assert clear_result["score"] >= vague_result["score"] + 10, (
        clear_result,
        vague_result,
    )
    clear_detail = clear_result["breakdown"]["thumbnail_detail"]
    assert clear_detail["vagueness_penalty"] == 0
    assert clear_detail["standalone_value_score"] > 0


def test_prompt_says_complementary_but_complete_without_contradicting_standalone_copy():
    prompt = _prompt()

    assert "COMPLEMENTARY, NOT REPETITIVE" in prompt
    assert "complementary but complete" in prompt.lower()
    assert "Do NOT use the thumbnail to summarize the video" not in prompt
