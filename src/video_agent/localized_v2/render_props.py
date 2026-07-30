from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from video_agent.localized_v2.brand_assets import BrandClip
from video_agent.localized_v2.config import validate_artifact
from video_agent.localized_v2.contracts import ArtifactKind

DEFAULT_PALETTE = {
    "background": "#F4F1EA",
    "primary": "#315C52",
    "secondary": "#B8754F",
    "accent": "#E2B84A",
    "text": "#23312D",
}


class RenderPropsError(ValueError):
    pass


def _public_ref(path: Path, artifacts_root: Path) -> str:
    return path.resolve().relative_to(artifacts_root.resolve()).as_posix()


def _promoted_file(
    raw: str | Path,
    *,
    artifacts_root: Path,
    promoted: Mapping[Path, str],
    expected_sha256: str | None = None,
) -> Path:
    root = artifacts_root.resolve()
    path = Path(raw)
    candidate = (path if path.is_absolute() else root / path).resolve()
    if not candidate.is_relative_to(root) or candidate not in promoted or not candidate.is_file():
        raise RenderPropsError("render props can reference only promoted V2 job artifacts")
    actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if actual != promoted[candidate]:
        raise RenderPropsError("promoted V2 artifact failed its registry integrity check")
    if expected_sha256 is not None and actual != expected_sha256:
        raise RenderPropsError("promoted V2 asset failed its manifest integrity check")
    return candidate


def _asset_index(
    manifest: dict[str, Any],
    scenes: dict[str, Any],
    *,
    artifacts_root: Path,
    promoted: Mapping[Path, str],
) -> tuple[dict[tuple[str, str], Path], Path]:
    indexed: dict[tuple[str, str], Path] = {}
    for item in manifest["assets"]:
        key = (str(item["sceneId"]), str(item["role"]))
        if key in indexed:
            raise RenderPropsError(f"duplicate V2 asset role: {key}")
        kind = str(item["mediaKind"])
        role = key[1]
        if role == "background" and kind != "video":
            raise RenderPropsError("every scene background must be real video")
        if role in {"graphic", "thumbnail"} and kind != "image":
            raise RenderPropsError(f"{role} assets must be images")
        indexed[key] = _promoted_file(
            item["path"],
            artifacts_root=artifacts_root,
            promoted=promoted,
            expected_sha256=str(item["sha256"]),
        )
    scene_ids = {str(scene["id"]) for scene in scenes["scenes"]}
    for scene in scenes["scenes"]:
        scene_id = str(scene["id"])
        if (scene_id, "background") not in indexed:
            raise RenderPropsError(f"scene {scene_id} is missing its video background")
        has_graphic = (scene_id, "graphic") in indexed
        if (scene["visualType"] == "graphic") != has_graphic:
            raise RenderPropsError(f"scene {scene_id} graphic asset contract does not match")
    allowed_scene_keys = scene_ids | {"video"}
    if any(scene_id not in allowed_scene_keys for scene_id, _role in indexed):
        raise RenderPropsError("asset manifest references an unknown scene")
    thumbnail = indexed.get(("video", "thumbnail"))
    if thumbnail is None:
        raise RenderPropsError("localized V2 render requires one promoted thumbnail")
    return indexed, thumbnail


def compile_render_props(
    *,
    job_root: Path,
    promoted_artifacts: Mapping[Path, str],
    schema_root: Path,
    channel: dict[str, Any],
    locale_pack: dict[str, Any],
    scenes: dict[str, Any],
    timing: dict[str, Any],
    seo: dict[str, Any],
    asset_manifest: dict[str, Any],
    narration_path: Path,
    brand_clips: Mapping[str, BrandClip],
    fps: int = 30,
    resolution: str = "1920x1080",
) -> dict[str, Any]:
    artifacts_root = (job_root / "artifacts").resolve()
    promoted = {
        path.resolve(): sha256
        for path, sha256 in promoted_artifacts.items()
    }
    for payload, kind in (
        (scenes, ArtifactKind.SCENES),
        (timing, ArtifactKind.AUDIO_TIMING),
        (seo, ArtifactKind.SEO),
        (asset_manifest, ArtifactKind.ASSET_MANIFEST),
    ):
        validate_artifact(payload, kind, schema_root)
    locale = str(channel["locale"])
    if any(
        payload["locale"] != locale
        for payload in (locale_pack, scenes, timing, seo, asset_manifest)
    ):
        raise RenderPropsError("localized V2 artifact locales do not match")
    narration = _promoted_file(
        narration_path,
        artifacts_root=artifacts_root,
        promoted=promoted,
    )
    indexed, thumbnail = _asset_index(
        asset_manifest,
        scenes,
        artifacts_root=artifacts_root,
        promoted=promoted,
    )
    required_clips = {"intro", "disclaimer", "outro"}
    if set(brand_clips) != required_clips:
        raise RenderPropsError("intro, disclaimer, and outro brand clips are required")
    verified_clips = {
        name: BrandClip(
            path=_promoted_file(
                clip.path,
                artifacts_root=artifacts_root,
                promoted=promoted,
            ),
            duration_sec=clip.duration_sec,
        )
        for name, clip in brand_clips.items()
    }
    if any(clip.duration_sec <= 0 for clip in verified_clips.values()):
        raise RenderPropsError("brand clip durations must be positive")

    timings = {str(item["id"]): item for item in timing["scenes"]}
    if set(timings) != {str(item["id"]) for item in scenes["scenes"]}:
        raise RenderPropsError("audio timing scene IDs do not match scene artifacts")
    compiled_scenes: list[dict[str, Any]] = []
    for scene in scenes["scenes"]:
        scene_id = str(scene["id"])
        compiled: dict[str, Any] = {
            "id": scene_id,
            "duration_sec": float(timings[scene_id]["durationSec"]),
            "narration": scene["narration"],
            "visual_type": scene["visualType"],
            "visual_prompt": scene["visualPrompt"],
            "on_screen_text": "",
            "caption": "",
            "motion": "slow_push",
            "asset_refs": {
                "background": _public_ref(
                    indexed[(scene_id, "background")],
                    artifacts_root,
                ),
                "background_media_kind": "video",
            },
        }
        if scene["visualType"] == "graphic":
            compiled["graphic"] = {
                "needed": True,
                "prompt": scene["visualPrompt"],
                "image_ref": _public_ref(
                    indexed[(scene_id, "graphic")],
                    artifacts_root,
                ),
            }
        compiled_scenes.append(compiled)

    content_duration = float(timing["totalDurationSec"])
    full_duration = content_duration + sum(
        clip.duration_sec for clip in verified_clips.values()
    )
    props = {
        "schemaVersion": "localized-render-props-v2/v1",
        "locale": locale,
        "composition": channel["render"]["composition"],
        "channel": {
            "id": channel["channelId"],
            "name": channel["brand"]["name"],
            "description": "",
        },
        "style": {"palette": DEFAULT_PALETTE},
        "render": {
            "fps": fps,
            "resolution": resolution,
            "duration_sec": full_duration,
            "duration_in_frames": round(full_duration * fps),
            "subtitles": {"enabled": False},
        },
        "scenes": compiled_scenes,
        "audio": {
            "narration": _public_ref(narration, artifacts_root),
            "music": None,
        },
        "seo": {
            "title": seo["title"],
            "description": seo["description"],
            "thumbnail_path": _public_ref(thumbnail, artifacts_root),
            "thumbnail_text": seo["thumbnailText"],
        },
        "branding": {
            "intro_video_path": _public_ref(
                verified_clips["intro"].path,
                artifacts_root,
            ),
            "disclaimer_video_path": _public_ref(
                verified_clips["disclaimer"].path,
                artifacts_root,
            ),
            "outro_video_path": _public_ref(
                verified_clips["outro"].path,
                artifacts_root,
            ),
            "intro_sec": verified_clips["intro"].duration_sec,
            "disclaimer_sec": verified_clips["disclaimer"].duration_sec,
            "outro_sec": verified_clips["outro"].duration_sec,
            "watermark_enabled": False,
            "show_channel_name_overlay": False,
            "hybrid_card_bg": _public_ref(
                verified_clips["intro"].path,
                artifacts_root,
            ),
        },
    }
    validate_artifact(props, ArtifactKind.RENDER_PROPS, schema_root)
    return props
