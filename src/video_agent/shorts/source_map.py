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


def funnel_topic_es(short_plan: dict) -> str:
    """Resolve the Short's topic to a short Spanish noun, or '' when unknown."""
    pillar = str(
        (short_plan or {}).get("pillar")
        or (short_plan or {}).get("detected_pillar")
        or ""
    ).strip().lower()
    return _PILLAR_TOPIC_ES.get(pillar, "")


def resolve_funnel_cta(funnel_cfg: dict, short_plan: dict, *, has_url: bool) -> str:
    """The spoken CTA that bridges a Short to its long video.

    Prefers a topic-aware template (``cta_topic_template_with/without_url`` with a
    ``{tema}`` placeholder) so the CTA names the theme; falls back to the plain
    ``default_cta_with/without_url`` when no topic template is set or the topic
    can't be resolved. Centralized here so the script prompt and this source-map
    validator use the SAME expected CTA and never drift.
    """
    funnel_cfg = funnel_cfg or {}
    tema = funnel_topic_es(short_plan or {})
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
    p = long_job_dir / "seo.json"
    if p.exists():
        try:
            return str(json.loads(p.read_text(encoding="utf-8")).get("title", ""))
        except Exception:
            return ""
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
    cta = short_script.get("cta") or resolve_funnel_cta(
        funnel_cfg, short_plan, has_url=bool(long_video_url)
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
