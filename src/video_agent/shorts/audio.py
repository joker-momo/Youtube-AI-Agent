"""Kokoro narration synthesis for a Short, scoped to the short folder.

Reuses the long-form ``prepare_assets`` pipeline (Kokoro TTS + vertical stock
assets) but runs it inside ``shorts/short-XX/`` so the Short is a self-contained
mini-job for the Remotion renderer. Uses the Shorts faster voice preset
(Kokoro ef_dora, speed 1.07) from ``channel_config.shorts.tts``.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import paths
from video_agent.storage.atomic import atomic_write_json


def assert_no_unconverted_graphic_scenes(short_scenes: dict) -> None:
    """Fail closed if planning-only graphic layouts reach a renderable artifact."""
    leaked = [
        f"{scene.get('id', '<unknown>')}:{scene.get('layout')}"
        for scene in short_scenes.get("scenes") or []
        if str(scene.get("layout") or "").startswith("graphic_")
    ]
    if leaked:
        raise RuntimeError(
            "Graphic scenes must be converted to ChatGPT image-backed layouts "
            f"before render: {', '.join(leaked)}"
        )


def _persist_prepared_short_scenes(
    short_dir: Path, short_scenes: dict, *, assert_converted: bool = True
) -> None:
    for scene in short_scenes.get("scenes") or []:
        scene.pop("_skip_ai_fallback", None)
    # The background pass defers graphic scene conversion to the post-QA unified
    # gen (step 5), so graphic_* layouts legitimately still exist here; the guard
    # runs after that pass instead (assert_converted=False during background).
    if assert_converted:
        assert_no_unconverted_graphic_scenes(short_scenes)
    atomic_write_json(
        short_dir / paths.SHORT_JSON_SUBDIR / paths.SHORT_SCENES_FILE,
        short_scenes,
    )


def _short_asset_context(short_dir: Path, channel_config: dict) -> dict[str, Any]:
    """Common style/visual/tts kwargs shared by the background + TTS passes."""
    from video_agent.contracts import repo_root
    from video_agent.utils.json_io import read_json

    style_dna_path = (channel_config.get("style_dna") or {}).get("path")
    style_dna: dict[str, Any] = {}
    if style_dna_path:
        p = repo_root() / style_dna_path
        if p.exists():
            style_dna = read_json(p)

    visuals = dict(channel_config.get("visuals") or {})
    if (channel_config.get("shorts") or {}).get("source", {}).get("prefer_vertical_assets", True):
        visuals["orientation"] = "portrait"

    tts_config = (channel_config.get("shorts") or {}).get("tts") or channel_config.get("tts")
    # The Remotion ShortVideo renders ONE narration track at frame 0 while each
    # scene is timed by its planned ``duration_sec``. Audio/video only stay in
    # sync when every scene's audio block equals its planned duration, so the
    # Shorts path must pad per-scene audio (dynamic_sync=False) rather than
    # shrink scene durations to the raw speech length. Force it here, copy-on-
    # write so the caller's config is untouched.
    tts_config = {**(tts_config or {}), "dynamic_sync": False}
    # Shorts music has one owner: audio_mixer.mix_short_audio. Keep this stage
    # narration-only so music is never selected or mixed twice.
    tts_config.pop("music", None)
    channel_id = (channel_config.get("channel") or {}).get("id", "unknown-channel")
    return {
        "style_dna": style_dna,
        "visual_config": visuals,
        "tts_config": tts_config,
        "channel_id": channel_id,
    }


def synthesize_short_backgrounds(
    short_dir: Path,
    short_scenes: dict,
    channel_config: dict,
    on_scene_resolved: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Resolve every scene's background (Pexels video/photo, ChatGPT image, or
    placeholder) WITHOUT synthesizing audio. Runs as its own pipeline stage so the
    UI can report, scene-by-scene, which source was used."""
    from video_agent.shorts.assets.prepare import prepare_assets

    ctx = _short_asset_context(short_dir, channel_config)
    # Lazy AI policy: scenes routed to native-video QA skip the ChatGPT tier now
    # because a validated span video can supersede per-scene backgrounds at
    # render. Generated graphic/image routes still acquire ChatGPT assets here.
    _mark_video_covered_scenes(short_dir, short_scenes)
    # Log AI image-gen prompts to the Short's prompt history (same JSONL the
    # ChatGPT/Gemini calls write to). short_dir is this Short's folder, so the
    # history file is json/llm_history.jsonl inside it.
    llm_history_path = short_dir / paths.SHORT_JSON_SUBDIR / paths.SHORT_LLM_HISTORY_FILE

    prepare_assets(
        job_dir=short_dir,
        scene_doc=short_scenes,
        render_backgrounds=True,
        render_tts=False,
        on_scene_resolved=on_scene_resolved,
        llm_history_path=llm_history_path,
        # Step 5: graphic scenes get a placeholder now; their ChatGPT image is
        # generated later in the single post-QA pass (_stage_fallback_image_gen).
        defer_graphic_ai=True,
        **ctx,
    )
    _persist_prepared_short_scenes(short_dir, short_scenes, assert_converted=False)


def _scenes_with_native_video_route(short_dir: Path) -> set[str]:
    """Scene ids covered by a span routed to native-video QA.

    ``visual_route`` is the source of truth. A stale provisional id on a generated
    graphic/image span must not suppress ChatGPT image generation.
    """
    from video_agent.utils.json_io import read_json

    jd = short_dir / paths.SHORT_JSON_SUBDIR
    try:
        spans = (read_json(jd / "visual_spans.json") or {}).get("spans") or []
        sel = (read_json(jd / "visual_span_asset_selection.json") or {}).get("spans") or []
    except Exception:
        return set()
    has_video = set()
    for selection in sel:
        route = str(selection.get("visual_route") or "").strip()
        if route:
            if route == "native_video_candidate" and selection.get("provisional_candidate_id"):
                has_video.add(selection.get("visual_span_id"))
            continue
        # Back-compat for old artifacts written before visual_route existed.
        if selection.get("provisional_candidate_id"):
            has_video.add(selection.get("visual_span_id"))
    covered: set[str] = set()
    for sp in spans:
        if (sp.get("id") or sp.get("visual_span_id")) in has_video:
            covered.update(str(x) for x in (sp.get("scene_ids") or []))
    return covered


def _mark_video_covered_scenes(short_dir: Path, short_scenes: dict) -> None:
    covered = _scenes_with_native_video_route(short_dir)
    for scene in short_scenes.get("scenes") or []:
        is_graphic = str(scene.get("layout") or "").startswith("graphic_")
        scene["_skip_ai_fallback"] = not is_graphic and scene.get("id") in covered


def regen_fallback_backgrounds(
    short_dir: Path, short_scenes: dict, channel_config: dict, scene_ids: set[str]
) -> None:
    """Lazy second pass: generate ChatGPT images ONLY for the given scenes (spans
    whose native-video route failed visual_local_qa), merging into the existing
    manifest/report. No-op when scene_ids is empty."""
    if not scene_ids:
        return
    from video_agent.shorts.assets.prepare import prepare_assets

    ctx = _short_asset_context(short_dir, channel_config)
    for scene in short_scenes.get("scenes") or []:
        if scene.get("id") in scene_ids:
            scene["_skip_ai_fallback"] = False
            # These scenes are in the regen set because their native stock was
            # rejected by local QA (or the layout needs a controlled image). Merely
            # enabling the AI tier is not enough: scene_resolver's strict stock tier
            # (Tier 1) still runs first for a stock_ok scene and re-selects the SAME
            # rejected clip — it has no view of the semantic rejection — so the AI
            # tier never fires and the rejected stock is kept (bug-477: s04/s06 kept
            # their age-band-rejected Pexels videos). Force the AI-image route so the
            # stock tiers are skipped; graphic_* scenes already force it via layout.
            if not str(scene.get("layout") or "").startswith("graphic_"):
                if str(scene.get("asset_strategy") or "stock_ok") not in (
                    "graphic_fallback",
                    "ai_image_preferred",
                ):
                    scene["asset_strategy"] = "ai_image_preferred"
    llm_history_path = short_dir / paths.SHORT_JSON_SUBDIR / paths.SHORT_LLM_HISTORY_FILE
    prepare_assets(
        job_dir=short_dir,
        scene_doc=short_scenes,
        render_backgrounds=True,
        render_tts=False,
        llm_history_path=llm_history_path,
        only_scene_ids=set(scene_ids),
        **ctx,
    )
    _persist_prepared_short_scenes(short_dir, short_scenes)


def synthesize_short_narration(short_dir: Path, short_scenes: dict, channel_config: dict) -> Path:
    """Synthesize the Short's narration track. Backgrounds are resolved earlier by
    ``synthesize_short_backgrounds`` (separate stage), so this pass skips visuals."""
    from video_agent.shorts.assets.prepare import prepare_assets

    ctx = _short_asset_context(short_dir, channel_config)
    prepare_assets(
        job_dir=short_dir,
        scene_doc=short_scenes,
        render_backgrounds=False,
        render_tts=True,
        **ctx,
    )

    src = short_dir / "assets" / "narration.wav"
    audio_dir = short_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    dst = audio_dir / "short_narration.wav"
    if src.exists():
        shutil.copyfile(src, dst)
    return dst
