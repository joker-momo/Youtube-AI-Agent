"""Spec v6 §2.4 — ChatGPT generates Short scenes AND picks layouts."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import paths, prompts
from video_agent.shorts.llm import LLMCallLog, log_llm_call
from video_agent.storage.atomic import atomic_write_json

PROVIDER = "chatgpt"


def _parse(raw: str) -> dict:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", str(text or "").strip()) if s.strip()]


SHORT_LAYOUTS = (
    "short_hook", "short_pain", "short_tip", "short_checklist",
    "short_myth", "short_quote", "short_cta",
)

# Backward-compat adapter (spec v6 §10). Legacy long-form layout names map to
# their nearest short_* equivalent ONLY for legacy artifacts. New
# short_scene_builder output must already use short_* directly.
_LEGACY_TO_SHORT = {
    "hook": "short_hook",
    "subtitle": "short_tip",
    "warning": "short_pain",
    "checklist": "short_checklist",
    "quote": "short_quote",
    "cta": "short_cta",
}


def _map_layout(layout: str) -> str:
    """Return a schema-valid short_* layout.

    short_* is the first-class layout family. Long-form legacy values are
    mapped through the legacy adapter so old artifacts keep rendering.
    Unknown → ``short_tip`` (safest neutral)."""
    raw = str(layout or "").strip().lower()
    if raw in SHORT_LAYOUTS:
        return raw
    if raw in _LEGACY_TO_SHORT:
        return _LEGACY_TO_SHORT[raw]
    return "short_tip"


def _chunk(seq: list, n: int) -> list[list]:
    if n <= 0:
        return []
    k = len(seq)
    return [seq[i * k // n : (i + 1) * k // n] for i in range(n)]


def normalize_short_scenes(scenes_doc: dict, short_script: dict) -> dict[str, Any]:
    """Make Short scenes compatible with the long-form render/TTS pipeline.

    - rename ``scene_id`` → ``id`` (renderer/prepare_assets read ``id``)
    - guarantee each scene has non-empty ``narration`` (Kokoro TTS needs it):
      reuse existing per-scene narration, else distribute the script narration
      across scenes by sentence, falling back to on_screen_text.
    """
    out = dict(scenes_doc or {})
    scenes = list(out.get("scenes") or [])
    n = len(scenes)
    sentences = _split_sentences((short_script or {}).get("narration"))
    groups = _chunk(sentences, n) if sentences else [[] for _ in range(n)]

    norm_scenes = []
    for i, raw in enumerate(scenes):
        sc = dict(raw)
        if not sc.get("id"):
            sc["id"] = sc.get("scene_id") or f"s{i + 1}"
        if not str(sc.get("narration") or "").strip():
            chunk = groups[i] if i < len(groups) else []
            narr = " ".join(chunk).strip()
            if not narr:
                narr = str(sc.get("on_screen_text") or "").strip() or str(
                    (short_script or {}).get("hook") or ""
                ).strip()
            sc["narration"] = narr
        # Seed the full long-form scene shape the render/TTS pipeline expects.
        sc.setdefault("on_screen_text", "")
        sc.setdefault("caption", sc.get("on_screen_text", ""))
        sc.setdefault("visual_prompt", sc.get("caption", ""))
        sc["layout"] = _map_layout(sc.get("layout"))
        sc.setdefault("layout_payload", {})
        sc.setdefault("layout_reason", "short")
        sc.setdefault("motion", "none")
        sc.setdefault("asset_refs", {})
        sc.setdefault("word_segments", [])
        sc.setdefault("planner_warnings", [])
        sc.setdefault("audio_offset_sec", 0.0)
        sc.setdefault("duration_sec", 3.0)
        norm_scenes.append(sc)

    # Guarantee a closing short_cta scene whenever the script declared a CTA
    # but the LLM forgot to add the layout. Without a CTA scene viewers swipe
    # away on the last beat with no save/comment prompt → retention drop.
    script_cta = str((short_script or {}).get("cta") or "").strip()
    has_cta_scene = any(
        str(s.get("layout") or "").strip().lower() == "short_cta"
        for s in norm_scenes
    )
    if script_cta and not has_cta_scene:
        # Merge scenes from the end if adding the CTA would exceed the maximum of 7 scenes.
        while len(norm_scenes) >= 7 and len(norm_scenes) >= 2:
            s1 = norm_scenes[-2]
            s2 = norm_scenes[-1]
            merged_scene = {
                "id": s1["id"],
                "duration_sec": s1.get("duration_sec", 3.0) + s2.get("duration_sec", 3.0),
                "layout": s1.get("layout", "short_tip"),
                "narration": (str(s1.get("narration") or "").strip() + " " + str(s2.get("narration") or "").strip()).strip(),
                "caption": s1.get("caption") or s2.get("caption") or "",
                "on_screen_text": s1.get("on_screen_text") or s2.get("on_screen_text") or "",
                "visual_prompt": s1.get("visual_prompt") or s2.get("visual_prompt") or "",
                "motion": s1.get("motion") or s2.get("motion") or "none",
                "layout_payload": {**s1.get("layout_payload", {}), **s2.get("layout_payload", {})},
                "layout_reason": "merged_to_fit_cta",
                "asset_refs": {**s1.get("asset_refs", {}), **s2.get("asset_refs", {})},
                "word_segments": [],
                "planner_warnings": [],
                "audio_offset_sec": 0.0,
            }
            norm_scenes = norm_scenes[:-2] + [merged_scene]

        cta_idx = len(norm_scenes) + 1
        use_zero = any(re.match(r"^s\d{2}$", str(s.get("id") or "")) for s in norm_scenes)
        cta_id = f"s{cta_idx:02d}" if use_zero else f"s{cta_idx}"
        cta_caption = script_cta
        cta_oneliner = " ".join(script_cta.split()[:6]).upper() or "GUÁRDALO"
        norm_scenes.append({
            "id": cta_id,
            "narration": script_cta,
            "on_screen_text": cta_oneliner,
            "caption": cta_caption,
            "visual_prompt": "Close-up of a calm person looking warmly at camera, soft natural light",
            "layout": "short_cta",
            "layout_payload": {"title": cta_oneliner, "subtitle": cta_caption},
            "layout_reason": "auto_cta_appended",
            "motion": "slow_zoom",
            "asset_refs": {},
            "word_segments": [],
            "planner_warnings": [],
            "audio_offset_sec": 0.0,
            "duration_sec": 3.5,
        })

    out["scenes"] = norm_scenes
    total_dur = sum(float(s.get("duration_sec") or 0) for s in norm_scenes)
    out["total_duration_sec"] = int(total_dur) if total_dur.is_integer() else round(total_dur, 1)
    return out


def build_short_scenes(
    long_job_dir: Path,
    short_plan: dict,
    short_script: dict,
    channel_config: dict,
    llm_fn: Callable[..., str],
    *,
    feedback: str = "",
    attempt: int = 1,
) -> dict[str, Any]:
    """Spec v6 §2.4: ChatGPT writes scenes AND picks each scene's layout.

    The deterministic ``normalize_short_scenes`` only fills missing fields and
    maps any legacy long-form layout names (backward compat); it must NOT
    overwrite layouts the LLM emitted."""
    prompt = prompts.short_scene_prompt_v6(channel_config, short_plan, short_script, feedback=feedback)
    log_llm_call(LLMCallLog(
        task="short_scene_builder", provider=PROVIDER,
        short_id=short_plan.get("short_id", "-"), attempt=attempt,
        input_artifacts=["short_script.json"],
        output_artifact="short_scenes.json",
    ))
    raw = _invoke(llm_fn, "scenes", prompt)
    scenes = normalize_short_scenes(_parse(raw), short_script)
    atomic_write_json(
        paths.short_dir(long_job_dir, short_plan["short_id"]) / paths.SHORT_SCENES_FILE,
        scenes,
    )
    return scenes


def _invoke(llm_fn: Callable[..., str], kind: str, prompt: str) -> str:
    try:
        return llm_fn(prompt)
    except TypeError:
        return llm_fn(kind, prompt)
