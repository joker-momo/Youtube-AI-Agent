from __future__ import annotations

from pathlib import Path

ARTIFACT_SCRIPT = "json/script.json"
ARTIFACT_SCENES = "json/scenes.json"
ARTIFACT_ASSETS = "json/assets_manifest.json"
ARTIFACT_VISUAL_REVIEW = "json/visual_review.json"
ARTIFACT_VISUAL_CONTACT_SHEET = "outputs/visual_contact_sheet.jpg"
ARTIFACT_RENDER_PROPS = "json/render_props.json"
ARTIFACT_SEO = "json/seo.json"
ARTIFACT_REPORT = "outputs/report.md"
ARTIFACT_VIDEO = "outputs/video.mp4"
ARTIFACT_THUMBNAIL = "outputs/thumbnail.jpg"
EVENT_LOG = "events.jsonl"

# Added for Option 2 structure
ARTIFACT_IDEA = "json/idea.json"
ARTIFACT_APPROVALS = "json/approvals.json"
ARTIFACT_REVIEW = "json/review.json"
ARTIFACT_AUDIO_QA = "json/audio_qa.json"
ARTIFACT_PERSONA_EVAL = "json/persona_eval.json"


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

