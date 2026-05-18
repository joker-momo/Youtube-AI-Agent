from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value.lower()).strip("-")
    return re.sub(r"-+", "-", slug) or "untitled"


def create_job_dir(base_dir: Path, channel_id: str, topic: str, timestamp: str | None = None) -> Path:
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    job_dir = base_dir / f"{stamp}-{channel_id}-{slugify(topic)[:48]}"
    job_dir.mkdir(parents=True, exist_ok=False)
    return job_dir
