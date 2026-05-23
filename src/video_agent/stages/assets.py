from __future__ import annotations

import math
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from video_agent.assets.service import StockAssetService
from video_agent.contracts import ARTIFACT_ASSETS, repo_root
from video_agent.tts import build_tts_client, synthesize_scene_track
from video_agent.utils.json_io import write_json

SUPPORTED_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


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


def _write_placeholder_image(path: Path, scene: dict[str, Any], color: tuple[int, int, int], palette: dict[str, str]) -> None:
    image = Image.new("RGB", (1920, 1080), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 760, 1920, 1080), fill=_hex_to_rgb(palette["text"]))
    draw.text((96, 820), scene["on_screen_text"], fill=_hex_to_rgb(palette["accent"]))
    image.save(path, quality=92)


def _write_video_from_image(image_path: Path, output_path: Path, duration_sec: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
        "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080",
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


def _write_placeholder_video(path: Path, scene: dict[str, Any], color: tuple[int, int, int], palette: dict[str, str], duration_sec: float) -> None:
    temp_image = path.with_suffix(".jpg")
    _write_placeholder_image(temp_image, scene, color, palette)
    try:
        _write_video_from_image(temp_image, path, duration_sec)
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
    filter_complex = (
        f"[0:a]volume={voice_gain_db}dB[vox];"
        f"[1:a]volume={bgm_gain_db}dB,aloop=loop=-1:size=2147483647[bgmraw];"
        f"[bgmraw][vox]sidechaincompress=threshold=0.03:ratio={ratio:.2f}:attack=20:release=300:makeup=1[bgmduck];"
        f"[vox][bgmduck]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,"
        f"{pan}loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}[out]"
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


def prepare_assets(
    job_dir: Path,
    style_dna: dict[str, Any],
    scene_doc: dict[str, Any],
    visual_config: dict[str, Any] | None = None,
    tts_config: dict[str, Any] | None = None,
    channel_id: str = "unknown-channel",
    stock_client: Any | None = None,
    download_client: Any | None = None,
    tts_client: Any | None = None,
) -> dict[str, Any]:
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    public_assets_dir = repo_root() / "remotion/public/jobs" / job_dir.name / "assets"
    public_assets_dir.mkdir(parents=True, exist_ok=True)
    palette = style_dna["palette"]
    colors = [_hex_to_rgb(palette["background"]), _hex_to_rgb(palette["primary"]), _hex_to_rgb(palette["secondary"])]
    visual_config = visual_config or {}
    source_dir = _resolve_source_dir(visual_config.get("source_dir"))
    stock_service = (
        StockAssetService(visual_config, stock_client=stock_client, download_client=download_client)
        if visual_config.get("strategy") in {"auto", "stock_photo_api"}
        else None
    )
    scene_assets = []
    for index, scene in enumerate(scene_doc["scenes"]):
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
                _write_video_from_image(local_image, image_path, scene_dur)
            source = (
                "asset_refs_primary" if primary_asset is not None else "local_directory"
            )
            source_path = str(local_image.resolve())
            extra_manifest = {}
        elif stock_asset:
            library_path = stock_service.library.root / stock_asset["file_path"]
            if library_path.suffix.lower() == ".mp4":
                shutil.copy2(library_path, image_path)
            else:
                _write_video_from_image(library_path, image_path, scene_dur)
            source = "asset_library"
            source_path = str(library_path.resolve())
            extra_manifest = {
                "asset_id": stock_asset["asset_id"],
                "provider": stock_asset["provider"],
                "provider_asset_id": stock_asset["provider_asset_id"],
                "source_url": stock_asset["original_url"],
                "attribution": stock_asset["attribution"],
                "asset_selection": stock_asset.get("asset_selection"),
            }
        else:
            _write_placeholder_video(
                image_path,
                scene,
                colors[index % len(colors)],
                palette,
                scene_dur,
            )
            source = "generated_placeholder"
            source_path = None
            extra_manifest = {"stock_errors": stock_service.last_errors} if stock_service else {}
        public_image_path = public_assets_dir / image_path.name
        shutil.copy2(image_path, public_image_path)
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
        scene_assets.append(scene_asset)
    tts_config = tts_config or {"provider": "mock-local"}
    music_cfg = (tts_config.get("music") or {}) if isinstance(tts_config, dict) else {}
    tts_provider = tts_config.get("provider", "mock-local")
    narration_path = assets_dir / "narration.wav"
    audio_metadata = {"provider": "mock-local", "source": "silent_placeholder", "sample_rate": 44100}
    if tts_provider == "mock-local":
        _write_silent_wav(narration_path, int(scene_doc["total_duration_sec"]))
    elif narration_path.exists() and narration_path.stat().st_size > 0:
        # Narration already synthesized (e.g., by assets_chatgpt stage) — skip re-synthesis.
        audio_metadata = {
            "provider": tts_provider,
            "source": "tts_cached",
            "sample_rate": tts_config.get("sample_rate", 24000),
        }
    else:
        client = tts_client or build_tts_client(tts_config)
        try:
            audio_metadata = synthesize_scene_track(scene_doc, narration_path, tts_config, client) | {"source": "tts"}
        except Exception:
            # Fallback for environments without optional TTS runtime deps
            # or network/model bootstrap failures in external providers.
            _write_silent_wav(narration_path, int(scene_doc["total_duration_sec"]))
            audio_metadata = {
                "provider": "mock-local",
                "source": "silent_placeholder",
                "sample_rate": 44100,
            }
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
                },
            }
        else:
            audio_metadata = {**audio_metadata, "mix": {"bgm_enabled": False, "error": "ffmpeg_mix_failed"}}

    # Write the dynamically updated scene durations back to scenes.json
    write_json(job_dir / "scenes.json", scene_doc)

    manifest = {
        "audio": {"narration": public_narration_ref, "music": public_music_ref, **audio_metadata},
        "scenes": scene_assets,
        "thumbnail_source": scene_assets[0]["background"],
    }
    write_json(job_dir / ARTIFACT_ASSETS, manifest)
    return manifest
