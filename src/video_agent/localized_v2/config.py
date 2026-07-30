from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from video_agent.localized_v2.contracts import ArtifactKind

ARTIFACT_SCHEMA_FILES = {
    ArtifactKind.SCRIPT: "localized-v2/script-v1.schema.json",
    ArtifactKind.SCENES: "localized-v2/scenes-v1.schema.json",
    ArtifactKind.SEO: "localized-v2/seo-v1.schema.json",
    ArtifactKind.AUDIO_TIMING: "localized-v2/audio-timing-v1.schema.json",
    ArtifactKind.ASSET_MANIFEST: "localized-v2/asset-manifest-v1.schema.json",
    ArtifactKind.RENDER_PROPS: "localized-v2/render-props-v1.schema.json",
}


class ContractValidationError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _read_document(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw) if path.suffix == ".json" else yaml.safe_load(raw)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ContractValidationError(
            "INVALID_DOCUMENT",
            f"cannot parse {path}: {exc}",
            details={"path": str(path)},
        ) from exc
    if not isinstance(payload, dict):
        raise ContractValidationError(
            "INVALID_DOCUMENT",
            f"{path} must contain an object",
            details={"path": str(path)},
        )
    return payload


def _validate(
    payload: dict[str, Any],
    schema_path: Path,
    *,
    code: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema = _read_document(schema_path)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise ContractValidationError(
            code,
            f"{location}: {error.message}",
            details=details,
        )
    return payload


def load_channel_config(path: Path, schema_root: Path) -> dict[str, Any]:
    return _validate(
        _read_document(path),
        schema_root / "localized-channel-v2.schema.json",
        code="INVALID_CHANNEL_CONFIG",
        details={"path": str(path)},
    )


def load_locale_pack(path: Path, schema_root: Path) -> dict[str, Any]:
    return _validate(
        _read_document(path),
        schema_root / "locale-pack-v2.schema.json",
        code="INVALID_LOCALE_PACK",
        details={"path": str(path)},
    )


def validate_artifact(
    payload: dict[str, Any],
    kind: ArtifactKind,
    schema_root: Path,
) -> dict[str, Any]:
    return _validate(
        payload,
        schema_root / ARTIFACT_SCHEMA_FILES[kind],
        code="INVALID_ARTIFACT",
        details={"artifactKind": kind.value},
    )
