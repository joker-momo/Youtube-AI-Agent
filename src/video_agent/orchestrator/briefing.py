from __future__ import annotations

from typing import Any

# Stage-specific role descriptions. The orchestrator prepends the
# generic briefing (channel context + hard constraints) and appends
# the stage-specific task prompt.

_ROLES_ES = {
    "script": (
        "Eres guionista profesional de videos cortos de bienestar para "
        "YouTube. Escribes guiones cálidos, claros, realistas y "
        "respetuosos, adaptados al canal indicado. Tu prioridad es "
        "que el contenido sea seguro, útil y emocionalmente cercano."
    ),
    "scenes": (
        "Eres director creativo de videos cortos de bienestar. "
        "Estructuras guiones aprobados en escenas visuales que combinan "
        "narración, texto en pantalla, prompts visuales en inglés (para "
        "bancos de imágenes) y duración exacta. Eres riguroso con el "
        "esquema y el tono del canal."
    ),
    "seo": (
        "Eres especialista en SEO de YouTube para nichos de salud y "
        "bienestar. Generas títulos, descripciones y tags que respetan "
        "el idioma, la audiencia y el posicionamiento del canal. Nunca "
        "usas clickbait ni afirmaciones médicas."
    ),
}


def _join_list(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value or "")


def _channel_summary(channel_config: dict) -> str:
    ch = channel_config.get("channel", {})
    aud = channel_config.get("audience", {})
    niche = channel_config.get("niche", {})
    positioning = channel_config.get("positioning", {})
    qa_thresholds = (
        channel_config.get("qa_rules", {}).get("thresholds", {})
    )
    tts = channel_config.get("tts", {})

    parts = [
        f"- Canal: {ch.get('name', ch.get('id', 'unknown'))} "
        f"({ch.get('id', 'unknown')}).",
        f"- Descripción: {ch.get('description', '')}",
        (
            f"- Audiencia: {_join_list(aud.get('age_range', []))} años, "
            f"mercados principales {_join_list(aud.get('primary_markets', []))}, "
            f"idioma {aud.get('language', 'es-419')}."
        ),
        (
            f"- Nicho: {niche.get('category', '')}, "
            f"sub-nichos {_join_list(niche.get('sub_niches', []))}."
        ),
    ]
    avoid = niche.get("avoid_topics", [])
    if avoid:
        parts.append(f"- Temas a evitar: {_join_list(avoid)}.")
    forbidden = positioning.get("forbidden_phrases", [])
    preferred = positioning.get("preferred_phrases", [])
    if forbidden:
        parts.append(f"- FRASES PROHIBIDAS: {_join_list(forbidden)}.")
    if preferred:
        parts.append(f"- Frases preferidas: {_join_list(preferred)}.")
    if qa_thresholds:
        parts.append(
            "- Reglas QA: "
            + ", ".join(f"{k}={v}" for k, v in qa_thresholds.items())
        )
    pace = tts.get("pace_wpm")
    if pace:
        parts.append(f"- Cadencia TTS: {pace} palabras por minuto.")
    return "\n".join(parts)


def build_stage_briefing(
    channel_config: dict,
    stage_name: str,
    *,
    job_id: str,
    channel_id: str,
) -> str:
    """First message of a per-stage temp chat: role + channel context.

    Returns a Spanish briefing block the model reads before the task
    prompt. We keep it as a single user message (ChatGPT temp chat has
    no system role) and ask the model to acknowledge so it commits the
    instructions to context before the task arrives.
    """
    role = _ROLES_ES.get(stage_name, _ROLES_ES["script"])
    summary = _channel_summary(channel_config)
    return (
        "# Rol\n"
        f"{role}\n\n"
        "# Contexto del canal\n"
        f"{summary}\n\n"
        "# Restricciones absolutas (válidas para todo lo que respondas)\n"
        f"- Usa exactamente job_id=\"{job_id}\" y channel_id=\"{channel_id}\" "
        "en todos los artefactos. No los inventes ni los acortes.\n"
        "- Responde siempre en español neutro (es-419).\n"
        "- Conserva los acentos correctos. Nunca uses transliteraciones.\n"
        "- Nunca des consejos médicos específicos; sugiere consultar a un "
        "profesional cuando aplique.\n"
        "- Cuando el formato de salida sea JSON, devuelve UN SOLO objeto "
        "JSON válido, sin texto adicional, sin bloques de código markdown.\n"
        "- Si no puedes cumplir alguna restricción, marca el artefacto como "
        "`qa.verdict=NEEDS_REWORK` con `qa.issues` describiendo el problema, "
        "en lugar de inventar contenido.\n\n"
        "Responde solo `OK` para confirmar que entendiste, y espera mi "
        "próximo mensaje con la tarea concreta."
    )


def build_task_prompt(existing_prompt: str) -> str:
    """Second message of the session: the existing v2 task prompt + a
    final reminder to actually emit JSON."""
    return (
        "# Tarea\n"
        f"{existing_prompt}\n\n"
        "# Antes de responder, autocheck silencioso\n"
        "1. ¿job_id y channel_id coinciden con los que te di?\n"
        "2. ¿Idioma es-419 con acentos?\n"
        "3. ¿Respeto las frases prohibidas y uso las preferidas cuando aplica?\n"
        "4. ¿Esquema completo y tipos correctos?\n"
        "5. ¿Devolveré un único objeto JSON sin markdown ni comentarios?\n\n"
        "Ahora responde **únicamente** el JSON solicitado."
    )
