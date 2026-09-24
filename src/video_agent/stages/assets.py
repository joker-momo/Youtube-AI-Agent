from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
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
    SUPPORTED_IMAGE_SUFFIXES,
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
from video_agent.contracts import ARTIFACT_ASSETS, ARTIFACT_SCENES, EVENT_LOG, repo_root
from video_agent.shorts.visual_semantic import build_semantic_analyzer
from video_agent.storage.public_jobs import prepare_public_job_dir
from video_agent.utils.json_io import write_json
from video_agent.utils.logging import EventLogger
from video_agent.visual.source_window_selection import (
    FfmpegWindowSampler,
    SourceWindowPolicy,
    apply_selection_to_scene,
    build_selection_report,
    center_cover_retained_fraction,
    parse_source_window_policy,
    probe_source_video,
    raise_for_rejections,
    required_window_frames,
    select_source_window,
    skipped_item,
)

SOURCE_WINDOW_REPORT = "source_window_selection.json"
# SigLIP ranks intent and required subjects only; it cannot ground these, so
# they stay UNKNOWN (fail closed) rather than silently passing.
_UNGROUNDED_REQUIRED_FIELDS = (
    "required_action_tags",
    "required_environment_tags",
    "required_evidence_tags",
)
_FORBIDDEN_FIELDS = ("forbidden_subject_tags", "forbidden_action_tags", "forbidden_evidence_tags")


class _AssetWriteStage:
    """Keep a full background pass from exposing partially replaced media."""

    def __init__(self, *, enabled: bool, job_dir: Path) -> None:
        self.enabled = enabled
        self._targets: dict[Path, Path] = {}
        self._tmp = (
            TemporaryDirectory(prefix=".asset-stage-", dir=job_dir)
            if enabled
            else None
        )
        self.root = Path(self._tmp.name) if self._tmp is not None else None

    def target(self, final_path: Path, namespace: str) -> Path:
        if not self.enabled or self.root is None:
            return final_path
        staged_path = self.root / namespace / final_path.name
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        self._targets[final_path] = staged_path
        return staged_path

    def commit(self) -> None:
        if not self.enabled:
            return
        pending: list[tuple[Path, Path]] = []
        try:
            for final_path, staged_path in self._targets.items():
                if not staged_path.exists():
                    continue
                next_path = final_path.with_name(f".{final_path.name}.asset-stage-next")
                materialize_media(staged_path, next_path)
                pending.append((next_path, final_path))
            for next_path, final_path in pending:
                next_path.replace(final_path)
        finally:
            self.cleanup()

    def cleanup(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None


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
    source_window_selection: dict[str, Any] | None = None,
    render_fps: int = 30,
    render_resolution: str = "1920x1080",
) -> dict[str, Any]:
    # Validate before any media or model work (spec: field-specific, fail early).
    source_window_policy = parse_source_window_policy(source_window_selection)
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = job_dir.parent
    for parent in job_dir.parents:
        if (parent / "remotion").is_dir():
            workspace_root = parent
            break
    public_assets_dir = prepare_public_job_dir(workspace_root, job_dir.name) / "assets"
    public_assets_dir.mkdir(parents=True, exist_ok=True)
    asset_stage = _AssetWriteStage(
        enabled=render_backgrounds and only_scene_ids is None,
        job_dir=job_dir,
    )
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
        graphic = scene.get("graphic") if isinstance(scene.get("graphic"), dict) else {}
        asset_refs = (
            scene.get("asset_refs")
            if isinstance(scene.get("asset_refs"), dict)
            else {}
        )
        primary_is_graphic_companion = (
            primary_asset is not None
            and primary_asset.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
            and asset_refs.get("primary_source") == "chatgpt_image"
            and bool(graphic.get("needed") or graphic.get("image_ref"))
        )
        # A generated scene image and a generated graphic card are separate
        # foreground candidates. When the graphic card is active, do not let the
        # scene image consume the background slot and bypass native stock video.
        local_directory_image = _find_local_scene_image(scene["id"], source_dir)
        if primary_is_graphic_companion:
            local_image = local_directory_image
            local_image_source = "local_directory" if local_image else None
        else:
            local_image = primary_asset or local_directory_image
            local_image_source = (
                "asset_refs_primary"
                if primary_asset is not None
                else ("local_directory" if local_directory_image else None)
            )
        stock_asset = None
        scene_dur = float(scene.get("duration_sec") or 30)
        if not local_image and stock_service:
            stock_asset = stock_service.get_scene_asset(scene, channel_id, job_dir.name)
        if (
            primary_is_graphic_companion
            and not local_image
            and (
                not stock_asset
                or stock_asset.get("provider") == "graphic_fallback"
            )
        ):
            # Preserve the existing graceful fallback when no usable background
            # can be acquired. The renderer recognizes this as image-backed and
            # shows the moving brand background behind the graphic card.
            local_image = primary_asset
            local_image_source = "asset_refs_primary"
            stock_asset = None
        # Encode all scene backgrounds to video so Remotion renders one media path.
        asset_suffix = ".mp4"
        image_path = assets_dir / f"{scene['id']}{asset_suffix}"
        staged_image_path = asset_stage.target(image_path, "job-assets")
        public_image_path = public_assets_dir / image_path.name
        staged_public_image_path = asset_stage.target(
            public_image_path, "public-assets"
        )
        # media_kind records whether the SOURCE was real video footage or a still
        # image (photo / AI image / placeholder), so the UI can preview it as a
        # <video> or <img> even though every asset is encoded to .mp4 for render.
        media_kind = "video"
        preview_still = assets_dir / f"{scene['id']}_preview.jpg"
        staged_preview_still = asset_stage.target(preview_still, "job-previews")
        if local_image:
            if local_image.suffix.lower() == ".mp4":
                if local_image.resolve() != staged_image_path.resolve():
                    materialize_media(local_image, staged_image_path)
            else:
                _write_video_from_image(
                    local_image,
                    staged_image_path,
                    scene_dur,
                    is_portrait=is_portrait,
                )
                if _write_preview_still(local_image, staged_preview_still):
                    media_kind = "image"
            source = local_image_source or "local_directory"
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
                materialize_media(library_path, staged_image_path)
            else:
                _write_video_from_image(
                    library_path,
                    staged_image_path,
                    scene_dur,
                    is_portrait=is_portrait,
                )
                if _write_preview_still(library_path, staged_preview_still):
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
                staged_image_path,
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
        materialize_media(staged_image_path, staged_public_image_path)
        public_ref = f"jobs/{job_dir.name}/assets/{image_path.name}"
        scene["asset_refs"]["background"] = public_ref
        # Whether the SOURCE was real footage or a photo-backed encode. The
        # renderer must not treat a photo-backed .mp4 as a living background
        # (bug-455: static Pexels photos masquerading as video at 14:00/25:00).
        scene["asset_refs"]["background_media_kind"] = media_kind

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

    asset_stage.commit()

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

    if source_window_policy is not None and render_backgrounds:
        # Post-TTS durations are final here; the report precedes render_props.json.
        report = _select_long_source_windows(
            job_dir,
            scene_doc,
            manifest_scenes,
            source_window_policy,
            fps=render_fps,
            target_aspect=_aspect_ratio(render_resolution),
        )
        write_json(job_dir / ARTIFACT_SCENES, scene_doc)
        raise_for_rejections(report)

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


def _aspect_ratio(resolution: str) -> float:
    try:
        width, height = (int(v) for v in str(resolution).lower().split("x", 1))
        return width / height if width > 0 and height > 0 else 16 / 9
    except (TypeError, ValueError):
        return 16 / 9


def _scene_tags(scene: dict[str, Any], fields: tuple[str, ...]) -> dict[str, list[str]]:
    return {f: [str(t) for t in (scene.get(f) or []) if str(t).strip()] for f in fields}


class _LocalSemanticWindowEvaluator:
    """Per-frame evidence from the local SigLIP adapter for one scene.

    Each frame is evaluated on its own (the adapter averages over the images it
    receives), so a single on-topic frame cannot hide off-topic ones. A missing
    analyzer yields CAPABILITY_UNAVAILABLE, which the selector rejects.
    """

    def __init__(self, analyzer_factory: Callable[[], Any], scene: dict[str, Any], asset_id: str | None) -> None:
        self._analyzer_factory = analyzer_factory
        self._intent = str(scene.get("visual_prompt") or "").strip() or str(
            scene.get("on_screen_text") or ""
        ).strip()
        self._required = _scene_tags(scene, ("required_subject_tags",))
        forbidden = _scene_tags(scene, _FORBIDDEN_FIELDS)
        self._forbidden = forbidden
        self._asset_id = asset_id
        ungrounded = _scene_tags(scene, _UNGROUNDED_REQUIRED_FIELDS)
        self._unverifiable = [
            {"requirement": f"forbidden_evidence:{tag}", "status": "UNKNOWN", "confidence": None,
             "reason": "local SigLIP cannot ground forbidden evidence"}
            for tags in forbidden.values() for tag in tags
        ] + [
            {"requirement": f"{field_name}:{tag}", "status": "UNKNOWN", "confidence": None,
             "reason": "local SigLIP cannot verify this requirement"}
            for field_name, tags in ungrounded.items() for tag in tags
        ]

    def __call__(self, images: list[Any]) -> list[list[dict[str, Any]]]:
        analyzer = self._analyzer_factory()
        adapters = list(getattr(analyzer, "adapters", None) or [])
        if not adapters or not self._intent:
            reason = "local semantic analyzer unavailable" if not adapters else "scene has no visual intent"
            return [[{"requirement": "topic:visual_intent", "status": "CAPABILITY_UNAVAILABLE",
                      "confidence": None, "reason": reason}] for _ in images]
        per_frame: list[list[dict[str, Any]]] = []
        for image in images:
            records: list[dict[str, Any]] = []
            for adapter in adapters:
                records.extend(adapter.evaluate(
                    [image],
                    required_tags=self._required,
                    forbidden_tags=self._forbidden,
                    visual_intent=self._intent,
                    asset_id=self._asset_id,
                ))
            per_frame.append(records + list(self._unverifiable))
        return per_frame


def _source_window_skip_reason(scene: dict[str, Any], scene_asset: dict[str, Any] | None) -> str | None:
    """Only a background the legacy renderer plays as native video is eligible."""
    if scene_asset is None or not scene_asset.get("background"):
        return "decode_unavailable"
    graphic = scene.get("graphic")
    if isinstance(graphic, dict) and graphic.get("image_ref"):
        return "non_native_video"
    if scene_asset.get("media_kind") != "video" or scene_asset.get("source") == "generated_placeholder":
        return "non_native_video"
    refs = scene.get("asset_refs") if isinstance(scene.get("asset_refs"), dict) else {}
    if not str(refs.get("background") or "").endswith(".mp4"):
        return "non_native_video"
    return None


def _select_long_source_windows(
    job_dir: Path,
    scene_doc: dict[str, Any],
    manifest_scenes: list[dict[str, Any]],
    policy: SourceWindowPolicy,
    *,
    fps: int,
    target_aspect: float,
) -> dict[str, Any]:
    logger = EventLogger(job_dir / EVENT_LOG)
    by_scene = {s.get("scene_id"): s for s in manifest_scenes if isinstance(s, dict)}
    analyzer_cache: list[Any] = []

    def analyzer() -> Any:
        if not analyzer_cache:
            analyzer_cache.append(build_semantic_analyzer(policy.semantic.as_local_qa_config()))
        return analyzer_cache[0]

    items: list[dict[str, Any]] = []
    for scene in scene_doc["scenes"]:
        if not isinstance(scene.get("asset_refs"), dict):
            scene["asset_refs"] = {}
        refs = scene["asset_refs"]
        scene_id = str(scene["id"])
        asset_ref = refs.get("background")
        scene_duration = float(scene.get("duration_sec") or 0.0)
        required = required_window_frames(scene_duration, fps)
        scene_asset = by_scene.get(scene_id)
        reason = "disabled" if not policy.enabled else _source_window_skip_reason(scene, scene_asset)
        probe = None
        if reason is None:
            probe = probe_source_video(Path(scene_asset["background"]))
            if probe is None:
                reason = "decode_unavailable"
        if reason is not None or probe is None:
            item = skipped_item(
                scene_id=scene_id,
                asset_ref=asset_ref,
                reason=reason or "decode_unavailable",
                source_duration_sec=probe.duration_sec if probe else None,
                required_duration_in_frames=required,
            )
        else:
            item = select_source_window(
                scene_id=scene_id,
                asset_ref=str(asset_ref),
                source_duration_sec=probe.duration_sec,
                scene_duration_sec=scene_duration,
                fps=fps,
                crop_retained_fraction=center_cover_retained_fraction(probe.width, probe.height, target_aspect),
                policy=policy,
                sampler=FfmpegWindowSampler(Path(scene_asset["background"]), fps),
                semantic_evaluator=_LocalSemanticWindowEvaluator(
                    analyzer, scene, scene_asset.get("asset_id") or scene_id
                ),
            )
        apply_selection_to_scene(refs, item)
        items.append(item)
        _log_source_window_item(logger, job_dir.name, item)

    report = build_selection_report(items, fps=fps)
    write_json((job_dir / ARTIFACT_SCENES).parent / SOURCE_WINDOW_REPORT, report)
    return report


_SOURCE_WINDOW_EVENTS = {
    "selected": "LONG_SOURCE_WINDOW_SELECTED",
    "skipped": "LONG_SOURCE_WINDOW_SKIPPED",
    "rejected": "LONG_SOURCE_WINDOW_REJECTED",
}


def _log_source_window_item(logger: EventLogger, job_id: str, item: dict[str, Any]) -> None:
    logger.log(_SOURCE_WINDOW_EVENTS[item["status"]], {
        "job_id": job_id,
        "scene_id": item["scene_id"],
        "status": item["status"],
        "reason": item.get("reason"),
        "asset_ref": item.get("asset_ref"),
        "source_duration_sec": item.get("source_duration_sec"),
        "selected_window_start_in_frames": item.get("selected_window_start_in_frames"),
        "selected_window_end_in_frames": item.get("selected_window_end_in_frames"),
        "score": item.get("score"),
        "rejected_window_counts": item.get("rejected_window_counts"),
        "analysis_runtime_ms": item.get("analysis_runtime_ms", 0),
    })
