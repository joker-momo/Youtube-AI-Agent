from __future__ import annotations

import enum
import logging
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


class TopicFamily(str, enum.Enum):
    MOVEMENT = "MOVEMENT"
    NUTRITION = "NUTRITION"
    SLEEP = "SLEEP"
    MENTAL_LOAD = "MENTAL_LOAD"
    GENERAL = "GENERAL"


def resolve_topic_family(script_dict: dict) -> TopicFamily:
    """Precedence: explicit -> mapped pillar -> text classifier -> GENERAL."""
    # a) Explicit
    explicit = script_dict.get("topic_family") or script_dict.get("topic")
    if explicit:
        try:
            return TopicFamily(str(explicit).strip().upper())
        except ValueError:
            pass

    # b) Mapped pillar
    pillar = str(script_dict.get("pillar", "")).strip().upper()
    if pillar in ("PAN", "BREAD", "NUTRITION", "ALIMENTACION", "ALIMENTACIÓN", "DIET"):
        return TopicFamily.NUTRITION
    if pillar in ("EJERCICIO", "MOVEMENT", "FITNESS", "MOVIMIENTO"):
        return TopicFamily.MOVEMENT
    if pillar in ("SLEEP", "SUENO", "SUEÑO", "DORMIR", "DESCANSO"):
        return TopicFamily.SLEEP
    if pillar in ("MENTAL_LOAD", "STRESS", "MENTAL", "ESTRES", "ESTRÉS", "CARGA_MENTAL"):
        return TopicFamily.MENTAL_LOAD
        
    # c) Deterministic classifier
    text = " ".join([
        str(script_dict.get("hook", "")),
        str(script_dict.get("narration", "")),
        str(script_dict.get("title", ""))
    ]).lower()
    
    if any(k in text for k in ["ejercicio", "movimiento", "sentadilla", "caminar", "entrenar"]):
        return TopicFamily.MOVEMENT
    if any(k in text for k in ["pan ", "alimentación", "comida", "desayuno", "cena", "dieta"]):
        return TopicFamily.NUTRITION
    if any(k in text for k in ["dormir", "sueño", "descanso", "insomnio"]):
        return TopicFamily.SLEEP
    if any(k in text for k in ["estrés", "carga mental", "ansiedad", "relajarse"]):
        return TopicFamily.MENTAL_LOAD

    # d) GENERAL
    logging.getLogger(__name__).warning("Could not determine TopicFamily; defaulting to GENERAL.")
    return TopicFamily.GENERAL


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

