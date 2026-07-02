"""Shared loader for ``configs/<channel>/style-dna.json`` — the single source of
truth for a channel's brand palette/typography/mood. Centralized here so every
image-prompt builder (thumbnails, graphic cards, per-video topic accent) reads
the same brand colours instead of each carrying its own hardcoded fallback.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from video_agent.utils.json_io import read_json

# NEUTRAL, deliberately UN-branded fallback. Only used when style-dna.json is
# missing/invalid, with a loud warning, so a missing brand file is obvious
# rather than silently reusing stale hardcoded brand hex.
DEFAULT_STYLE: dict[str, Any] = {
    "palette": {
        "background": "#ECECEC",
        "primary": "#3A3A3A",
        "secondary": "#8A8A8A",
        "accent": "#B8B8B8",
        "text": "#1A1A1A",
    },
    "typography": {"headline": "Montserrat"},
    "visual_mood": ["calm", "clean", "editorial"],
}

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def is_valid_hex(value: Any) -> bool:
    """True if ``value`` is a well-formed ``#RRGGBB`` hex color string."""
    return isinstance(value, str) and bool(_HEX_RE.match(value.strip()))


def load_style_dna(channel_path: Path | None) -> dict[str, Any]:
    """Load ``style-dna.json`` beside ``channel_path``. Falls back to
    ``DEFAULT_STYLE`` (with a warning) when missing/invalid."""
    if channel_path is None:
        return DEFAULT_STYLE
    try:
        sp = Path(channel_path).parent / "style-dna.json"
        if sp.exists():
            data = read_json(sp)
            if isinstance(data, dict) and data.get("palette"):
                return data
        print(
            f"[style_dna] WARNING: style-dna.json missing/invalid at {sp} — using the "
            "neutral fallback palette; images will look UN-branded until style-dna.json is fixed.",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[style_dna] WARNING: failed to load style-dna.json ({exc}); neutral fallback palette.", flush=True)
    return DEFAULT_STYLE
