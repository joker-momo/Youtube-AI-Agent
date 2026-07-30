from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from video_agent.localized_v2.brand_assets import BrandClip
from video_agent.localized_v2.render_props import (
    RenderPropsError,
    compile_render_props,
)

from .locale_fixtures import channel, locale_pack

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


def _file(path: Path, body: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def _inputs(tmp_path: Path) -> dict:
    job_root = tmp_path / "jobs" / "job-v2"
    artifacts = job_root / "artifacts"
    background = _file(artifacts / "assets" / "opening.mp4", b"background")
    graphic = _file(artifacts / "assets" / "opening.png", b"graphic")
    thumbnail = _file(artifacts / "assets" / "thumbnail.png", b"thumbnail")
    narration = _file(artifacts / "audio" / "narration.wav", b"narration")
    clips = {
        name: BrandClip(
            _file(artifacts / "branding" / f"{name}.mp4", name.encode()),
            duration,
        )
        for name, duration in (("intro", 3.0), ("disclaimer", 8.0), ("outro", 4.0))
    }
    promoted = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in {
            background,
            graphic,
            thumbnail,
            narration,
            *(clip.path for clip in clips.values()),
        }
    }
    scenes = {
        "schemaVersion": "localized-scenes-v2/v1",
        "locale": "en-US",
        "scenes": [
            {
                "id": "opening",
                "narration": "Research suggests this routine may support wellbeing.",
                "visualType": "graphic",
                "visualPrompt": "A clear educational routine graphic",
                "searchBrief": {
                    "language": "en",
                    "queries": ["healthy daily routine adult"],
                },
            }
        ],
    }
    timing = {
        "schemaVersion": "localized-audio-timing-v2/v1",
        "locale": "en-US",
        "totalDurationSec": 10.0,
        "scenes": [{"id": "opening", "startSec": 0.0, "durationSec": 10.0}],
    }
    seo = {
        "schemaVersion": "localized-seo-v2/v1",
        "locale": "en-US",
        "title": "Healthy Aging With One Realistic Daily Routine",
        "description": "Research suggests realistic routines may support wellbeing.",
        "tags": ["healthy aging", "daily routine", "wellbeing"],
        "thumbnailText": "ONE REAL ROUTINE",
        "pinnedComment": "Which realistic routine works for you?",
    }
    manifest = {
        "schemaVersion": "localized-assets-v2/v1",
        "locale": "en-US",
        "assets": [
            {
                "sceneId": "opening",
                "role": "background",
                "mediaKind": "video",
                "path": str(background),
                "source": "https://assets.example/background",
                "sha256": hashlib.sha256(background.read_bytes()).hexdigest(),
            },
            {
                "sceneId": "opening",
                "role": "graphic",
                "mediaKind": "image",
                "path": str(graphic),
                "source": "https://assets.example/graphic",
                "sha256": hashlib.sha256(graphic.read_bytes()).hexdigest(),
            },
            {
                "sceneId": "video",
                "role": "thumbnail",
                "mediaKind": "image",
                "path": str(thumbnail),
                "source": "https://assets.example/thumbnail",
                "sha256": hashlib.sha256(thumbnail.read_bytes()).hexdigest(),
            },
        ],
    }
    return {
        "job_root": job_root,
        "promoted_artifacts": promoted,
        "schema_root": SCHEMA_ROOT,
        "channel": channel("en-US"),
        "locale_pack": locale_pack("en-US"),
        "scenes": scenes,
        "timing": timing,
        "seo": seo,
        "asset_manifest": manifest,
        "narration_path": narration,
        "brand_clips": clips,
    }


def test_render_props_are_voice_only_and_graphics_keep_video_backing(
    tmp_path: Path,
) -> None:
    props = compile_render_props(**_inputs(tmp_path))

    scene = props["scenes"][0]
    assert props["render"]["subtitles"] == {"enabled": False}
    assert props["audio"]["music"] is None
    assert "word_segments" not in scene
    assert scene["asset_refs"]["background"].endswith("opening.mp4")
    assert scene["asset_refs"]["background_media_kind"] == "video"
    assert scene["graphic"]["image_ref"].endswith("opening.png")
    assert props["branding"]["hybrid_card_bg"]
    assert props["render"]["duration_sec"] == 25.0


def test_render_props_reject_unpromoted_or_tampered_assets(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    graphic = Path(inputs["asset_manifest"]["assets"][1]["path"])
    inputs["promoted_artifacts"].pop(graphic)

    with pytest.raises(RenderPropsError, match="only promoted"):
        compile_render_props(**inputs)

    inputs = _inputs(tmp_path / "tamper")
    Path(inputs["asset_manifest"]["assets"][0]["path"]).write_bytes(b"tampered")
    with pytest.raises(RenderPropsError, match="integrity"):
        compile_render_props(**inputs)
