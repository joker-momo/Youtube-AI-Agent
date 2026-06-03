"""Spec v6 §2.2 — ChatGPT-driven Shorts planner.

Deterministic Python extracts and scores candidates. ChatGPT then picks 1-3
Shorts from those candidates (constraint: must reuse provided candidate_id).
A no-LLM fallback path exists so old jobs / tests can still run without a
browser.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import candidate_scorer, extractor, music_selector, prompts
from video_agent.shorts.llm import LLMCallLog, log_llm_call

PROVIDER = "chatgpt"


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
        score = sum(3 for k in kws if k in title) + sum(1 for k in kws if k in scenes_text)
        if score > best_score:
            best, best_score = pillar, score
    return best


def _parse(raw: str) -> dict:
    from video_agent.operator import extract_json_objects
    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


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


def _deterministic_pick(scored: list[dict], target: int, allow_fewer: bool) -> tuple[list[dict], list[str]]:
    """Fallback path: pick best candidates without LLM."""
    warnings: list[str] = []
    strong = [c for c in scored if c["tier"] == "strong"]
    acceptable = [c for c in scored if c["tier"] in ("strong", "acceptable")]
    if len(strong) >= target:
        pool = strong
    elif allow_fewer and acceptable:
        pool = acceptable
        if len(acceptable) < target:
            warnings.append("fewer_shorts_due_to_weak_candidates")
    else:
        pool = scored[:1]
        warnings.append("only_fallback_candidate_available")
    return _dedupe_non_overlapping(pool, target), warnings


def _build_short_from_pick(idx: int, cand: dict, formats: list[str], voice_preset: dict,
                            music_track: str) -> dict:
    from datetime import datetime
    from video_agent.shorts.paths import slugify

    cand_id = cand.get("candidate_id") or "cand"
    raw_title = cand.get("narration", "")
    title_slug = slugify(raw_title)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_id = f"short-{idx:02d}_{cand_id}_{ts}_{title_slug}"

    return {
        "short_id": short_id,
        "format": formats[(idx - 1) % len(formats)] if formats else "pain_to_tip",
        "candidate_id": cand.get("candidate_id"),
        "scene_ids": cand.get("scene_ids"),
        "source_scene_ids": cand.get("scene_ids"),
        "source_start_sec": cand.get("source_start_sec"),
        "source_end_sec": cand.get("source_end_sec"),
        "score": cand.get("final_score"),
        "reason": f"{cand.get('tier')} candidate (score {cand.get('final_score')})",
        "music_track": music_track,
        "cover_strategy": "first_scene_cover",
        "voice_preset": voice_preset,
        "narration_seed": cand.get("narration", ""),
    }


def plan_shorts_from_long_video(
    long_job_dir: Path,
    channel_config: dict,
    requested_count: int | None = None,
    *,
    llm_fn: Callable[[str], str] | None = None,
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
    cand_index = {c["candidate_id"]: c for c in scored}

    target = requested_count if requested_count else default_count
    target = max(1, min(target, max_count))

    warnings: list[str] = []
    selected: list[dict] = []

    # --- Path A: ChatGPT planner (spec v6 §2.2) ----------------------------
    if llm_fn is not None and scored:
        prompt = prompts.planner_prompt(
            channel_config=channel_config,
            candidates=scored[:20],
            long_summary={
                "job_id": long_job_dir.name,
                "title": _seo_title(long_job_dir),
                "pillar": pillar,
            },
            formats=all_formats,
        )
        log_llm_call(LLMCallLog(
            task="planner", provider=PROVIDER, short_id="-",
            attempt=1, input_artifacts=["scenes.json", "script.json", "seo.json"],
            output_artifact="shorts_plan.json",
        ))
        raw = llm_fn(prompt)
        parsed = _parse(raw) or {}
        picks = parsed.get("selected_shorts") or []
        for idx, pick in enumerate(picks, start=1):
            cid = pick.get("candidate_id")
            if cid not in cand_index:
                warnings.append(f"invalid_candidate_id:{cid}")
                continue
            cand = cand_index[cid]
            entry = _build_short_from_pick(idx, cand, all_formats, voice_preset, music_track)
            # Merge LLM-supplied hook_angle / viewer_pain / reason / format.
            for k in ("hook_angle", "viewer_pain", "practical_payoff",
                      "music_track", "format", "reason"):
                if pick.get(k):
                    entry[k] = pick[k]
            selected.append(entry)
            if len(selected) >= max_count:
                break
        if not selected:
            warnings.append("planner_returned_no_valid_shorts")

    # --- Path B: deterministic fallback ------------------------------------
    if not selected:
        picked, warn = _deterministic_pick(scored, target, allow_fewer)
        warnings.extend(warn)
        for idx, cand in enumerate(picked, start=1):
            selected.append(_build_short_from_pick(idx, cand, all_formats, voice_preset, music_track))

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
