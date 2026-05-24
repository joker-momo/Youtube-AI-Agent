from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from video_agent.storage.atomic import append_jsonl_locked


@dataclass
class EventLogger:
    path: Path

    def log(self, event: str, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "data": data,
        }
        append_jsonl_locked(self.path, payload)
