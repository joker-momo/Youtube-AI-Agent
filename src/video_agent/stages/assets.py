from __future__ import annotations

import math
import shutil
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


def _write_placeholder_image(path: Path, scene: dict[str, Any], color: tuple[int, int, int], palette: dict[str, str]) -> None:
    image = Image.new("RGB", (1920, 1080), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 760, 1920, 1080), fill=_hex_to_rgb(palette["text"]))
    draw.text((96, 820), scene["on_screen_text"], fill=_hex_to_rgb(palette["accent"]))
    image.save(path, quality=92)


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
        local_image = _find_local_scene_image(scene["id"], source_dir)
        stock_asset = None
        if not local_image and stock_service:
            stock_asset = stock_service.get_scene_asset(scene, channel_id, job_dir.name)
        image_suffix = local_image.suffix if local_image else ".jpg"
        image_path = assets_dir / f"{scene['id']}{image_suffix}"
        if local_image:
            shutil.copy2(local_image, image_path)
            source = "local_directory"
            source_path = str(local_image.resolve())
            extra_manifest = {}
        elif stock_asset:
            library_path = stock_service.library.root / stock_asset["file_path"]
            shutil.copy2(library_path, image_path)
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
            _write_placeholder_image(image_path, scene, colors[index % len(colors)], palette)
            source = "generated_placeholder"
            source_path = None
            extra_manifest = {}
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
    tts_provider = tts_config.get("provider", "mock-local")
    narration_path = assets_dir / "narration.wav"
    audio_metadata = {"provider": "mock-local", "source": "silent_placeholder", "sample_rate": 44100}
    if tts_provider == "mock-local":
        _write_silent_wav(narration_path, int(scene_doc["total_duration_sec"]))
    else:
        client = tts_client or build_tts_client(tts_config)
        audio_metadata = synthesize_scene_track(scene_doc, narration_path, tts_config, client) | {"source": "tts"}
    public_narration_path = public_assets_dir / "narration.wav"
    shutil.copy2(narration_path, public_narration_path)
    public_narration_ref = f"jobs/{job_dir.name}/assets/narration.wav"
    manifest = {
        "audio": {"narration": public_narration_ref, "music": None, **audio_metadata},
        "scenes": scene_assets,
        "thumbnail_source": scene_assets[0]["background"],
    }
    write_json(job_dir / ARTIFACT_ASSETS, manifest)
    return manifest
