"""Channel presenter identity for AI-generated imagery.

One presenter identity across every AI-generated image that shows a person —
long-form thumbnails, long-form scene images, and the Shorts AI-image tier.
Identity is guided by a TEXT description (no reference photo is attached to the
ChatGPT generation): the attachment path was dropped because it was unreliable
(echoed the reference photo verbatim, upload-registration failures). The prompt
carries a conditional identity instruction, so scenes without people
(food/object/hands close-ups) are unaffected.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Text description of the recurring presenter, appended to scene-image prompts
# when a presenter is configured. No photo is attached — identity is described
# in words. Conditional on purpose: person-less shots must NOT get a person.
PERSONA_SCENE_INSTRUCTION = (
    "\n\nPRESENTER IDENTITY: if this image shows a person, depict the channel's "
    "recurring presenter — a natural-looking mature Mediterranean Spanish woman "
    "(around 55-65) with a silver-gray bob haircut, warm and trustworthy, kept "
    "recognizably consistent across images. Wardrobe, pose and setting follow the "
    "scene description. If the scene needs no person (object, food, or hands-only "
    "close-up), do NOT add a person."
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
