from __future__ import annotations

import math
import re
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw

from video_agent.assets.service import StockAssetService
from video_agent.assets.visual_diversity.integration import (
    finalize_visual_diversity_report,
    prepare_visual_diversity,
    record_scene_selection,
)
from video_agent.contracts import ARTIFACT_ASSETS, repo_root, ARTIFACT_AUDIO_QA, ARTIFACT_SCENES
from video_agent.tts import build_tts_client, synthesize_scene_track
from video_agent.qa.tts_report import audio_qa_report, build_tts_report
from video_agent.storage.public_jobs import prepare_public_job_dir
from video_agent.utils.json_io import write_json

SUPPORTED_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

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

    # Default to localhost since the script is mostly run natively on host machine now
    browser_worker_url = os.environ.get("BROWSER_WORKER_URL", "http://localhost:8001")
    client = BrowserClient(browser_worker_url)
    asyncio.run(
        client.generate_image(
            prompt=prompt,
            project_name=_project_name_from_out_path(out_path),
            out_path=str(out_path),
            aspect_ratio="9:16",
        )
    )

def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _write_silent_wav(path: Path, duration_sec: int, sample_rate: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = duration_sec * sample_rate
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        chunk = b"\x00\x00" * sample_rate
        for _ in range(math.ceil(frame_count / sample_rate)):
            handle.writeframes(chunk)


def _resolve_source_dir(source_dir: str | None) -> Path | None:
    if not source_dir:
        return None
    path = Path(source_dir)
    if not path.is_absolute():
        path = repo_root() / path
    return path


def _find_local_scene_image(scene_id: str, source_dir: Path | None) -> Path | None:
    if not source_dir or not source_dir.exists():
        return None
    for suffix in SUPPORTED_IMAGE_SUFFIXES:
        candidate = source_dir / f"{scene_id}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _find_asset_refs_primary(scene: dict[str, Any], job_dir: Path) -> Path | None:
    """Return the scene's ``asset_refs.primary`` image if it exists.

    Lets the orchestrator (or external image generator) inject a
    job-local image — e.g. ChatGPT-generated artwork at
    ``jobs/<id>/assets/scene-NN.png`` — and have it win over the
    channel's stock-image directory.
    """
    refs = scene.get("asset_refs") or {}
    primary = refs.get("primary")
    if not isinstance(primary, str) or not primary:
        return None
    # Always interpret as job-relative — the field is operator-controlled
    # (model output / external image generator), so an absolute path or
    # ``..`` segment must not escape the job dir and end up mirrored under
    # ``remotion/public/jobs/<id>/assets`` (a publicly-served directory).
    if Path(primary).is_absolute() or ".." in Path(primary).parts:
        return None
    candidate = (job_dir / primary).resolve()
    try:
        candidate.relative_to(job_dir.resolve())
    except ValueError:
        return None
    return candidate if candidate.exists() else None


def _write_placeholder_image(
    path: Path,
    scene: dict[str, Any],
    index: int,
    palette: dict[str, str],
    is_portrait: bool = False
) -> None:
    width, height = (1080, 1920) if is_portrait else (1920, 1080)
    
    # Warm brand color pairings for gradient
    color_pairs = [
        ((74, 93, 77), (110, 128, 113)),   # #4A5D4D to #6E8071 (Muted Olive)
        ((92, 77, 60), (126, 110, 92)),    # #5C4D3C to #7E6E5C (Warm Brown)
        ((140, 79, 62), (172, 111, 94))    # #8C4F3E to #AC6F5E (Muted Terracotta)
    ]
    color1, color2 = color_pairs[index % len(color_pairs)]
    
    is_standard = not (scene.get("layout") or "").startswith("graphic_")
    
    if is_standard:
        # Create a smooth gradient using upscale bilinear filtering
        base = Image.new("RGB", (1, 2))
        base.putpixel((0, 0), color1)
        base.putpixel((0, 1), color2)
        image = base.resize((width, height), resample=Image.Resampling.BILINEAR)
        
        # Soft paper texture: grid of tiny dots with very low opacity (~3.5%)
        texture = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(texture)
        dot_color = (47, 42, 36, 9)
        for x in range(0, width, 7):
            for y in range(0, height, 7):
                draw.point((x, y), fill=dot_color)
        image.paste(texture, (0, 0), texture)
        
        # FIX: Even for standard layouts, draw the text if available so it's not a blank screen
        draw = ImageDraw.Draw(image)
        text_content = scene.get("on_screen_text", "")
        if text_content:
            # Draw semi-transparent background box for readability
            draw.rectangle((0, int(height * 0.7), width, height), fill=_hex_to_rgb(palette["text"]))
            draw.text((int(width * 0.05), int(height * 0.76)), text_content, fill=_hex_to_rgb(palette["accent"]))
            
        image.save(path, quality=92)
    else:
        # Original behavior for graphic layouts (flat color + bottom text box + text)
        image = Image.new("RGB", (width, height), color1)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, int(height * 0.7), width, height), fill=_hex_to_rgb(palette["text"]))
        text_content = scene.get("on_screen_text", "")
        if text_content:
            draw.text((int(width * 0.05), int(height * 0.76)), text_content, fill=_hex_to_rgb(palette["accent"]))
        image.save(path, quality=92)


def _write_video_from_image(image_path: Path, output_path: Path, duration_sec: float, is_portrait: bool = False) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    vf_filter = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" if is_portrait else "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-t",
        f"{max(1.0, float(duration_sec)):.2f}",
        "-vf",
        vf_filter,
        "-r",
        "30",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _write_placeholder_video(
    path: Path,
    scene: dict[str, Any],
    index: int,
    palette: dict[str, str],
    duration_sec: float,
    is_portrait: bool = False,
) -> None:
    temp_image = path.with_suffix(".jpg")
    _write_placeholder_image(temp_image, scene, index, palette, is_portrait=is_portrait)
    try:
        _write_video_from_image(temp_image, path, duration_sec, is_portrait=is_portrait)
    finally:
        try:
            temp_image.unlink()
        except OSError:
            pass


def _choose_bgm_track(job_dir: Path, music_cfg: dict[str, Any]) -> Path | None:
    bgm_dir = repo_root() / "asset_library" / "source" / "bgm"
    if not bgm_dir.exists():
        return None
    candidates = sorted(
        p
        for p in bgm_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
    )
    if not candidates:
        return None
    preferred = str(music_cfg.get("preferred_track") or "").strip()
    if preferred:
        for c in candidates:
            if c.name == preferred:
                return c
    return candidates[0]


def _mix_bgm_with_narration(
    narration_path: Path,
    bgm_path: Path,
    mixed_path: Path,
    *,
    voice_gain_db: float = -4.5,
    bgm_gain_db: float = -24.0,
    duck_db: float = 8.0,
    target_lufs: float = -13.6,
    target_tp: float = 0.0,
    target_lra: float = 4.8,
    out_sample_rate: int = 44100,
    out_bitrate: str = "128k",
    stereo: bool = True,
) -> bool:
    if not narration_path.exists() or not bgm_path.exists():
        return False
    ratio = max(3.0, min(12.0, 1.5 + duck_db * 0.7))
    pan = "pan=stereo|c0=c0|c1=c0," if stereo else ""
    # Apply alimiter after loudnorm: hard ceiling at TP-aware level to prevent
    # clipping when mixed peaks ride the loudness target.
    limiter_ceiling_db = min(target_tp, -1.0)
    filter_complex = (
        f"[0:a]volume={voice_gain_db}dB[vox];"
        f"[1:a]volume={bgm_gain_db}dB,aloop=loop=-1:size=2147483647[bgmraw];"
        f"[bgmraw][vox]sidechaincompress=threshold=0.03:ratio={ratio:.2f}:attack=20:release=300:makeup=1[bgmduck];"
        f"[vox][bgmduck]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,"
        f"{pan}loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra},"
        f"alimiter=limit={limiter_ceiling_db}dB[out]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(narration_path),
        "-i",
        str(bgm_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-c:a",
        "aac",
        "-b:a",
        out_bitrate,
        "-ar",
        str(out_sample_rate),
        str(mixed_path),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    return mixed_path.exists() and mixed_path.stat().st_size > 0


def _write_audio_progress(job_dir: Path, percent: float, stage: str) -> None:
    try:
        from video_agent.storage.atomic import atomic_write_json
        progress_dir = job_dir / "json"
        progress_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(progress_dir / "audio_progress.json", {"percent": percent, "stage": stage})
    except Exception:
        pass


def _background_source_label(scene_asset: dict[str, Any]) -> str:
    """Human label for where a scene's background came from (UI report)."""
    source = str(scene_asset.get("source") or "")
    provider = str(scene_asset.get("provider") or "").lower()
    tier = str(scene_asset.get("asset_tier") or "").lower()
    if source == "generated_placeholder":
        return "Placeholder"
    if provider == "ai_generated" or tier in {"ai_image", "ai_generated"}:
        return "ChatGPT image"
    if provider == "graphic_fallback" or tier == "graphic_fallback":
        return "Graphic"
    if "video" in tier:  # pexels_video
        return "Pexels video"
    if "pexels" in provider or "pexels" in tier:
        return "Pexels photo"
    if "pixabay" in provider:
        return "Pixabay photo"
    if source in {"asset_refs_primary", "local_directory"}:
        return "Local asset"
    return provider.title() or "Unknown"


def _write_background_report(
    json_dir: Path,
    scene_assets: list[dict[str, Any]],
    scene_doc: dict[str, Any],
    vision_rejections: list[dict[str, Any]] | None = None,
) -> None:
    """Per-scene background sourcing report consumed by the Shorts Studio UI."""
    motion_by_id = {s.get("id"): s.get("motion") for s in (scene_doc.get("scenes") or [])}
    entries = []
    for a in scene_assets:
        sid = a.get("scene_id")
        sel = a.get("asset_selection") or {}
        entries.append({
            "scene_id": sid,
            "background_source": a.get("background_source") or _background_source_label(a),
            "provider": a.get("provider"),
            "asset_tier": a.get("asset_tier"),
            "query": sel.get("query"),
            "attribution": a.get("attribution"),
            "source_url": a.get("source_url"),
            "public_background": a.get("public_background"),
            "motion": motion_by_id.get(sid),
        })
    json_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"scenes": entries}
    if vision_rejections:
        report["vision_rejections"] = vision_rejections
    write_json(json_dir / "background_report.json", report)


def _synthesize_narration_and_mix(
    job_dir: Path,
    scene_doc: dict[str, Any],
    *,
    tts_config: dict[str, Any] | None,
    tts_client: Any | None,
    assets_dir: Path,
    public_assets_dir: Path,
) -> tuple[dict[str, Any], str, str | None]:
    """TTS synthesis + optional BGM mix. Returns (audio_metadata, narration_ref, music_ref)."""
    _write_audio_progress(job_dir, 50.0, "tts")
    tts_config = tts_config or {"provider": "mock-local"}
    music_cfg = (tts_config.get("music") or {}) if isinstance(tts_config, dict) else {}
    tts_provider = tts_config.get("provider", "mock-local")
    narration_path = assets_dir / "narration.wav"
    tts_durations_path = assets_dir / "tts_durations.json"
    audio_metadata = {"provider": "mock-local", "source": "silent_placeholder", "sample_rate": 44100}
    if tts_provider == "mock-local":
        _write_silent_wav(narration_path, int(scene_doc["total_duration_sec"]))
    elif narration_path.exists() and narration_path.stat().st_size > 0:
        # Narration already synthesized — skip re-synthesis, but restore per-scene durations.
        audio_metadata = {
            "provider": tts_provider,
            "source": "tts_cached",
            "sample_rate": tts_config.get("sample_rate", 24000),
        }
        # Restore per-scene duration_sec from saved file so scene timings match speech exactly.
        if tts_durations_path.exists():
            try:
                from video_agent.utils.json_io import read_json as _rj
                saved = _rj(tts_durations_path)  # {scene_id: duration_sec}
                for scene in scene_doc["scenes"]:
                    if scene["id"] in saved:
                        scene["duration_sec"] = saved[scene["id"]]
                scene_doc["total_duration_sec"] = int(
                    round(sum(float(s["duration_sec"]) for s in scene_doc["scenes"]))
                )
            except Exception:
                pass  # Non-fatal: fall back to original durations
    else:
        client = tts_client or build_tts_client(tts_config)
        try:
            audio_metadata = synthesize_scene_track(scene_doc, narration_path, tts_config, client) | {"source": "tts"}
            # Persist per-scene durations so future re-renders (tts_cached) stay in sync.
            from video_agent.utils.json_io import write_json as _wj
            _wj(tts_durations_path, {s["id"]: s["duration_sec"] for s in scene_doc["scenes"]})
            try:
                report = build_tts_report(scene_doc["scenes"], audio_metadata, tts_config)
                _wj(job_dir / "tts_report.json", report)
            except Exception:
                pass
        except Exception as exc:
            # Fallback for environments without optional TTS runtime deps
            # or network/model bootstrap failures in external providers.
            _write_silent_wav(narration_path, int(scene_doc["total_duration_sec"]))
            audio_metadata = {
                "provider": "mock-local",
                "source": "silent_placeholder",
                "sample_rate": 44100,
            }
    _write_audio_progress(job_dir, 95.0, "mixing")
    public_narration_path = public_assets_dir / "narration.wav"
    shutil.copy2(narration_path, public_narration_path)
    public_narration_ref = f"jobs/{job_dir.name}/assets/narration.wav"
    public_music_ref = None

    bgm_track = _choose_bgm_track(job_dir, music_cfg)
    if bgm_track is not None:
        bgm_copy = assets_dir / f"bgm{bgm_track.suffix.lower()}"
        shutil.copy2(bgm_track, bgm_copy)
        public_bgm_copy = public_assets_dir / bgm_copy.name
        shutil.copy2(bgm_copy, public_bgm_copy)
        public_music_ref = f"jobs/{job_dir.name}/assets/{bgm_copy.name}"
        mixed_path = assets_dir / "narration_mixed.m4a"
        voice_gain_db = float(music_cfg.get("voice_gain_db", -4.5))
        bgm_gain_db = float(music_cfg.get("level_db", -24.0))
        duck_db = float(music_cfg.get("duck_db", 8.0))
        target_lufs = float(music_cfg.get("target_lufs", -13.6))
        target_tp = float(music_cfg.get("target_tp_dbtp", 0.0))
        target_lra = float(music_cfg.get("target_lra", 4.8))
        out_sr = int(music_cfg.get("sample_rate", 44100))
        out_br = str(music_cfg.get("bitrate", "128k"))
        out_stereo = bool(music_cfg.get("stereo", True))
        if _mix_bgm_with_narration(
            narration_path,
            bgm_copy,
            mixed_path,
            voice_gain_db=voice_gain_db,
            bgm_gain_db=bgm_gain_db,
            duck_db=duck_db,
            target_lufs=target_lufs,
            target_tp=target_tp,
            target_lra=target_lra,
            out_sample_rate=out_sr,
            out_bitrate=out_br,
            stereo=out_stereo,
        ):
            public_mixed_path = public_assets_dir / mixed_path.name
            shutil.copy2(mixed_path, public_mixed_path)
            public_narration_ref = f"jobs/{job_dir.name}/assets/{mixed_path.name}"
            audio_metadata = {
                **audio_metadata,
                "mix": {
                    "bgm_enabled": True,
                    "bgm_track": bgm_track.name,
                    "voice_gain_db": voice_gain_db,
                    "bgm_gain_db": bgm_gain_db,
                    "duck_db": duck_db,
                    "target_lufs": target_lufs,
                    "target_tp": target_tp,
                    "target_lra": target_lra,
                    "sample_rate": out_sr,
                    "bitrate": out_br,
                    "stereo": out_stereo,
                    "limiter_ceiling_db": min(target_tp, -1.0),
                },
            }
            try:
                br_kbps = int(re.sub(r"[^0-9]", "", str(out_br)) or 0) or None
                qa_report = audio_qa_report(
                    integrated_lufs=target_lufs,
                    true_peak_dbtp=target_tp,
                    bitrate_kbps=br_kbps,
                    duration_sec=None,
                    codec="aac",
                    sample_rate=out_sr,
                )
                write_json(job_dir / ARTIFACT_AUDIO_QA, qa_report)
            except Exception:
                pass
        else:
            audio_metadata = {**audio_metadata, "mix": {"bgm_enabled": False, "error": "ffmpeg_mix_failed"}}

    return audio_metadata, public_narration_ref, public_music_ref


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
    colors = [_hex_to_rgb(palette["background"]), _hex_to_rgb(palette["primary"]), _hex_to_rgb(palette["secondary"])]
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
    stock_service = (
        StockAssetService(
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
        if local_image:
            if local_image.suffix.lower() == ".mp4":
                if local_image.resolve() != image_path.resolve():
                    shutil.copy2(local_image, image_path)
            else:
                _write_video_from_image(local_image, image_path, scene_dur, is_portrait=is_portrait)
            source = (
                "asset_refs_primary" if primary_asset is not None else "local_directory"
            )
            source_path = str(local_image.resolve())
            extra_manifest = {}
        elif stock_asset and stock_asset.get("provider") != "graphic_fallback":
            # ai_generated or standard stock API asset
            if "file_path" in stock_asset:
                library_path = stock_service.library.root / stock_asset["file_path"]
            elif "local_path" in stock_asset:
                library_path = Path(stock_asset["local_path"])
            else:
                library_path = Path(stock_asset.get("url", "")) # Should not happen

            if library_path.suffix.lower() == ".mp4":
                shutil.copy2(library_path, image_path)
            else:
                _write_video_from_image(library_path, image_path, scene_dur, is_portrait=is_portrait)
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
                extra_manifest = {"stock_errors": stock_service.last_errors}
            record_scene_selection(diversity_run, scene=scene, selected_asset=None, is_placeholder=True)
        public_image_path = public_assets_dir / image_path.name
        shutil.copy2(image_path, public_image_path)
        public_ref = f"jobs/{job_dir.name}/assets/{image_path.name}"
        scene["asset_refs"]["background"] = public_ref
        
        if stock_asset and stock_asset.get("provider") == "graphic_fallback":
            if (scene.get("layout") or "").startswith("graphic_"):
                scene["asset_refs"]["background"] = ""
                scene["background_mode"] = "paper"
        scene_asset = {
            "scene_id": scene["id"],
            "background": str(image_path.resolve()),
            "public_background": public_ref,
            "source": source,
            "source_path": source_path,
        }
        scene_asset.update(extra_manifest)
        scene_asset["background_source"] = _background_source_label(scene_asset)
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
            vision_rejections=(stock_service.vision_rejections if stock_service else None),
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
    if render_backgrounds:
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

    manifest = {
        "audio": {"narration": public_narration_ref, "music": public_music_ref, **audio_metadata},
        "scenes": manifest_scenes,
        "thumbnail_source": thumbnail_source,
    }
    write_json(job_dir / ARTIFACT_ASSETS, manifest)
    _write_audio_progress(job_dir, 100.0, "completed")
    return manifest
