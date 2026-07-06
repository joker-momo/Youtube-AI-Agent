"""Shorts ``prepare_assets`` (T5).

Forked from ``video_agent.stages.assets.prepare_assets`` so the Shorts pipeline
owns its asset-preparation entry point independently of the long pipeline. The
signature and behavior match the long copy, but scenes are resolved via the
Shorts :class:`~video_agent.shorts.assets.scene_resolver.ShortSceneResolver`
(which reaches the ChatGPT/AI image tier the long path never uses), and every
helper is pulled from the SHARED ``video_agent.assets.*`` homes or Shorts code —
never from ``video_agent.stages.*`` (the asset-layer boundary).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from video_agent.assets.audio_ops import (  # shared primitives (P1)
    _synthesize_narration_and_mix,
    _write_audio_progress,
)
from video_agent.assets.materialize import materialize_media
from video_agent.assets.media_ops import (  # shared primitives (P1)
    _write_placeholder_video,
    _write_preview_still,
    _write_video_from_image,
)
from video_agent.assets.scene_prep import (  # shared helpers (P1)
    _background_source_label,
    _find_asset_refs_primary,
    _find_local_scene_image,
    _resolve_source_dir,
    _write_background_report,
)
from video_agent.assets.visual_diversity.integration import (
    finalize_visual_diversity_report,
    prepare_visual_diversity,
    record_scene_selection,
)
from video_agent.contracts import ARTIFACT_ASSETS, ARTIFACT_SCENES, repo_root
from video_agent.shorts.assets.scene_resolver import ShortSceneResolver
from video_agent.storage.public_jobs import prepare_public_job_dir
from video_agent.utils.json_io import write_json


class RequiredGeneratedImageError(RuntimeError):
    """Raised when a planning-only graphic scene has no ChatGPT image."""


def _project_name_from_out_path(out_path: str | Path) -> str:
    """Readable ChatGPT project name like ``<job_id>_<scene_id>`` derived from the
    image's out_path (``jobs/<job_id>/assets/ai_temp_<scene>_<hash>.png``) so the
    project is identifiable in the ChatGPT sidebar instead of a generic 'fallback'."""
    try:
        p = Path(out_path)
        job_id = p.parent.parent.name if p.parent.parent.name not in ("", "assets") else p.parent.name
        stem = p.stem
        scene = stem
        if stem.startswith("ai_temp_"):
            scene = stem[len("ai_temp_"):].rsplit("_", 1)[0]  # drop the trailing hash
        name = f"{job_id}_{scene}".strip("_")
        return name or "fallback"
    except Exception:
        return "fallback"


def _default_sync_image_gen(prompt: str, out_path: str | Path) -> None:
    import asyncio
    import os

    from video_agent.orchestrator.browser_client import BrowserClient
    from video_agent.persona import PERSONA_SCENE_INSTRUCTION, resolve_persona_reference

    # Default to localhost since the script is mostly run natively on host machine now
    browser_worker_url = os.environ.get("BROWSER_WORKER_URL", "http://localhost:8001")
    client = BrowserClient(browser_worker_url)
    # Presenter identity: attach the configured reference photo (CHANNEL_CONFIG
    # env) and add the conditional instruction so any person in the image IS the
    # channel presenter; person-less scenes ignore the reference.
    persona_ref = resolve_persona_reference()
    if persona_ref:
        prompt = prompt + PERSONA_SCENE_INSTRUCTION
    asyncio.run(
        client.generate_image(
            prompt=prompt,
            project_name=_project_name_from_out_path(out_path),
            out_path=str(out_path),
            aspect_ratio="9:16",
            attachment_path=persona_ref or None,
        )
    )


def prepare_assets(
    job_dir: Path,
    style_dna: dict[str, Any],
    scene_doc: dict[str, Any],
    *,
    visual_config: dict[str, Any] | None = None,
    tts_config: dict[str, Any] | None = None,
    channel_id: str = "unknown-channel",
    image_gen_fn: Any | None = None,
    stock_client: Any | None = None,
    download_client: Any | None = None,
    tts_client: Any | None = None,
    llm_history_path: Path | None = None,
    render_backgrounds: bool = True,
    render_tts: bool = True,
    on_scene_resolved: Callable[[dict[str, Any]], None] | None = None,
    vision_qa_fn: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
    only_scene_ids: set[str] | None = None,
    defer_graphic_ai: bool = False,
) -> dict[str, Any]:
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = job_dir.parent
    for parent in job_dir.parents:
        if (parent / "remotion").is_dir():
            workspace_root = parent
            break
    public_assets_dir = prepare_public_job_dir(workspace_root, job_dir.name) / "assets"
    public_assets_dir.mkdir(parents=True, exist_ok=True)
    palette = style_dna["palette"]
    visual_config = visual_config or {}

    # Determine portrait default dynamically
    is_portrait = (visual_config.get("orientation") == "portrait")
    if not is_portrait:
        if "shorts" in job_dir.parts or visual_config.get("format") == "short":
            is_portrait = True

    source_dir = _resolve_source_dir(visual_config.get("source_dir"))
    # When a history path is supplied (Shorts), log every AI image-gen prompt to
    # the same prompt-history stream as the ChatGPT/Gemini calls.
    image_gen_recorder = None
    if llm_history_path is not None:
        try:
            from video_agent.shorts.llm_history import LLMHistoryRecorder
            image_gen_recorder = LLMHistoryRecorder(Path(llm_history_path))
        except Exception:  # pragma: no cover - logging must never break the stage
            image_gen_recorder = None
    scene_resolver = (
        ShortSceneResolver(
            visual_config,
            stock_client=stock_client,
            download_client=download_client,
            image_gen_fn=image_gen_fn or _default_sync_image_gen,
            image_gen_recorder=image_gen_recorder,
            vision_qa_fn=vision_qa_fn,
        )
        if visual_config.get("strategy") in {"auto", "stock_photo_api"}
        else None
    )

    diversity_run = prepare_visual_diversity(
        scene_doc=scene_doc,
        visual_config=visual_config,
        channel_id=channel_id,
        job_id=job_dir.name,
        repo_root=repo_root(),
        outputs_root=repo_root() / "outputs",
    )

    scene_assets: list[dict[str, Any]] = []
    num_scenes = len(scene_doc["scenes"])
    for index, scene in enumerate(scene_doc["scenes"] if render_backgrounds else []):
        # Targeted re-gen pass (lazy AI fallback): resolve only the requested
        # scenes and merge into the existing manifest/report further below.
        if only_scene_ids is not None and scene["id"] not in only_scene_ids:
            continue
        _write_audio_progress(job_dir, round((index / num_scenes) * 50.0, 1), f"visuals (scene {index+1}/{num_scenes})")
        # Emit BEFORE acquiring so the UI shows the scene currently being fetched
        # (acquisition — esp. ChatGPT image gen — can take many seconds).
        if on_scene_resolved is not None:
            try:
                on_scene_resolved({
                    "index": index,
                    "total": num_scenes,
                    "scene_id": scene["id"],
                    "phase": "start",
                    "background_source": None,
                })
            except Exception:  # pragma: no cover - reporting must never break asset prep
                pass
        primary_asset = _find_asset_refs_primary(scene, job_dir)
        local_image = primary_asset or _find_local_scene_image(scene["id"], source_dir)
        stock_asset = None
        scene_dur = float(scene.get("duration_sec") or 30)
        _layout = str(scene.get("layout") or "")
        was_graphic_layout = _layout.startswith("graphic_")
        if was_graphic_layout:
            if defer_graphic_ai:
                # Step 5: defer this graphic scene's ChatGPT generation to the
                # single post-QA pass. Skip the AI tier now (placeholder) so the
                # whole batch of needed images can be generated together later.
                scene["_skip_ai_fallback"] = True
            else:
                # graphic_* is planning vocabulary only. A provisional stock video
                # must never suppress the required ChatGPT image acquisition.
                scene["_skip_ai_fallback"] = False
        if not local_image and scene_resolver:
            stock_asset = scene_resolver.get_scene_asset(scene, channel_id, job_dir.name)
        if (
            was_graphic_layout
            and not defer_graphic_ai
            and (not stock_asset or stock_asset.get("provider") != "ai_generated")
        ):
            provider = stock_asset.get("provider") if stock_asset else "none"
            raise RequiredGeneratedImageError(
                f"Scene {scene['id']} layout {_layout} requires a ChatGPT-generated "
                f"image; acquired provider={provider}."
            )
        # Encode all scene backgrounds to video so Remotion renders one media path.
        asset_suffix = ".mp4"
        image_path = assets_dir / f"{scene['id']}{asset_suffix}"
        # media_kind records whether the SOURCE was real video footage or a still
        # image (photo / AI image / placeholder), so the UI can preview it as a
        # <video> or <img> even though every asset is encoded to .mp4 for render.
        media_kind = "video"
        preview_still = assets_dir / f"{scene['id']}_preview.jpg"
        if local_image:
            if local_image.suffix.lower() == ".mp4":
                if local_image.resolve() != image_path.resolve():
                    materialize_media(local_image, image_path)
            else:
                _write_video_from_image(local_image, image_path, scene_dur, is_portrait=is_portrait)
                if _write_preview_still(local_image, preview_still):
                    media_kind = "image"
            source = (
                "asset_refs_primary" if primary_asset is not None else "local_directory"
            )
            source_path = str(local_image.resolve())
            extra_manifest = {}
        elif stock_asset and stock_asset.get("provider") != "graphic_fallback":
            # ai_generated or standard stock API asset
            if "file_path" in stock_asset:
                library_path = scene_resolver.core.library.root / stock_asset["file_path"]
            elif "local_path" in stock_asset:
                library_path = Path(stock_asset["local_path"])
            else:
                library_path = Path(stock_asset.get("url", "")) # Should not happen

            if library_path.suffix.lower() == ".mp4":
                materialize_media(library_path, image_path)
            else:
                _write_video_from_image(library_path, image_path, scene_dur, is_portrait=is_portrait)
                if _write_preview_still(library_path, preview_still):
                    media_kind = "image"
            source = "asset_library"
            source_path = str(library_path.resolve())
            extra_manifest = {
                "asset_id": stock_asset.get("asset_id"),
                "provider": stock_asset.get("provider"),
                "provider_asset_id": stock_asset.get("provider_asset_id"),
                "source_url": stock_asset.get("original_url"),
                "attribution": stock_asset.get("attribution"),
                "asset_tier": stock_asset.get("asset_tier"),
                "asset_selection": stock_asset.get("asset_selection"),
            }
            record_scene_selection(diversity_run, scene=scene, selected_asset=stock_asset)
        else:
            _write_placeholder_video(
                image_path,
                scene,
                index,
                palette,
                scene_dur,
                is_portrait=is_portrait,
            )
            source = "generated_placeholder"
            source_path = None
            extra_manifest = {}
            if stock_asset and stock_asset.get("provider") == "graphic_fallback":
                extra_manifest = {
                    "asset_id": stock_asset.get("asset_id"),
                    "provider": stock_asset.get("provider"),
                    "provider_asset_id": stock_asset.get("provider_asset_id"),
                    "source_url": None,
                    "attribution": stock_asset.get("attribution"),
                    "asset_tier": stock_asset.get("asset_tier"),
                    "asset_selection": stock_asset.get("asset_selection"),
                }
            elif scene_resolver:
                extra_manifest = {"stock_errors": scene_resolver.core.last_errors}
            record_scene_selection(diversity_run, scene=scene, selected_asset=None, is_placeholder=True)
        public_image_path = public_assets_dir / image_path.name
        materialize_media(image_path, public_image_path)
        public_ref = f"jobs/{job_dir.name}/assets/{image_path.name}"
        scene["asset_refs"]["background"] = public_ref

        if was_graphic_layout and stock_asset and stock_asset.get("provider") == "ai_generated":
            scene["generated_image_source_layout"] = _layout
            scene["layout"] = "short_tip"
            scene["background_mode"] = "generated_image"
            scene.pop("_skip_ai_fallback", None)
        scene_asset = {
            "scene_id": scene["id"],
            "background": str(image_path.resolve()),
            "public_background": public_ref,
            "source": source,
            "source_path": source_path,
        }
        if scene.get("generated_image_source_layout"):
            scene_asset["generated_image_source_layout"] = scene.get("generated_image_source_layout")
        scene_asset.update(extra_manifest)
        scene_asset["background_source"] = _background_source_label(scene_asset)
        scene_asset["media_kind"] = media_kind
        scene_assets.append(scene_asset)
        if on_scene_resolved is not None:
            try:
                on_scene_resolved({
                    "index": index,
                    "total": num_scenes,
                    "scene_id": scene["id"],
                    "phase": "resolved",
                    "background_source": scene_asset["background_source"],
                })
            except Exception:  # pragma: no cover - reporting must never break asset prep
                pass

    if render_backgrounds:
        finalize_visual_diversity_report(
            diversity_run,
            job_id=job_dir.name,
            channel_id=channel_id,
            outputs_dir=job_dir,
        )
        # Attach deterministic ROI crop plans now that asset_refs.background is
        # resolved, so render/QA see crop_plan in the persisted scene artifact
        # (not only in memory). Skips graphic_* and background-less scenes.
        try:
            from video_agent.shorts.roi_crop_planner import apply_crop_plan
            scene_doc["scenes"] = apply_crop_plan(scene_doc)["scenes"]
        except Exception:  # pragma: no cover - crop planning must never break asset prep
            pass
        # Persist asset_refs + the per-scene background sourcing report so the
        # Shorts Studio UI can show which source each scene used.
        write_json(job_dir / ARTIFACT_SCENES, scene_doc)
        _write_background_report(
            job_dir / "json", scene_assets, scene_doc,
            vision_rejections=(scene_resolver.core.vision_rejections if scene_resolver else None),
            merge=only_scene_ids is not None,
        )

    audio_metadata: dict[str, Any] = {}
    public_narration_ref: str | None = None
    public_music_ref: str | None = None
    if render_tts:
        audio_metadata, public_narration_ref, public_music_ref = _synthesize_narration_and_mix(
            job_dir,
            scene_doc,
            tts_config=tts_config,
            tts_client=tts_client,
            assets_dir=assets_dir,
            public_assets_dir=public_assets_dir,
        )
    # Write the dynamically updated scene durations back to scenes.json (TTS may
    # have adjusted per-scene durations to match speech length).
    if render_tts:
        write_json(job_dir / ARTIFACT_SCENES, scene_doc)

    # Build the manifest. When this pass only ran TTS (Shorts phase 2), reuse the
    # scene list written by the earlier background pass so we never clobber it.
    if render_backgrounds and only_scene_ids is not None:
        # Lazy re-gen merge: replace only the re-genned scenes, keep the rest.
        try:
            from video_agent.utils.json_io import read_json as _rj3
            prev = _rj3(job_dir / ARTIFACT_ASSETS) or {}
        except Exception:
            prev = {}
        prev_scenes = prev.get("scenes", []) if isinstance(prev, dict) else []
        fresh = {a["scene_id"]: a for a in scene_assets}
        manifest_scenes = [fresh.get(s.get("scene_id"), s) for s in prev_scenes]
        thumbnail_source = (prev.get("thumbnail_source") if isinstance(prev, dict) else None) or (
            manifest_scenes[0].get("background") if manifest_scenes else None
        )
    elif render_backgrounds:
        manifest_scenes = scene_assets
        thumbnail_source = scene_assets[0]["background"] if scene_assets else None
    else:
        try:
            from video_agent.utils.json_io import read_json as _rj2
            prev = _rj2(job_dir / ARTIFACT_ASSETS)
        except Exception:
            prev = {}
        prev = prev if isinstance(prev, dict) else {}
        manifest_scenes = prev.get("scenes", [])
        thumbnail_source = prev.get("thumbnail_source")

    if render_tts:
        audio_block: dict[str, Any] = {
            "narration": public_narration_ref, "music": public_music_ref, **audio_metadata
        }
    else:
        # This pass produced no audio (background-only or lazy fallback re-gen) —
        # preserve the audio block the TTS/mix pass already wrote so we never
        # clobber it back to nulls (that silenced the rendered video).
        try:
            from video_agent.utils.json_io import read_json as _rja
            _prevm = _rja(job_dir / ARTIFACT_ASSETS) or {}
        except Exception:
            _prevm = {}
        audio_block = (_prevm.get("audio") if isinstance(_prevm, dict) else None) or {
            "narration": public_narration_ref, "music": public_music_ref, **audio_metadata
        }
    manifest = {
        "audio": audio_block,
        "scenes": manifest_scenes,
        "thumbnail_source": thumbnail_source,
    }
    write_json(job_dir / ARTIFACT_ASSETS, manifest)
    _write_audio_progress(job_dir, 100.0, "completed")
    return manifest
