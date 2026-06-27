"""Shorts-only asset layer (T4).

Houses the ChatGPT image-prompt builder and the visual-span asset service that
were previously embedded in ``video_agent.assets.service``. Importing from this
package is the forward-looking path; ``service.py`` still re-exports the same
names for back-compat until callers are switched.

Boundary: nothing here may import ``video_agent.stages.*``.
"""

from __future__ import annotations

from video_agent.shorts.assets.image_prompt import build_scene_image_prompt
from video_agent.shorts.assets.span_candidates import ShortSpanAssetService

__all__ = [
    "ShortSpanAssetService",
    "build_scene_image_prompt",
]
