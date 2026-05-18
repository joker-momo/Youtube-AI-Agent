from __future__ import annotations

from pathlib import Path

ARTIFACT_SCRIPT = "script.json"
ARTIFACT_SCENES = "scenes.json"
ARTIFACT_ASSETS = "assets_manifest.json"
ARTIFACT_RENDER_PROPS = "render_props.json"
ARTIFACT_SEO = "seo.json"
ARTIFACT_REPORT = "report.md"
ARTIFACT_VIDEO = "video.mp4"
ARTIFACT_THUMBNAIL = "thumbnail.jpg"
EVENT_LOG = "events.jsonl"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
