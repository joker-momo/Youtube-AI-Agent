from __future__ import annotations

import re
import unicodedata
from typing import Any

MIN_HOOK_WORDS = 2
MAX_HOOK_WORDS = 7
MIN_SYNTHESIS_SCENES = 2
DISTINCT_PARTS_MIN_GAP_RATIO = 0.20

CURIOSITY_PHRASES = [
    "no lo sabias",
    "no lo sabes",
    "casi nadie",
    "nadie reconoce",
    "lo que pasa",
    "por que",
    "por qué",
    "la verdad",
    "el error",
    "no es",
    "te engaña",
    "te engana",
]

_SCORE_KEYS = (
    "hook_strength",
    "viewer_pain",
    "practical_value",
    "source_fidelity",
    "visual_potential",
    "safety",
    "uniqueness",
)


def valid_scene_id_set(source_doc: dict) -> set[str]:
    return {str(scene["scene_id"]) for scene in source_doc.get("scenes", [])}


def normalize_text_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = re.sub(r"[^\w\s]", " ", normalized.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _token_set(text: str) -> set[str]:
    return {token for token in normalize_text_key(text).split() if token}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def covers_distinct_parts(scene_ids: list[str], source_doc: dict) -> bool:
    scene_index_by_id = {
        str(scene.get("scene_id")): int(scene.get("index") or idx)
        for idx, scene in enumerate(source_doc.get("scenes") or [])
    }
    indexes = [scene_index_by_id[sid] for sid in scene_ids if sid in scene_index_by_id]
    if len(indexes) < 2:
        return False
    total = max(1, len(source_doc.get("scenes", [])) - 1)
    span_ratio = (max(indexes) - min(indexes)) / total
    return span_ratio >= DISTINCT_PARTS_MIN_GAP_RATIO


def has_strong_curiosity_phrase(text: str) -> bool:
    normalized = normalize_text_key(text)
    if re.search(r"\b\d+\b", normalized):
        return True
    return any(phrase in normalized for phrase in CURIOSITY_PHRASES)


def _clamp_score(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except Exception:
        return 0


def _recompute_overall(scores: dict[str, Any]) -> float:
    return round(
        0.22 * scores["hook_strength"]
        + 0.18 * scores["viewer_pain"]
        + 0.20 * scores["practical_value"]
        + 0.18 * scores["source_fidelity"]
        + 0.10 * scores["visual_potential"]
        + 0.07 * scores["safety"]
        + 0.05 * scores["uniqueness"],
        1,
    )


def validate_and_score_ideas(raw: dict, source_doc: dict, target_count: int = 10) -> dict:
    valid_ids = valid_scene_id_set(source_doc)
    total_scenes = len(source_doc.get("scenes") or [])
    diagnostics: list[dict[str, str]] = []
    ideas: list[dict[str, Any]] = []

    for raw_idea in raw.get("ideas") or []:
        original_idea_id = str(raw_idea.get("idea_id") or "")
        if raw_idea.get("idea_type") != "synthesis":
            diagnostics.append({"original_idea_id": original_idea_id, "reason": "not_synthesis"})
            continue

        deduped_scene_ids: list[str] = []
        for scene_id in raw_idea.get("source_scene_ids") or []:
            sid = str(scene_id)
            if sid in valid_ids and sid not in deduped_scene_ids:
                deduped_scene_ids.append(sid)
        if not deduped_scene_ids:
            diagnostics.append({"original_idea_id": original_idea_id, "reason": "no_valid_source_scenes"})
            continue
        if total_scenes >= 2 and len(deduped_scene_ids) < MIN_SYNTHESIS_SCENES:
            diagnostics.append({"original_idea_id": original_idea_id, "reason": "invalid_source_scene_ids"})
            continue

        key_points = []
        for item in raw_idea.get("key_points") or []:
            point_scene_ids: list[str] = []
            for scene_id in item.get("source_scene_ids") or []:
                sid = str(scene_id)
                if sid in valid_ids and sid not in point_scene_ids:
                    point_scene_ids.append(sid)
            key_points.append({"point": str(item.get("point") or "").strip(), "source_scene_ids": point_scene_ids})

        scores = {key: _clamp_score((raw_idea.get("scores") or {}).get(key)) for key in _SCORE_KEYS}
        scores["overall"] = _recompute_overall(scores)
        overall = scores["overall"]
        if str(raw_idea.get("risk_level") or "") == "medical_sensitive" and scores["safety"] < 80:
            overall -= 20
        if len(str(raw_idea.get("hook_text") or "").split()) > MAX_HOOK_WORDS:
            overall -= 15
        if not str(raw_idea.get("visual_angle") or "").strip():
            overall -= 10
        if not str(raw_idea.get("practical_payoff") or "").strip():
            overall -= 10
        if covers_distinct_parts(deduped_scene_ids, source_doc):
            overall += 10
        if str(raw_idea.get("format") or "") in {"checklist", "mistake_list", "warning_signs"} and len(deduped_scene_ids) >= 3:
            overall += 8
        if has_strong_curiosity_phrase(f"{raw_idea.get('title', '')} {raw_idea.get('hook_text', '')}"):
            overall += 5
        scores["overall"] = round(max(0.0, min(100.0, overall)), 1)

        ideas.append(
            {
                "idea_id": original_idea_id or "idea",
                "idea_type": "synthesis",
                "format": str(raw_idea.get("format") or "pain_to_tip"),
                "title": str(raw_idea.get("title") or "").strip(),
                "hook_text": str(raw_idea.get("hook_text") or "").strip(),
                "viewer_pain": str(raw_idea.get("viewer_pain") or "").strip(),
                "practical_payoff": str(raw_idea.get("practical_payoff") or "").strip(),
                "source_scene_ids": deduped_scene_ids,
                "key_points": key_points,
                "narration_seed": str(raw_idea.get("narration_seed") or "").strip(),
                "visual_angle": str(raw_idea.get("visual_angle") or "").strip(),
                "cta_angle": str(raw_idea.get("cta_angle") or "long_video_channel_cta"),
                "risk_level": str(raw_idea.get("risk_level") or "lifestyle"),
                "risk_flags": list(raw_idea.get("risk_flags") or []),
                "scores": scores,
                "status": "idea_ready",
            }
        )

    kept: list[dict[str, Any]] = []
    for idea in ideas:
        title_key = normalize_text_key(idea["title"])
        hook_key = normalize_text_key(idea["hook_text"])
        source_set = set(idea["source_scene_ids"])
        text_set = _token_set(f"{idea['title']} {idea['hook_text']}")
        duplicate_index = None
        for idx, existing in enumerate(kept):
            same_title_or_hook = (
                title_key == normalize_text_key(existing["title"])
                or hook_key == normalize_text_key(existing["hook_text"])
            )
            same_source_and_text = (
                _jaccard(source_set, set(existing["source_scene_ids"])) >= 0.80
                and _jaccard(text_set, _token_set(f"{existing['title']} {existing['hook_text']}")) >= 0.60
            )
            if same_title_or_hook or same_source_and_text:
                duplicate_index = idx
                break
        if duplicate_index is None:
            kept.append(idea)
            continue
        existing = kept[duplicate_index]
        if idea["scores"]["overall"] > existing["scores"]["overall"]:
            diagnostics.append({"original_idea_id": existing["idea_id"], "reason": "duplicate"})
            kept[duplicate_index] = idea
        else:
            diagnostics.append({"original_idea_id": idea["idea_id"], "reason": "duplicate"})

    kept.sort(
        key=lambda item: (
            -float(item["scores"]["overall"]),
            -int(item["scores"]["source_fidelity"]),
            -int(item["scores"]["safety"]),
            item["idea_id"],
        )
    )
    kept = kept[:target_count]
    for idx, idea in enumerate(kept, start=1):
        idea["idea_id"] = f"idea-{idx:02d}"

    return {
        "source_long_job_id": raw.get("source_long_job_id") or source_doc.get("source_long_job_id"),
        "source_title": raw.get("source_title") or source_doc.get("title", ""),
        "ideas": kept,
        "warnings": list(raw.get("warnings") or []),
        "diagnostics": {"rejected_ideas": diagnostics},
    }
