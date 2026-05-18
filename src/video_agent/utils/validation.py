from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from video_agent.utils.json_io import read_json


def validate_json(data: Any, schema_path: Path) -> None:
    schema = read_json(schema_path)
    Draft202012Validator(schema).validate(data)
