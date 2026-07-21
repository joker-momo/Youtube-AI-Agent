from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.audience_age import resolve_target_min_age
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
    "script_qa": (
        "Eres revisor (QA) de guiones para videos cortos de bienestar en YouTube. "
        "Tu función tiene DOS pilares de igual importancia:\n"
        "PILAR 1 — POLÍTICAS DE YOUTUBE (tolerancia cero): Antes de cualquier otra "
        "revisión, evalúas si el contenido cumple con las Políticas de la comunidad "
        "de YouTube. Cualquier indicio de desinformación médica, consejos de salud "
        "peligrosos, clickbait engañoso, contenido sensacionalista, promoción de "
        "suplementos, afirmaciones sin evidencia científica, o contenido que pueda "
        "ser eliminado o demonetizado → verdict=NEEDS_REWORK inmediato. La duda "
        "mínima equivale a incumplimiento.\n"
        "PILAR 2 — CALIDAD TÉCNICA: Verificas esquema, contrato de longitud, tono "
        "del canal, frases prohibidas/preferidas y seguridad médica general.\n"
        "Devuelves UN SOLO JSON con verdict, youtube_policy, scores, issues y "
        "required_changes. PASS solo si AMBOS pilares son perfectos."
    ),
    "scenes_qa": (
        "Eres revisor (QA) de escenas para videos cortos de bienestar en YouTube. "
        "Tu función tiene DOS pilares de igual importancia:\n"
        "PILAR 1 — POLÍTICAS DE YOUTUBE (tolerancia cero): Evalúas si cada escena "
        "(narración, on_screen_text, visual_prompt) cumple con las Políticas de la "
        "comunidad de YouTube. Desinformación médica, afirmaciones engañosas, "
        "imágenes sugestivas o inapropiadas, texto sensacionalista → "
        "verdict=NEEDS_REWORK inmediato. La duda mínima equivale a incumplimiento.\n"
        "PILAR 2 — CALIDAD TÉCNICA: Verificas suma de duraciones, formato de IDs "
        "secuenciales, visual_prompt en inglés, asset_refs como objeto {}, tono y "
        "seguridad médica general.\n"
        "Devuelves UN SOLO JSON con verdict, youtube_policy, scores, issues y "
        "required_changes. PASS solo si AMBOS pilares son perfectos."
    ),
    "seo_qa": (
        "Eres revisor (QA) de SEO de YouTube para un canal de bienestar. "
        "Tu función tiene DOS pilares de igual importancia:\n"
        "PILAR 1 — POLÍTICAS DE YOUTUBE (tolerancia cero): El título, la descripción "
        "y las etiquetas deben cumplir estrictamente las Políticas de la comunidad de "
        "YouTube. Títulos engañosos, thumbnails clickbait, descripciones con promesas "
        "médicas, tags de spam o cualquier elemento que pueda causar demonetización o "
        "eliminación → verdict=NEEDS_REWORK inmediato. La duda mínima equivale a "
        "incumplimiento.\n"
        "PILAR 2 — CALIDAD TÉCNICA: Verificas el language configurado del canal, rango de tags, "
        "ausencia de duplicados, ausencia de frases prohibidas, longitud de "
        "title/description y ai_disclosure=true.\n"
        "Devuelves UN SOLO JSON con verdict, youtube_policy, scores, issues y "
        "required_changes. PASS solo si AMBOS pilares son perfectos."
    ),
}

_QA_SCHEMA = (
    "{\n"
    '  "verdict": "PASS" | "NEEDS_REWORK",\n'
    '  "youtube_policy": {\n'
    '    "compliant": true | false,\n'
    '    "risk_level": "none" | "low" | "medium" | "high",\n'
    '    "violations": array de strings (cita o descripción exacta del problema)\n'
    "  },\n"
    '  "scores": {\n'
    '    "schema_fit": int (1-5),\n'
    '    "channel_fit": int (1-5),\n'
    '    "safety": int (1-5),\n'
    '    "clarity": int (1-5),\n'
    '    "youtube_policy": int (1-5, donde 5=sin ninguna duda, 1=violación clara)\n'
    "  },\n"
    '  "issues": array de strings (descripciones cortas),\n'
    '  "required_changes": array de strings (acciones concretas)\n'
    "}\n"
    "REGLA DE VERDICT:\n"
    "- verdict=PASS SOLO SI: youtube_policy.compliant=true AND risk_level='none' "
    "AND todos los scores >= 4 AND issues=[] AND required_changes=[].\n"
    "- Cualquier duda sobre políticas de YouTube → compliant=false → NEEDS_REWORK."
)


# Per-stage explicit schema descriptions. These supplement (not replace)
# the v2 prompt's schema summary so the model has unambiguous types and
# bounds. Format is a JSON-Schema-ish shorthand to keep the prompt short.
_SCHEMA_ES = {
    "script": (
        "{\n"
        '  "channel_id": str (= channel_id dado),\n'
        '  "job_id": str (= job_id dado),\n'
        '  "hook": str (80-180 caracteres, una o dos frases),\n'
        '  "sections": array de 10-15 objetos,\n'
        "    cada objeto: {\n"
        '      "title": str (3-50 caracteres),\n'
        '      "focus": str (15-150 caracteres)\n'
        "    },\n"
        '  "narration": str (AL MENOS {script_word_floor} palabras = ~{floor_min}+ min a {pace_wpm} wpm; SIN máximo, más largo si aporta valor),\n'
        '  "cta": str (20-250 caracteres),\n'
        '  "qa": { "verdict": "PENDING_GEMINI_QA" }\n'
        "}"
    ),
    "scenes": (
        "{\n"
        '  "channel_id": str (= channel_id dado),\n'
        '  "job_id": str (= job_id dado),\n'
        '  "total_duration_sec": int (= duración hablada del narration aprobado; mínimo {floor_sec}s, SIN tope fijo),\n'
        '  "scenes": array (1 escena cada 30-60 s; normalmente 12-40 según la longitud),\n'
        "    cada objeto: {\n"
        '      "id": str (formato \\"scene-NN\\" secuencial),\n'
        '      "duration_sec": int (30-60),\n'
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
        '  "title": str (50-70 caracteres, en {expected_language}, sin clickbait),\n'
        '  "description": str (mô tả chuẩn vàng 6 phần, 700-1500 caracteres),\n'
        '  "tags": array de 6-10 strings ({expected_language}, mezcla 1-2 broad + '
        '4-8 long-tail, sin duplicados),\n'
        '  "language": "{expected_language}",\n'
        '  "ai_disclosure": true,\n'
        '  "thumbnail_path": str (ruta relativa, por ej. "thumbnail.jpg"),\n'
        '  "suggested_pinned_comments": str (un comentario fijado en español que combine ambas estrategias: una pregunta para interactuar y un CTA con link de suscripción)\n'
        "}"
    ),
    "script_qa": _QA_SCHEMA,
    "scenes_qa": _QA_SCHEMA,
    "seo_qa": _QA_SCHEMA,
}

# Length contracts: short, declarative, easy to verify mentally.
_LENGTH_ES = {
    "script": (
        "- hook: 80-180 caracteres, una o dos frases, sin clickbait.\n"
        "- sections: 10-15 secciones, cada title 3-50 chars, focus 15-150 chars.\n"
        "- narration: AL MENOS {script_word_floor} palabras (~{floor_min}+ min a {pace_wpm} palabras/min). "
        "SIN máximo: un guion más largo es bienvenido siempre que cada sección aporte valor real. "
        "Nunca recortes contenido útil para acortar.\n"
        "- Estructura long-form (mínimo {floor_min} min, sin tope): hook (15-25 s) -> intro promesa "
        "(30-45 s) -> 10-15 secciones, cada una con: explicación + ejemplo o "
        "micro-historia + transición fluida -> resumen (45-60 s) -> "
        "cta (30-45 s). Mantén ritmo y evita relleno; cada sección aporta "
        "un punto único.\n"
        "- cta: 20-250 caracteres, llamado claro y no agresivo, incluye "
        "sugerencia de suscribirse + comentar + ver otro video."
    ),
    "script_qa": (
        "- LONGITUD (obligatorio, mismo contrato que generó el guion): narration "
        "debe tener AL MENOS {script_word_floor} palabras (~{floor_min}+ min a "
        "{pace_wpm} palabras/min). Si tiene MENOS, verdict=NEEDS_REWORK y pide "
        "ampliarlo con ejemplos/pasos/micro-historias.\n"
        "- NO existe máximo: un guion más largo que {script_word_floor} palabras es "
        "CORRECTO. Nunca marques un guion como demasiado largo ni apliques ningún "
        "rango superior fijo de palabras; cualquier tope superior es obsoleto.\n"
    ),
    "scenes": (
        "- total_duration_sec: igual a la duración hablada real del narration aprobado "
        "(~palabras / {pace_wpm} × 60). Mínimo {floor_sec} s, SIN tope fijo. NO inventes "
        "relleno ni estires escenas para alcanzar un número: la duración la fija el guion.\n"
        "- Un cambio de plano cada 30-60 s (normalmente 12-40 escenas según la longitud).\n"
        "- La suma de duration_sec debe igualar total_duration_sec.\n"
        "- visual_prompt en inglés (5-25 palabras), describe un plano real.\n"
        "- on_screen_text en español, 3-6 palabras, sin signos finales.\n"
        "- Cada escena cubre una porción del narration; sigue el orden del "
        "script. Cambios de plano cada 30-60 s evitan monotonía en formato "
        "largo."
    ),
    "seo": (
        "- title: 50-70 caracteres, palabra clave principal en primeros 60 chars.\n"
        "- description: 700-1500 caracteres. Debe seguir estrictamente esta Estructura de Oro (6 secciones separadas por líneas en blanco):\n"
        "    1. Sección 1 (Hook & SEO): 2-3 frases cortas. Comienza con la palabra clave principal en las primeras 25 letras.\n"
        "    2. Sección 2 (Resumen detallado): 2-3 párrafos cortos explicando el contenido y qué aprenderá el espectador, con palabras clave secundarias.\n"
        "    3. Sección 3 (Timestamps / Mốc thời gian): Lista de timestamps en formato 'mm:ss - Título de la sección'. IMPORTANTE: No incluyas ningún enlace aquí.\n"
        "    4. Sección 4 (CTA & Link de suscripción): Llamado a la acción para suscribirse, con el enlace exacto: https://www.youtube.com/channel/UCKUswqsAaLsEkcsgzTuKAmw?sub_confirmation=1\n"
        "    5. Sección 5 (Info del canal, Disclaimer y AI Disclosure): Breve descripción del canal (Vida Plena 45+), descargo de responsabilidad médica y declaración de uso de IA.\n"
        "    6. Sección 6 (Hashtags): 3-5 hashtags relevantes al final.\n"
        "- suggested_pinned_comments: un único comentario fijado sugerido (en español, con emojis cálidos) que combine dos estrategias: una pregunta de enganche al inicio para generar conversación y debate en el público, seguido inmediatamente por un llamado a la acción para suscribirse al canal con el link de suscripción exacto: https://www.youtube.com/channel/UCKUswqsAaLsEkcsgzTuKAmw?sub_confirmation=1\n"
        "- tags: 6-10 elementos, mezcla 1-2 broad (high comp) + 4-8 long-tail "
        "(low comp, score 50+), idioma {expected_language}, sin duplicados."
    ),
}

# Sub-task decomposition: forces the model to plan before emitting JSON.
_DECOMP_ES = {
    "script": [
        "1. Identifica el dolor o duda principal de la idea y el resultado concreto que el espectador {audience_min_age}+ obtiene tras al menos {floor_min} min.",
        "2. Escribe el hook (80-180 chars) que conecte con el dolor + promesa.",
        "3. Define 10-15 sections que cubran la respuesta práctica con profundidad (mínimo {floor_min} min, sin tope): cada sección debe tener un sub-punto único + ejemplo o anécdota.",
        "4. Redacta narration uniendo hook + intro + secciones + resumen + cta. Mínimo: {script_word_floor} palabras (~{floor_min}+ min a {pace_wpm} wpm); sin máximo.",
        "5. Cuenta palabras de narration. Si estás por debajo de {script_word_floor}, agrega más ejemplos, pasos concretos o micro-historias hasta superar el mínimo. No hay límite superior; nunca recortes para acortar.",
        "6. Verifica transiciones fluidas entre secciones (\"además\", \"otro hábito\", \"si esto te suena\"...).",
        "7. Evita relleno: cada párrafo aporta valor concreto; el espectador debe sentir que el tiempo está bien invertido.",
        "8. Solo entonces construye el JSON.",
    ],
    "scenes": [
        "1. Lee narration del script aprobado y fija total_duration_sec = su duración hablada real (~palabras / {pace_wpm} × 60); mínimo {floor_sec}s, sin tope fijo.",
        "2. Divide narration en bloques sucesivos (uno cada ~30-60 s, normalmente 12-40) cuyas duraciones sumen total_duration_sec. No añadas texto que no esté en el narration aprobado.",
        "3. Para cada bloque: redacta on_screen_text (3-6 palabras en español), caption (1 frase), visual_prompt (5-25 palabras en INGLÉS, estilo de búsqueda en banco de imágenes), motion (de la lista permitida).",
        "4. Asigna ids scene-01, scene-02... en orden, hasta scene-NN.",
        "5. Vuelve a sumar duration_sec. Si no coincide con total_duration_sec, ajusta una o dos escenas (preferiblemente la primera o última) para cuadrar exactamente.",
        "6. asset_refs DEBE ser {} (objeto), nunca [] (array).",
        "7. Varía visual_prompt entre escenas para evitar monotonía visual en formato largo.",
        "8. Solo entonces construye el JSON.",
    ],
    "seo": [
        "1. Extrae el tema y el beneficio principal del script y las escenas.",
        "2. Redacta un title de 50-70 chars sin clickbait ni promesas médicas.",
        "3. Redacta la description siguiendo la Estructura de Oro de 6 secciones descrita en el contrato de longitud.",
        "4. Genera las 2 propuestas de comentarios fijados (engagement_boosting y subscriber_growth) con emojis y el link de suscripción correcto.",
        "5. Genera 5-8 tags en {expected_language}, todos relevantes, sin duplicados, sin frases prohibidas.",
        "6. Confirma language={expected_language}, ai_disclosure=true, thumbnail_path razonable y la estructura del JSON.",
        "7. Solo entonces construye el JSON.",
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


def _expected_language(channel_config: dict | None = None) -> str:
    config = channel_config or {}
    return str(
        (config.get("seo") or {}).get("language")
        or (config.get("audience") or {}).get("language")
        or "es-ES"
    )


def _script_length_floor(channel_config: dict | None = None) -> dict[str, int]:
    """Content-driven length contract: a hard MINIMUM only, NO upper cap.

    Derived from channel config (``content_format.duration_sec_min`` × ``tts.pace_wpm``)
    exactly like the script-generation prompt in operator_prompts.py, so the
    generator and the Gemini QA gate agree on the same floor. Previously the QA
    contract here was hardcoded to "2900-4350 palabras (~20-30 min a 145 wpm)",
    which ignored the channel's real pace (120 wpm) and 11-min floor and imposed
    a phantom upper bound — failing correctly-sized scripts (bug-495).
    """
    config = channel_config or {}
    pace_wpm = int((config.get("tts") or {}).get("pace_wpm", 120))
    floor_sec = int((config.get("content_format") or {}).get("duration_sec_min", 660))
    floor_min = round(floor_sec / 60)
    word_floor = int(round(floor_sec / 60 * pace_wpm))
    return {
        "pace_wpm": pace_wpm,
        "floor_sec": floor_sec,
        "floor_min": floor_min,
        "script_word_floor": word_floor,
    }


def _fill_stage_contract(text: str, channel_config: dict | None = None) -> str:
    text = text.replace("{expected_language}", _expected_language(channel_config))
    if "{audience_min_age}" in text:
        # Channel-level floor (briefing has no per-idea signal); the per-video
        # age override is applied in the operator content prompt.
        text = text.replace(
            "{audience_min_age}", str(resolve_target_min_age(channel_config or {}))
        )
    if any(
        token in text
        for token in ("{script_word_floor}", "{floor_min}", "{pace_wpm}", "{floor_sec}")
    ):
        floor = _script_length_floor(channel_config)
        for key, value in floor.items():
            text = text.replace("{" + key + "}", str(value))
    return text


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
            f"idioma {aud.get('language') or _expected_language(channel_config)}."
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


def build_initial_briefing(
    channel_config: dict,
    *,
    kind: str,
    job_id: str,
    channel_id: str,
) -> str:
    """Initial message of a persistent temp chat.

    ``kind`` is ``"writing"`` (sent to ChatGPT before the
    script/scenes/seo stages) or ``"qa"`` (sent to Gemini before the
    QA stages). The pipeline sends this exactly once per tab so the
    model commits the role + channel DNA + hard constraints to the
    conversation context, and the subsequent task messages stay short.
    """
    expected_language = _expected_language(channel_config)
    if kind == "qa":
        role = (
            "Eres revisor (QA) profesional de contenido de video de bienestar "
            "para YouTube. Tu función en esta conversación tiene DOS PILARES "
            "inseparables de igual peso:\n\n"
            "PILAR 1 — POLÍTICAS DE YOUTUBE (tolerancia CERO):\n"
            "Evalúas cada artefacto contra las Políticas de la Comunidad de YouTube "
            "y los Lineamientos de monetización. Cualquier indicio — por pequeño "
            "que sea — de los siguientes problemas OBLIGA a devolver NEEDS_REWORK:\n"
            "• Desinformación médica o de salud (afirmaciones sin evidencia científica sólida)\n"
            "• Consejos médicos específicos o diagnósticos implícitos\n"
            "• Promesas de resultados garantizados ('cura', 'elimina', 'en X días')\n"
            "• Promoción directa o indirecta de suplementos o productos\n"
            "• Títulos, textos en pantalla o descripciones engañosas (clickbait)\n"
            "• Contenido sensacionalista sobre enfermedades, muerte o miedo\n"
            "• Cualquier elemento que pueda resultar en demonetización o eliminación\n"
            "La duda mínima equivale a incumplimiento. No des el beneficio de la duda.\n\n"
            "PILAR 2 — CALIDAD TÉCNICA:\n"
            "Verificas esquema JSON, contrato de longitud y duraciones, tono del "
            f"canal, frases prohibidas/preferidas, idioma {expected_language} y seguridad médica "
            "general.\n\n"
            "Cada vez que te pase un artefacto, devolverás UN SOLO objeto JSON con: "
            "verdict, youtube_policy (compliant, risk_level, violations[]), scores, "
            "issues[] y required_changes[]. Nada más — ningún texto fuera del JSON."
        )
    else:
        role = (
            "Eres un equipo creativo profesional para videos cortos de "
            "bienestar en YouTube: guionista, director de escenas y "
            "especialista en SEO. Yo te pediré tres tareas seguidas en "
            "esta misma conversación: primero el guión, luego las "
            "escenas, luego el SEO. Mantén el mismo tono y respeta las "
            "mismas restricciones en las tres."
        )
    summary = _channel_summary(channel_config)
    brand_voice = _read_brand_voice(channel_config)
    forbidden_tone = ", ".join(_NEGATIVE_TERMS_ES)

    blocks = [
        "# Rol para toda esta conversación",
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
            "# Restricciones absolutas (válidas para TODAS las respuestas)",
            f'- Usa exactamente job_id="{job_id}" y channel_id="{channel_id}" '
            "en cualquier artefacto que generes. No los inventes ni los acortes.",
            f"- Responde siempre en el español configurado del canal ({expected_language}).",
            "- Conserva los acentos correctos. Nunca uses transliteraciones.",
            "- Nunca des consejos médicos específicos; sugiere consultar a un "
            "profesional cuando aplique.",
            f"- Evita estas palabras manipulativas: {forbidden_tone}.",
            "- POLÍTICAS DE YOUTUBE: Toda tu revisión debe reflejar las Políticas "
            "de la Comunidad de YouTube vigentes. Ante la mínima duda de "
            "incumplimiento, devuelve NEEDS_REWORK con youtube_policy.compliant=false.",
            "- Cuando te pida un artefacto, devuelve UN SOLO objeto JSON "
            "válido. Sin texto adicional. Sin bloques de código markdown. "
            "Si tu primera respuesta no fuera JSON puro, autocorrígete y "
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
            "Responde solo `OK` para confirmar que entendiste el rol, el "
            "contexto y las restricciones. Espera mi próximo mensaje con la "
            "primera tarea concreta.",
        ]
    )
    return "\n".join(blocks)


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
    expected_language = _expected_language(channel_config)
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
            f"- Responde siempre en el español configurado del canal ({expected_language}).",
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
    channel_config: dict | None = None,
) -> str:
    """Per-stage task message for a persistent temp chat.

    Sent AFTER ``build_initial_briefing``. Includes a short hat-swap
    line (model already has full role+context+constraints) plus the v2
    task, explicit schema, length contract, decomposition checklist,
    and self-check.
    """
    role_hint = _ROLES_ES.get(stage_name, "")
    expected_language = _expected_language(channel_config)
    schema = _fill_stage_contract(_SCHEMA_ES.get(stage_name, ""), channel_config)
    length = _fill_stage_contract(_LENGTH_ES.get(stage_name, ""), channel_config)
    decomp = [
        _fill_stage_contract(step, channel_config)
        for step in _DECOMP_ES.get(stage_name, [])
    ]

    blocks = [
        f"# Cambia de sombrero: {stage_name}",
        role_hint,
        "",
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
            f"2. ¿Idioma {expected_language} con acentos correctos?",
            "3. ¿Respeto las frases prohibidas y uso las preferidas cuando aplica?",
            "4. ¿Esquema completo, tipos correctos, longitudes dentro de los rangos del contrato?",
            "5. ¿Sin palabras manipulativas (milagro, cura, garantizado, etc.)?",
            "6. ¿Sin estadísticas, citas, marcas o nombres inventados?",
            "7. ¿Devolveré un único objeto JSON válido sin markdown ni comentarios?",
            "Anota cualquier fallo en el campo `qa.issues` con verdict NEEDS_REWORK, pero "
            "SIEMPRE devuelve el artefacto completo — nunca devuelvas solo el objeto QA.",
            "",
            "Ahora responde **únicamente** el JSON solicitado.",
        ]
    )
    return "\n".join(blocks)
