"""Build ``short_source_map.json`` linking a Short back to long-video scenes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Pillar → short Spanish topic noun for a spoken CTA that names the companion
# long video's theme ("Más sobre el sueño en el canal.") instead of a generic
# "Vídeo completo en el canal.". Kept short so the CTA stays within cta_max_words.
_PILLAR_TOPIC_ES: dict[str, str] = {
    "sleep": "el sueño", "sueño": "el sueño", "sueno": "el sueño", "rest": "el sueño",
    "nutrition": "la alimentación", "nutricion": "la alimentación", "food": "la alimentación",
    "movement": "el movimiento", "mobility": "el movimiento", "exercise": "el movimiento",
    "walking": "el movimiento",
    "stress": "la calma", "mind": "la calma", "anxiety": "la calma",
    "energy": "la energía", "fatigue": "la energía",
    "weight": "el peso", "metabolism": "el peso",
    "digestion": "la digestión", "fiber": "la digestión",
    "heart": "la circulación", "blood_pressure": "la circulación", "circulation": "la circulación",
    "blood_sugar": "el azúcar", "diabetes": "el azúcar",
    "memory": "la memoria", "brain": "la memoria", "cognition": "la memoria",
    "joint": "las articulaciones", "joints": "las articulaciones", "pain": "las articulaciones",
    "protein": "el músculo", "muscle": "el músculo",
    "hydration": "la hidratación",
    "routine": "el hábito", "habits": "el hábito",
}


# Keyword → topic fallback for when a Short plan carries no pillar (common —
# bug-484). Accent-insensitive stems; scanned in order so a specific health topic
# wins over generic "la alimentación", which is checked last.
_TOPIC_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("la memoria", ("memoria", "cerebro", "olvid", "demencia", "alzheimer", "concentra")),
    ("el sueno_ACCENT", ("dormir", "sueno", "insomnio", "descanso", "despertar", "de noche")),
    ("la circulacion_ACCENT", ("circulaci", "presion", "corazon", "tension", "pantorrilla")),
    ("el azucar_ACCENT", ("glucosa", "diabetes", "insulina", "prediabetes")),
    ("la digestion_ACCENT", ("digesti", "intestino", "hinchazon", "estrenim", "fibra")),
    ("el peso", ("adelgaz", "grasa", "cintura", "metabolismo", "barriga", "perder peso")),
    ("las articulaciones", ("articulaci", "rodilla", "espalda", "cuello", "cadera", "rigidez")),
    ("el musculo_ACCENT", ("musculo", "proteina", "sarcopenia", "fuerza")),
    ("el movimiento", ("caminar", "movili", "ejercicio", "estirar", "andar", "paseo", "pasos")),
    ("la energia_ACCENT", ("energia", "cansancio", "cansad", "fatiga", "agotam", "bajon")),
    ("la calma", ("estres", "ansiedad", "calma", "nervios", "preocupa")),
    ("la hidratacion_ACCENT", ("hidrata", "beber agua", "vaso de agua", "sed ")),
    ("la alimentacion_ACCENT", (
        "aceite", "oliva", "pan", "comer", "comida", "alimentaci", "plato",
        "desayuno", "cena", "yogur", "fruta", "verdura", "dieta", "nutrici", "azucar",
    )),
]

# Restore accents on the returned topic (kept accent-free above for readability).
_TOPIC_ACCENTS = {
    "el sueno_ACCENT": "el sueño",
    "la circulacion_ACCENT": "la circulación",
    "el azucar_ACCENT": "el azúcar",
    "la digestion_ACCENT": "la digestión",
    "el musculo_ACCENT": "el músculo",
    "la energia_ACCENT": "la energía",
    "la hidratacion_ACCENT": "la hidratación",
    "la alimentacion_ACCENT": "la alimentación",
}


def _strip_accents(text: str) -> str:
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFKD", str(text).lower())
        if not unicodedata.combining(c)
    )


def funnel_topic_es(short_plan: dict, extra_text: str = "") -> str:
    """Resolve the Short's topic to a short Spanish noun, or '' when unknown.

    Prefers the explicit pillar; when it is missing/unmapped (bug-484), scans the
    plan's text (title, hook, viewer pain, payoff, narration seed) plus any
    ``extra_text`` (e.g. the parent long video's title) for topic keywords so the
    CTA still specializes even when the plan carries no topic fields."""
    short_plan = short_plan or {}
    pillar = str(short_plan.get("pillar") or short_plan.get("detected_pillar") or "").strip().lower()
    if pillar in _PILLAR_TOPIC_ES:
        return _PILLAR_TOPIC_ES[pillar]

    haystack = _strip_accents(
        " ".join(
            [str(short_plan.get(k) or "")
             for k in ("title", "hook_text", "hook", "viewer_pain",
                       "practical_payoff", "narration_seed", "topic")]
            + [str(extra_text or "")]
        )
    )
    if haystack.strip():
        for topic, stems in _TOPIC_KEYWORDS:
            if any(stem in haystack for stem in stems):
                return _TOPIC_ACCENTS.get(topic, topic)
    return ""


def is_generic_cta(funnel_cfg: dict, cta: str) -> bool:
    """True when ``cta`` is one of the plain (non-topic) default phrases, so a
    caller can treat it as 'not specific' and prefer a topic-aware CTA instead."""
    funnel_cfg = funnel_cfg or {}
    generics = {
        str(funnel_cfg.get("default_cta_without_url") or ""),
        str(funnel_cfg.get("default_cta_with_url") or ""),
        "Vídeo completo en el canal.",
    }
    generics.discard("")
    return str(cta or "").strip() in generics


def resolve_funnel_cta(funnel_cfg: dict, short_plan: dict, *, has_url: bool, extra_text: str = "") -> str:
    """The spoken CTA that bridges a Short to its long video.

    Prefers a topic-aware template (``cta_topic_template_with/without_url`` with a
    ``{tema}`` placeholder) so the CTA names the theme; falls back to the plain
    ``default_cta_with/without_url`` when no topic template is set or the topic
    can't be resolved. Centralized here so the script prompt and this source-map
    validator use the SAME expected CTA and never drift.
    """
    funnel_cfg = funnel_cfg or {}
    tema = funnel_topic_es(short_plan or {}, extra_text=extra_text)
    tpl_key = "cta_topic_template_with_url" if has_url else "cta_topic_template_without_url"
    tpl = str(funnel_cfg.get(tpl_key) or "")
    if tema and "{tema}" in tpl:
        return tpl.replace("{tema}", tema)
    default_key = "default_cta_with_url" if has_url else "default_cta_without_url"
    return str(funnel_cfg.get(default_key) or "Vídeo completo en el canal.")


def _long_scene_index(long_job_dir: Path) -> dict[str, dict]:
    p = long_job_dir / "scenes.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {str(s.get("id")): s for s in (data.get("scenes") or [])}


def _long_title(long_job_dir: Path) -> str:
    # Different pipeline versions store the long-form artifacts at the job root or
    # under json/; try both, and fall back to the script title, so the topic is
    # always recoverable for the funnel CTA.
    for rel in ("seo.json", "json/seo.json", "script.json", "json/script.json"):
        p = long_job_dir / rel
        if not p.exists():
            continue
        try:
            title = str(json.loads(p.read_text(encoding="utf-8")).get("title", "")).strip()
        except Exception:
            title = ""
        if title:
            return title
    return ""


def build_source_map(
    long_job_dir: Path,
    short_plan: dict,
    short_script: dict,
    channel_config: dict,
    long_video_url: str = "",
) -> dict[str, Any]:
    idx = _long_scene_index(long_job_dir)
    used = []
    for sid in short_plan.get("scene_ids") or short_plan.get("source_scene_ids") or []:
        scene = idx.get(str(sid), {})
        used.append(
            {
                "scene_id": sid,
                "source_start_sec": scene.get("audio_offset_sec", short_plan.get("source_start_sec")),
                "source_end_sec": (
                    float(scene.get("audio_offset_sec", 0.0)) + float(scene.get("duration_sec", 0.0))
                    if scene
                    else short_plan.get("source_end_sec")
                ),
                "reason": short_plan.get("reason", "Selected for standalone value"),
                "original_narration": scene.get("narration", ""),
                "short_rewrite": short_script.get("narration", ""),
            }
        )
    funnel_cfg = (channel_config.get("shorts") or {}).get("funnel") or {}
    # The parent long video's title carries the topic ("...aceite de oliva...") even
    # when the Short plan has no pillar/topic fields, so feed it to the resolver.
    long_title = _long_title(long_job_dir)
    script_cta = str(short_script.get("cta") or "").strip()
    if script_cta and not is_generic_cta(funnel_cfg, script_cta):
        cta = script_cta
    else:
        cta = resolve_funnel_cta(
            funnel_cfg, short_plan, has_url=bool(long_video_url), extra_text=long_title
        )
    return {
        "short_id": short_plan.get("short_id"),
        "idea_id": short_plan.get("idea_id"),
        "idea_type": short_plan.get("idea_type") or short_plan.get("candidate_type"),
        "key_points": list(short_plan.get("key_points") or []),
        "source_long_job_id": long_job_dir.name,
        "source_video_title": _long_title(long_job_dir),
        "source_video_url": long_video_url,
        "source_files": {
            "script": "../../script.json",
            "scenes": "../../scenes.json",
            "seo": "../../seo.json",
            "whisper_timestamps": "../../whisper_timestamps.json",
        },
        "used_source_scenes": used,
        "funnel": {"cta": cta, "long_video_url": long_video_url},
    }
