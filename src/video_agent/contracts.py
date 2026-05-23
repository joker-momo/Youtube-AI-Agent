from __future__ import annotations

from pathlib import Path

ARTIFACT_SCRIPT = "script.json"
ARTIFACT_SCENES = "scenes.json"
ARTIFACT_ASSETS = "assets_manifest.json"
ARTIFACT_VISUAL_REVIEW = "visual_review.json"
ARTIFACT_VISUAL_CONTACT_SHEET = "visual_contact_sheet.jpg"
ARTIFACT_RENDER_PROPS = "render_props.json"
ARTIFACT_SEO = "seo.json"
ARTIFACT_REPORT = "report.md"
ARTIFACT_VIDEO = "video.mp4"
ARTIFACT_THUMBNAIL = "thumbnail.jpg"
EVENT_LOG = "events.jsonl"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_env() -> None:
    import os
    env_path = repo_root() / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    if key and key not in os.environ:
                        os.environ[key] = val

