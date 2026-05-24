from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from video_agent.storage.atomic import atomic_write_json


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    """Atomically write JSON to ``path``.

    Writes to a sibling tempfile then renames via ``os.replace`` so a crash
    mid-write cannot leave callers reading a truncated file.
    """
    atomic_write_json(path, data)


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))
