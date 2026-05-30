"""Plan the best 1–3 Shorts from a completed long-form job (no LLM)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_agent.shorts import candidate_scorer, extractor, music_selector

PILLAR_KEYWORDS = {
    "movement": ["mover", "muévete", "muevete", "caminar", "camina", "ejercicio", "rodilla", "movimiento", "andar"],
    "sleep": ["dormir", "sueño", "sueno", "descansar", "noche", "insomnio", "madrugada"],
    "stress": ["estrés", "estres", "ansiedad", "calma", "mente", "preocupaci"],
    "food": ["comer", "comida", "cena", "nutrición", "nutricion", "alimentación", "alimentacion", "plato"],
    "routine": ["rutina", "hábito", "habito", "organiza"],
    "energy": ["energía", "energia", "cansancio", "bajón", "bajon", "fatiga"],
    "menopause": ["menopausia", "sofocos", "perimenopausia", "hormonal"],
}

DEFAULT_PILLAR = "routine"


def _seo_title(long_job_dir: Path) -> str:
    p = long_job_dir / "seo.json"
    if p.exists():
        try:
            return str(json.loads(p.read_text(encoding="utf-8")).get("title", ""))
        except Exception:
            return ""
    return ""


def detect_pillar(long_job_dir: Path) -> str:
    title = _seo_title(long_job_dir).lower()
    scenes_text = ""
    sp = long_job_dir / "scenes.json"
    if sp.exists():
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
            scenes_text = " ".join(str(s.get("narration") or "") for s in (data.get("scenes") or [])).lower()
        except Exception:
            pass
    text = title + " " + scenes_text
    best, best_score = DEFAULT_PILLAR, 0
    for pillar, kws in PILLAR_KEYWORDS.items():
        # title hits weigh more than body hits
        score = sum(3 for k in kws if k in title) + sum(1 for k in kws if k in scenes_text)
        if score > best_score:
            best, best_score = pillar, score
    return best


def _dedupe_non_overlapping(scored: list[dict], limit: int) -> list[dict]:
    picked: list[dict] = []
    used: set[str] = set()
    for cand in scored:
        ids = set(cand.get("scene_ids") or [])
        if ids & used:
            continue
        picked.append(cand)
        used |= ids
        if len(picked) >= limit:
            break
    return picked


def plan_shorts_from_long_video(
    long_job_dir: Path,
    channel_config: dict,
    requested_count: int | None = None,
) -> dict[str, Any]:
    shorts_cfg = channel_config.get("shorts") or {}
    ag = shorts_cfg.get("auto_generate") or {}
    default_count = int(ag.get("default_count", 3))
    max_count = int(ag.get("max_count", 5))
    allow_fewer = bool(ag.get("allow_fewer_if_candidates_are_weak", True))
    formats = (shorts_cfg.get("content") or {}).get("default_formats_per_long") or ["pain_to_tip"]
    fallback_formats = (shorts_cfg.get("content") or {}).get("fallback_formats") or []
    all_formats = list(formats) + list(fallback_formats)
    voice_preset = dict(shorts_cfg.get("tts") or {})

    pillar = detect_pillar(long_job_dir)
    music_track = music_selector.select_music_track(pillar, channel_config)

    candidates = extractor.extract_candidates(long_job_dir)
    scored = [candidate_scorer.score_candidate(c, channel_config) for c in candidates]
    scored.sort(key=lambda c: c["final_score"], reverse=True)

    target = requested_count if requested_count else default_count
    target = max(1, min(target, max_count))

    strong = [c for c in scored if c["tier"] == "strong"]
    acceptable = [c for c in scored if c["tier"] in ("strong", "acceptable")]

    warnings: list[str] = []
    if len(strong) >= target:
        pool = strong
    elif allow_fewer and acceptable:
        pool = acceptable
        if len(acceptable) < target:
            warnings.append("fewer_shorts_due_to_weak_candidates")
    else:
        # nothing acceptable — take best single as a fallback rather than nothing
        pool = scored[:1]
        warnings.append("only_fallback_candidate_available")

    picked = _dedupe_non_overlapping(pool, target)
    if not picked and scored:
        picked = scored[:1]

    selected = []
    for idx, cand in enumerate(picked, start=1):
        fmt = all_formats[(idx - 1) % len(all_formats)] if all_formats else "pain_to_tip"
        selected.append(
            {
                "short_id": f"short-{idx:02d}",
                "format": fmt,
                "candidate_id": cand.get("candidate_id"),
                "scene_ids": cand.get("scene_ids"),
                "source_start_sec": cand.get("source_start_sec"),
                "source_end_sec": cand.get("source_end_sec"),
                "score": cand.get("final_score"),
                "reason": f"{cand.get('tier')} candidate (score {cand.get('final_score')})",
                "music_track": music_track,
                "cover_strategy": "first_scene_cover",
                "voice_preset": voice_preset,
                "narration_seed": cand.get("narration", ""),
            }
        )

    return {
        "source_long_job_id": long_job_dir.name,
        "source_title": _seo_title(long_job_dir),
        "detected_pillar": pillar,
        "target_count": len(selected),
        "voice_preset": voice_preset,
        "music_enabled": bool((shorts_cfg.get("music") or {}).get("enabled", True)),
        "cover_enabled": bool((shorts_cfg.get("cover") or {}).get("enabled", True)),
        "selected_shorts": selected,
        "warnings": warnings,
    }
