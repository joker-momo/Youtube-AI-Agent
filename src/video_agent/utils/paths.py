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


_SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_job_id(job_id: str) -> str:
    if not job_id or not _SAFE_JOB_ID_RE.match(job_id):
        raise ValueError(f"Invalid job_id: {job_id!r}")
    return job_id


def _generated_job_id(channel_id: str, topic: str, timestamp: str | None = None) -> str:
    topic_slug = slugify(topic)[:60].strip("-") or "untitled"
    channel_slug = slugify(channel_id)[:40].strip("-") or "channel"
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    return validate_job_id(f"{topic_slug}-{channel_slug}-{stamp}")


def allocate_job_dir(
    base_dir: Path,
    channel_id: str,
    topic: str,
    *,
    timestamp: str | None = None,
    explicit_job_id: str | None = None,
    max_attempts: int = 100,
) -> tuple[str, Path]:
    """Reserve a job directory atomically and return ``(job_id, job_dir)``."""
    if explicit_job_id:
        job_id = validate_job_id(explicit_job_id)
        job_dir = base_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        return job_id, job_dir

    base_job_id = _generated_job_id(channel_id, topic, timestamp=timestamp)
    for suffix in range(1, max_attempts + 1):
        if suffix == 1:
            job_id = base_job_id
        else:
            tail = f"-{suffix}"
            job_id = validate_job_id(f"{base_job_id[: 128 - len(tail)]}{tail}")
        job_dir = base_dir / job_id
        try:
            job_dir.mkdir(parents=True, exist_ok=False)
            return job_id, job_dir
        except FileExistsError:
            continue
    raise FileExistsError(f"Could not allocate unique job dir for {base_job_id!r}")


def create_job_dir(base_dir: Path, channel_id: str, topic: str, timestamp: str | None = None) -> Path:
    _job_id, job_dir = allocate_job_dir(base_dir, channel_id, topic, timestamp=timestamp)
    return job_dir
