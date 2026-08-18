from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BrandAssetError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BrandClip:
    path: Path
    duration_sec: float


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def _contained_regular_file(path: Path, media_root: Path) -> Path:
    root = media_root.resolve(strict=True)
    unresolved = path if path.is_absolute() else root / path
    cursor = unresolved
    while cursor != root:
        if cursor.is_symlink():
            raise BrandAssetError("brand clip cannot traverse a symlink")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    candidate = unresolved.resolve(strict=True)
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise BrandAssetError("brand clip must be a regular file inside the V2 media root")
    return candidate


def probe_brand_clip(
    path: Path,
    media_root: Path,
    *,
    runner: RunCommand = subprocess.run,
) -> BrandClip:
    candidate = _contained_regular_file(path, media_root)
    try:
        result = runner(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type",
                "-of",
                "json",
                str(candidate),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BrandAssetError("brand clip media probe failed") from exc
    if result.returncode != 0:
        raise BrandAssetError("brand clip media probe rejected the file")
    try:
        payload: Any = json.loads(result.stdout)
        duration = float(payload["format"]["duration"])
        streams = payload["streams"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BrandAssetError("brand clip probe returned invalid metadata") from exc
    if duration <= 0 or not isinstance(streams, list):
        raise BrandAssetError("brand clip must have positive duration")
    if not any(stream.get("codec_type") == "video" for stream in streams):
        raise BrandAssetError("brand clip must contain a video stream")
    return BrandClip(path=candidate, duration_sec=duration)


def probe_brand_clips(
    paths: Iterable[Path],
    media_root: Path,
    *,
    runner: RunCommand = subprocess.run,
) -> tuple[BrandClip, ...]:
    return tuple(probe_brand_clip(path, media_root, runner=runner) for path in paths)
