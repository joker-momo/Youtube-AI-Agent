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

    out["scenes"] = norm_scenes
    if not out.get("total_duration_sec"):
        out["total_duration_sec"] = round(sum(float(s.get("duration_sec") or 0) for s in norm_scenes), 1)
    return out


def build_short_scenes(
    long_job_dir: Path,
    short_plan: dict,
    short_script: dict,
    channel_config: dict,
    llm_fn: Callable[..., str],
    *,
    attempt: int = 1,
) -> dict[str, Any]:
    """Spec v6 §2.4: ChatGPT writes scenes AND picks each scene's layout.

    The deterministic ``normalize_short_scenes`` only fills missing fields and
    maps any legacy long-form layout names (backward compat); it must NOT
    overwrite layouts the LLM emitted."""
    prompt = prompts.short_scene_prompt_v6(channel_config, short_plan, short_script)
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
