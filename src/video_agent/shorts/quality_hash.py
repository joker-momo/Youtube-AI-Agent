from __future__ import annotations

import hashlib
import json
from typing import Any

from video_agent.shorts.quality_config import quality_fingerprint


def stable_hash(*parts: Any, channel_config: dict[str, Any] | None = None) -> str:
    payload = {
        "parts": parts,
        "fingerprint": quality_fingerprint(channel_config),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
