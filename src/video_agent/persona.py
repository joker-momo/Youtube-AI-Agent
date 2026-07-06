"""Channel presenter identity for AI-generated imagery.

One presenter face across every AI-generated image that shows a person —
long-form thumbnails, long-form scene images, and the Shorts AI-image tier —
driven by the reference photo configured at ``thumbnail.persona_reference``
(brand-wide key). The photo is attached to each ChatGPT generation and the
prompt carries a conditional identity instruction, so scenes without people
(food/object/hands close-ups) are unaffected.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Appended to scene-image prompts whenever the reference photo is attached.
# Conditional on purpose: the same attachment goes to person-less shots too,
# and ChatGPT must not force the presenter into them.
PERSONA_SCENE_INSTRUCTION = (
    "\n\nPRESENTER IDENTITY (reference photo attached): if this image shows a "
    "person, the person MUST be the same woman as in the attached photo — same "
    "face, same silver-gray bob haircut, same age — recognizably her. Wardrobe, "
    "pose and setting follow the scene description. If the scene needs no person "
    "(object, food, or hands-only close-up), ignore the reference photo entirely "
    "and do NOT add a person."
)


def resolve_persona_reference(channel_config: dict[str, Any] | None = None) -> str:
    """Repo-relative path of the presenter reference photo, or ``""``.

    Reads ``channel_config["thumbnail"]["persona_reference"]``. When no config
    dict is at hand (sync image wrappers deep in the asset stack), falls back to
    loading the YAML at the ``CHANNEL_CONFIG`` env var the worker exports.
    Returns ``""`` unless the configured file actually exists, so callers can
    treat truthiness as "attach and instruct".
    """
    cfg = channel_config
    if cfg is None:
        cfg_path = os.environ.get("CHANNEL_CONFIG", "")
        if not cfg_path or not Path(cfg_path).is_file():
            return ""
        try:
            from video_agent.utils.json_io import read_yaml

            cfg = read_yaml(Path(cfg_path)) or {}
        except Exception:
            return ""
    ref = str(((cfg or {}).get("thumbnail") or {}).get("persona_reference") or "").strip()
    if not ref:
        return ""
    p = Path(ref)
    if not p.is_absolute():
        from video_agent.contracts import repo_root

        p = repo_root() / ref
    return ref if p.is_file() else ""
