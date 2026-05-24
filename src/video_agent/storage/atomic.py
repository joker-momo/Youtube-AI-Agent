from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from video_agent.storage.locks import file_lock


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write text via sibling tempfile + fsync + atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any, indent: int = 2) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=indent) + "\n"
    atomic_write_text(path, payload, encoding="utf-8")


def append_jsonl_locked(path: Path, event: dict[str, Any]) -> None:
    """Append one JSONL event while holding a sibling lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with file_lock(lock_path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
