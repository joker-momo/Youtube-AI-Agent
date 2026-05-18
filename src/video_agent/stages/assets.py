from __future__ import annotations

import math
import shutil
import wave
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from video_agent.contracts import ARTIFACT_ASSETS, repo_root
from video_agent.utils.json_io import write_json


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


def prepare_assets(job_dir: Path, style_dna: dict[str, Any], scene_doc: dict[str, Any]) -> dict[str, Any]:
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    public_assets_dir = repo_root() / "remotion/public/jobs" / job_dir.name / "assets"
    public_assets_dir.mkdir(parents=True, exist_ok=True)
    palette = style_dna["palette"]
    colors = [_hex_to_rgb(palette["background"]), _hex_to_rgb(palette["primary"]), _hex_to_rgb(palette["secondary"])]
    scene_assets = []
    for index, scene in enumerate(scene_doc["scenes"]):
        image_path = assets_dir / f"{scene['id']}.jpg"
        image = Image.new("RGB", (1920, 1080), colors[index % len(colors)])
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 760, 1920, 1080), fill=_hex_to_rgb(palette["text"]))
        draw.text((96, 820), scene["on_screen_text"], fill=_hex_to_rgb(palette["accent"]))
        image.save(image_path, quality=92)
        public_image_path = public_assets_dir / image_path.name
        shutil.copy2(image_path, public_image_path)
        public_ref = f"jobs/{job_dir.name}/assets/{image_path.name}"
        scene["asset_refs"]["background"] = public_ref
        scene_assets.append({"scene_id": scene["id"], "background": str(image_path.resolve()), "public_background": public_ref})
    narration_path = assets_dir / "narration.wav"
    _write_silent_wav(narration_path, int(scene_doc["total_duration_sec"]))
    public_narration_path = public_assets_dir / "narration.wav"
    shutil.copy2(narration_path, public_narration_path)
    public_narration_ref = f"jobs/{job_dir.name}/assets/narration.wav"
    manifest = {
        "audio": {"narration": public_narration_ref, "music": None},
        "scenes": scene_assets,
        "thumbnail_source": scene_assets[0]["background"],
    }
    write_json(job_dir / ARTIFACT_ASSETS, manifest)
    return manifest
