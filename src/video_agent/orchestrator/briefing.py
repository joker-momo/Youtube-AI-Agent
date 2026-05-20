from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.contracts import repo_root

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

# Per-stage explicit schema descriptions. These supplement (not replace)
# the v2 prompt's schema summary so the model has unambiguous types and
# bounds. Format is a JSON-Schema-ish shorthand to keep the prompt short.
_SCHEMA_ES = {
    "script": (
        "{\n"
        '  "channel_id": str (= channel_id dado),\n'
        '  "job_id": str (= job_id dado),\n'
        '  "hook": str (60-140 caracteres, una frase),\n'
        '  "sections": array de 4-6 objetos,\n'
        "    cada objeto: {\n"
        '      "title": str (3-30 caracteres),\n'
        '      "focus": str (10-80 caracteres)\n'
        "    },\n"
        '  "narration": str (110-150 palabras = 50-60 s a 145 wpm),\n'
        '  "cta": str (10-100 caracteres),\n'
        '  "qa": { "verdict": "PENDING_GEMINI_QA" }\n'
        "}"
    ),
    "scenes": (
        "{\n"
        '  "channel_id": str (= channel_id dado),\n'
        '  "job_id": str (= job_id dado),\n'
        '  "total_duration_sec": int (50-65),\n'
        '  "scenes": array de exactamente 4-6 objetos,\n'
        "    cada objeto: {\n"
        '      "id": str (formato \\"scene-NN\\" secuencial),\n'
        '      "duration_sec": int (8-15),\n'
        '      "narration": str (en español, sub-bloque del narration aprobado),\n'
        '      "on_screen_text": str (3-6 palabras en español),\n'
        '      "caption": str (una frase corta en español),\n'
        '      "visual_prompt": str (EN INGLÉS, descriptivo, stock-friendly),\n'
        '      "motion": str ("slow push-in" | "gentle pan" | "slow zoom-out" | "static"),\n'
        '      "asset_refs": objeto vacío {} (no array)\n'
        "    },\n"
        '  "qa": { "verdict": "PENDING_GEMINI_QA" }\n'
        "}\n"
        "REGLA DURA: la suma de duration_sec de las escenas DEBE igualar "
        "total_duration_sec exactamente."
    ),
    "seo": (
        "{\n"
        '  "job_id": str (= job_id dado),\n'
        '  "channel_id": str (= channel_id dado),\n'
        '  "title": str (50-70 caracteres, en es-419, sin clickbait),\n'
        '  "description": str (300-600 caracteres, 2-3 párrafos),\n'
        '  "tags": array de 5-8 strings (es-419, sin duplicados),\n'
        '  "language": "es-419",\n'
        '  "ai_disclosure": true,\n'
        '  "thumbnail_path": str (ruta relativa, por ej. "thumbnail.jpg")\n'
        "}"
    ),
}

# Length contracts: short, declarative, easy to verify mentally.
_LENGTH_ES = {
    "script": (
        "- hook: 60-140 caracteres, una sola frase, sin clickbait.\n"
        "- sections: 4-6 secciones, cada title 3-30 chars, focus 10-80 chars.\n"
        "- narration: 110-150 palabras (~50-60 s a 145 palabras/min).\n"
        "- cta: 10-100 caracteres, llamado claro y no agresivo."
    ),
    "scenes": (
        "- total_duration_sec: 50-65 segundos.\n"
        "- 4-6 escenas, duración 8-15 s cada una.\n"
        "- La suma de duration_sec debe igualar total_duration_sec.\n"
        "- visual_prompt en inglés (5-25 palabras), describe un plano real.\n"
        "- on_screen_text en español, 3-6 palabras, sin signos finales."
    ),
    "seo": (
        "- title: 50-70 caracteres.\n"
        "- description: 300-600 caracteres, 2-3 párrafos cortos.\n"
        "- tags: 5-8 elementos, en español neutro (es-419), sin duplicados."
    ),
}

# Sub-task decomposition: forces the model to plan before emitting JSON.
_DECOMP_ES = {
    "script": [
        "1. Identifica el dolor o duda principal de la idea.",
        "2. Escribe el hook (60-140 chars) que conecte con ese dolor.",
        "3. Define 4-6 sections que cubran la respuesta práctica.",
        "4. Redacta narration uniendo hook + secciones + cta (110-150 palabras).",
        "5. Cuenta palabras de narration. Ajusta si está fuera de rango.",
        "6. Solo entonces construye el JSON.",
    ],
    "scenes": [
        "1. Lee narration del script aprobado y elige un total_duration_sec entre 50 y 65 s.",
        "2. Divide narration en 4-6 bloques sucesivos cuyas duraciones sumen total_duration_sec.",
        "3. Para cada bloque: redacta on_screen_text (3-6 palabras en español), caption (1 frase), visual_prompt (5-25 palabras en INGLÉS, estilo de búsqueda en banco de imágenes), motion (de la lista permitida).",
        "4. Asigna ids scene-01, scene-02... en orden.",
        "5. Vuelve a sumar duration_sec. Si no coincide con total_duration_sec, ajusta una escena para cuadrar.",
        "6. asset_refs DEBE ser {} (objeto), nunca [] (array).",
        "7. Solo entonces construye el JSON.",
    ],
    "seo": [
        "1. Extrae el tema y el beneficio principal del script y las escenas.",
        "2. Redacta un title de 50-70 chars sin clickbait ni promesas médicas.",
        "3. Redacta la description en 2-3 párrafos cortos: hook breve, contenido, CTA suave.",
        "4. Genera 5-8 tags en es-419, todos relevantes, sin duplicados, sin frases prohibidas.",
        "5. Confirma language=es-419, ai_disclosure=true, thumbnail_path razonable.",
        "6. Solo entonces construye el JSON.",
    ],
}

# Concrete bad-tone examples to avoid (concise; risk of bias is lower
# than including full positive few-shots because we never tell the
# model to imitate these).
_NEGATIVE_TERMS_ES = [
    "milagro",
    "milagroso",
    "cura definitiva",
    "garantizado",
    "100% efectivo",
    "comprobado científicamente",
    "el mejor",
    "secreto",
    "fácil y rápido",
    "experto número uno",
]


def _join_list(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value or "")


def _channel_summary(channel_config: dict) -> str:
    ch = channel_config.get("channel", {})
    aud = channel_config.get("audience", {})
    niche = channel_config.get("niche", {})
    positioning = channel_config.get("positioning", {})
    qa_thresholds = channel_config.get("qa_rules", {}).get("thresholds", {})
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
        parts.append(f"- FRASES PROHIBIDAS (nunca usar): {_join_list(forbidden)}.")
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


def _read_brand_voice(channel_config: dict) -> str:
    """Load the channel's brand-voice markdown if present.

    The channel YAML declares ``brand_voice_path`` relative to the
    repo root. Returns the file's text stripped, or empty string if
    the path is missing or unreadable.
    """
    rel = channel_config.get("brand_voice_path")
    if not rel:
        return ""
    candidate = repo_root() / rel
    if not candidate.exists():
        return ""
    try:
        return candidate.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def build_stage_briefing(
    channel_config: dict,
    stage_name: str,
    *,
    job_id: str,
    channel_id: str,
) -> str:
    """First message of a per-stage temp chat: role + channel DNA.

    Includes role description, channel summary, brand-voice file, and
    absolute constraints (job_id pin, language, accents, medical-safety,
    forbidden tone words, JSON-only output, anti-hallucination, refuse
    format). Asks the model to acknowledge with ``OK`` so it commits the
    briefing before the task arrives.
    """
    role = _ROLES_ES.get(stage_name, _ROLES_ES["script"])
    summary = _channel_summary(channel_config)
    brand_voice = _read_brand_voice(channel_config)
    forbidden_tone = ", ".join(_NEGATIVE_TERMS_ES)

    blocks = [
        "# Rol",
        role,
        "",
        "# Contexto del canal",
        summary,
    ]
    if brand_voice:
        blocks.extend(
            [
                "",
                "# Guía de voz del canal (brand voice)",
                brand_voice,
            ]
        )
    blocks.extend(
        [
            "",
            "# Restricciones absolutas (válidas para todo lo que respondas)",
            f'- Usa exactamente job_id="{job_id}" y channel_id="{channel_id}" '
            "en todos los artefactos. No los inventes ni los acortes.",
            "- Responde siempre en español neutro (es-419).",
            "- Conserva los acentos correctos. Nunca uses transliteraciones.",
            "- Nunca des consejos médicos específicos; sugiere consultar a un "
            "profesional cuando aplique.",
            f"- Evita estas palabras manipulativas: {forbidden_tone}.",
            "- Cuando el formato de salida sea JSON, devuelve UN SOLO objeto "
            "JSON válido, sin texto adicional, sin bloques de código markdown.",
            "- Si tu primera respuesta no fuera JSON puro, autocorrígete y "
            "reenvía solo el JSON sin disculpas ni explicaciones.",
            "",
            "# Anti-alucinación",
            "- Si la idea no provee datos concretos (estadísticas, citas, "
            "fuentes, nombres propios, marcas), NO los inventes.",
            "- Usa lenguaje cualitativo: \"muchas personas reportan...\", "
            "\"algunos estudios sugieren...\".",
            "- Si no puedes cumplir alguna restricción, marca el artefacto "
            "como `qa.verdict=NEEDS_REWORK` con `qa.issues` describiendo el "
            "problema en vez de inventar contenido.",
            "",
            "Responde solo `OK` para confirmar que entendiste, y espera mi "
            "próximo mensaje con la tarea concreta.",
        ]
    )
    return "\n".join(blocks)


def build_task_prompt(
    stage_name: str,
    existing_prompt: str,
) -> str:
    """Second message of the session: v2 task + schema + length + decomp + self-check."""
    schema = _SCHEMA_ES.get(stage_name, "")
    length = _LENGTH_ES.get(stage_name, "")
    decomp = _DECOMP_ES.get(stage_name, [])

    blocks = [
        "# Tarea (v2)",
        existing_prompt,
    ]
    if schema:
        blocks.extend(["", "# Esquema explícito de salida", schema])
    if length:
        blocks.extend(["", "# Contrato de longitud", length])
    if decomp:
        blocks.extend(["", "# Pasos a seguir antes de escribir el JSON"])
        blocks.extend(decomp)
    blocks.extend(
        [
            "",
            "# Antes de responder, autocheck silencioso",
            "1. ¿job_id y channel_id coinciden EXACTAMENTE con los que te di?",
            "2. ¿Idioma es-419 con acentos correctos?",
            "3. ¿Respeto las frases prohibidas y uso las preferidas cuando aplica?",
            "4. ¿Esquema completo, tipos correctos, longitudes dentro de los rangos del contrato?",
            "5. ¿Sin palabras manipulativas (milagro, cura, garantizado, etc.)?",
            "6. ¿Sin estadísticas, citas, marcas o nombres inventados?",
            "7. ¿Devolveré un único objeto JSON válido sin markdown ni comentarios?",
            "Si alguno falla, NO devuelvas el artefacto: devuelve",
            "`{\"qa\":{\"verdict\":\"NEEDS_REWORK\",\"issues\":[descripción de cada fallo]}}`.",
            "",
            "Ahora responde **únicamente** el JSON solicitado.",
        ]
    )
    return "\n".join(blocks)
