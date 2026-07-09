"""Per-job audit log of every ChatGPT image-generation prompt.

Records the exact, complete prompt string sent to ChatGPT image generation for
all long-form image paths — thumbnail (``auto_thumbnail_image_stage``),
generated scene assets (``generate_scene_asset``), and graphic/card images
(``run_graphic_images_stage``) — so a human can inspect and compare every prompt
after a run.

Storage layout, per job::

    job_dir/operator/chatgpt/image_prompts/
        index.jsonl                     # one JSON record per prompt, append-only
        0001-graphic_images-graphic-scene-07-<hash>.md   # full prompt, verbatim

The prompt is stored **untruncated** in both the JSONL record (``prompt`` field,
JSON-escaped and therefore lossless) and the human-readable ``.md`` file. Appends
are serialised with a sibling file lock and the sequence number is derived from
the current line count under that lock, so a previously recorded prompt is never
lost even when several long-form stages generate images concurrently
(``run_all_pipeline`` fans them out with ``asyncio.gather``).

This module only *observes*: it never mutates the prompt text sent to ChatGPT.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from video_agent.storage.atomic import atomic_write_bytes
from video_agent.storage.locks import file_lock

__all__ = [
    "IMAGE_PROMPT_LOG_DIR",
    "INDEX_NAME",
    "log_image_prompt",
    "safe_log_image_prompt",
    "read_image_prompt_index",
]

# Job-relative directory holding the image-prompt audit trail.
IMAGE_PROMPT_LOG_DIR = Path("operator") / "chatgpt" / "image_prompts"
INDEX_NAME = "index.jsonl"

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(text: str, *, max_len: int = 48) -> str:
    """Filesystem-safe, lower-cased slug for a filename component."""
    cleaned = _SLUG_RE.sub("-", str(text or "")).strip("-.").lower()
    return cleaned[:max_len] or "x"


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_image_prompt(
    job_dir: Path | str,
    *,
    stage: str,
    kind: str,
    prompt: str,
    scene_id: str | None = None,
    index: int | None = None,
    strategy: str | None = None,
    out_path: str | None = None,
    project_name: str | None = None,
    aspect_ratio: str = "16:9",
    model: str = "chatgpt-image",
) -> Path:
    """Append one exact ChatGPT image prompt to the job's audit log.

    Returns the path of the per-prompt ``.md`` artifact. The append is atomic
    with respect to concurrent long-form stages: sequence numbering and the
    JSONL append both happen while holding the index lock.

    Metadata captured: ``stage`` (pipeline stage), ``kind``
    (``thumbnail``/``scene_asset``/``graphic``), ``scene_id`` and/or ``index``
    (thumbnail variant), ``strategy`` (thumbnail visual strategy), ``out_path``
    (destination asset), ``project_name``, ``aspect_ratio``, a ``prompt_sha256``
    and character count, and a UTC ``created_at`` — plus the full, untruncated
    ``prompt``.
    """
    log_dir = Path(job_dir) / IMAGE_PROMPT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    index_path = log_dir / INDEX_NAME
    lock_path = index_path.with_name(f".{INDEX_NAME}.lock")

    prompt_text = str(prompt)
    prompt_sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    created_at = _utc_now_iso()
    discriminator = scene_id if scene_id is not None else index

    with file_lock(lock_path):
        seq = _count_lines(index_path) + 1

        name_parts = [f"{seq:04d}", _slug(stage), _slug(kind)]
        if discriminator is not None:
            name_parts.append(_slug(str(discriminator)))
        name_parts.append(prompt_sha[:8])
        prompt_file = log_dir / ("-".join(name_parts) + ".md")

        record: dict[str, Any] = {
            "seq": seq,
            "created_at": created_at,
            "stage": stage,
            "kind": kind,
            "scene_id": scene_id,
            "index": index,
            "strategy": strategy,
            "out_path": out_path,
            "project_name": project_name,
            "aspect_ratio": aspect_ratio,
            "model": model,
            "prompt_sha256": prompt_sha,
            "prompt_chars": len(prompt_text),
            "prompt_file": prompt_file.name,
            "prompt": prompt_text,
        }

        atomic_write_bytes(prompt_file, _render_markdown(record).encode("utf-8"))

        line = json.dumps(record, ensure_ascii=False) + "\n"
        with index_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    return prompt_file


def safe_log_image_prompt(job_dir: Path | str, **kwargs: Any) -> Path | None:
    """``log_image_prompt`` wrapper that never raises into the generation path.

    Observability must never break image generation, so a logging failure is
    swallowed with a warning (a missing audit record is preferable to a failed
    render). Returns the artifact path, or ``None`` if logging failed.
    """
    try:
        return log_image_prompt(job_dir, **kwargs)
    except Exception as exc:  # pragma: no cover - logging must never break gen
        print(f"[image-prompt-log] failed to record prompt: {exc}", flush=True)
        return None


def _render_markdown(record: dict[str, Any]) -> str:
    """Human-readable per-prompt artifact holding the full prompt verbatim."""
    meta_keys = (
        "seq",
        "created_at",
        "stage",
        "kind",
        "scene_id",
        "index",
        "strategy",
        "out_path",
        "project_name",
        "aspect_ratio",
        "model",
        "prompt_sha256",
        "prompt_chars",
    )
    lines = [f"# ChatGPT image prompt #{record['seq']:04d}", ""]
    for key in meta_keys:
        value = record.get(key)
        if value is None:
            continue
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## Exact prompt sent to ChatGPT")
    lines.append("")
    # Verbatim block — not fenced, because prompts can themselves contain code
    # fences; a plain section preserves every character without escaping games.
    lines.append(record["prompt"])
    lines.append("")
    return "\n".join(lines)


def read_image_prompt_index(job_dir: Path | str) -> list[dict[str, Any]]:
    """Read the append-only index back into a list of records (empty if none)."""
    index_path = Path(job_dir) / IMAGE_PROMPT_LOG_DIR / INDEX_NAME
    if not index_path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records
