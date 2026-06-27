from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from video_agent.assets.audio_ops import (  # extracted shared primitives (P1)
    _synthesize_narration_and_mix,
    _write_audio_progress,
)
from video_agent.assets.materialize import materialize_media
from video_agent.assets.media_ops import (  # extracted shared primitives (P1)
    _write_placeholder_video,
    _write_preview_still,
    _write_video_from_image,
)
from video_agent.assets.scene_prep import (  # extracted shared helpers (P1)
    _background_source_label,
    _find_asset_refs_primary,
    _find_local_scene_image,
    _resolve_source_dir,
    _write_background_report,
)
from video_agent.assets.service import StockAssetService
from video_agent.assets.visual_diversity.integration import (
    finalize_visual_diversity_report,
    prepare_visual_diversity,
    record_scene_selection,
)
from video_agent.contracts import ARTIFACT_ASSETS, ARTIFACT_SCENES, repo_root
from video_agent.storage.public_jobs import prepare_public_job_dir
from video_agent.utils.json_io import write_json


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
    render_backgrounds: bool = True,
    render_tts: bool = True,
    on_scene_resolved: Callable[[dict[str, Any]], None] | None = None,
    vision_qa_fn: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
    only_scene_ids: set[str] | None = None,
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

    source_dir = _resolve_source_dir(visual_config.get("source_dir"))
    stock_service = (
        StockAssetService(
            visual_config,
            stock_client=stock_client,
            download_client=download_client,
            image_gen_fn=image_gen_fn,
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
        if not local_image and stock_service:
            stock_asset = stock_service.get_scene_asset(scene, channel_id, job_dir.name)
        # Force all scene backgrounds to video so Remotion always renders OffthreadVideo.
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
                library_path = stock_service.core.library.root / stock_asset["file_path"]
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
            elif stock_service:
                extra_manifest = {"stock_errors": stock_service.core.last_errors}
            record_scene_selection(diversity_run, scene=scene, selected_asset=None, is_placeholder=True)
        public_image_path = public_assets_dir / image_path.name
        materialize_media(image_path, public_image_path)
        public_ref = f"jobs/{job_dir.name}/assets/{image_path.name}"
        scene["asset_refs"]["background"] = public_ref

        scene_asset = {
            "scene_id": scene["id"],
            "background": str(image_path.resolve()),
            "public_background": public_ref,
            "source": source,
            "source_path": source_path,
        }
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
        # Persist asset_refs + the per-scene background sourcing report so the
        # Shorts Studio UI can show which source each scene used.
        write_json(job_dir / ARTIFACT_SCENES, scene_doc)
        _write_background_report(
            job_dir / "json", scene_assets, scene_doc,
            vision_rejections=(stock_service.core.vision_rejections if stock_service else None),
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
