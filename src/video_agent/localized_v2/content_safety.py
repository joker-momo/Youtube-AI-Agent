from __future__ import annotations

import re
import unicodedata
from typing import Any

from video_agent.localized_v2.contracts import ArtifactKind

LEGACY_MARKERS = (
    "vida plena",
    "uckuswqsaalsekcsgztukamw",
    "escribe en español",
    "redacta en español",
    "suscríbete a vida plena",
)


class LocalizedContentError(ValueError):
    def __init__(self, code: str, field: str, message: str):
        super().__init__(message)
        self.code = code
        self.field = field

    def to_failure(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "stage": "content-validation",
            "artifact": self.field,
            "message": str(self),
            "retryable": False,
        }


def _canonical(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _audience_fields(
    kind: ArtifactKind,
    payload: dict[str, Any],
) -> list[tuple[str, str]]:
    if kind is ArtifactKind.IDEA:
        fields = ["angle", "audiencePromise", "localRelevance"]
        return [(field, str(payload[field])) for field in fields]
    if kind is ArtifactKind.SCRIPT:
        values = [("title", str(payload["title"]))]
        values.extend(
            (f"sections.{index}.narration", str(section["narration"]))
            for index, section in enumerate(payload["sections"])
        )
        return values
    if kind is ArtifactKind.SCENES:
        return [
            (f"scenes.{index}.narration", str(scene["narration"]))
            for index, scene in enumerate(payload["scenes"])
        ]
    if kind is ArtifactKind.SEO:
        values = [
            ("title", str(payload["title"])),
            ("description", str(payload["description"])),
            ("thumbnailText", str(payload["thumbnailText"])),
            ("pinnedComment", str(payload["pinnedComment"])),
        ]
        values.extend(
            (f"tags.{index}", str(tag)) for index, tag in enumerate(payload["tags"])
        )
        return values
    return []


def _all_text(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        output: list[tuple[str, str]] = []
        for key, child in value.items():
            output.extend(_all_text(child, f"{path}.{key}"))
        return output
    if isinstance(value, list):
        output = []
        for index, child in enumerate(value):
            output.extend(_all_text(child, f"{path}[{index}]"))
        return output
    return [(path, value)] if isinstance(value, str) else []


def validate_localized_content(
    kind: ArtifactKind,
    payload: dict[str, Any],
    locale_pack: dict[str, Any],
) -> None:
    expected_locale = locale_pack["locale"]
    if payload.get("locale") != expected_locale:
        raise LocalizedContentError(
            "LOCALE_MISMATCH",
            "locale",
            f"artifact locale must be {expected_locale}",
        )
    for field, value in _all_text(payload):
        canonical = _canonical(value)
        marker = next(
            (candidate for candidate in LEGACY_MARKERS if candidate in canonical),
            None,
        )
        if marker:
            raise LocalizedContentError(
                "LEGACY_LANGUAGE_LEAKAGE",
                field,
                f"legacy channel marker detected in {field}",
            )
    audience_fields = _audience_fields(kind, payload)
    for field, value in audience_fields:
        canonical = _canonical(value)
        prohibited = next(
            (
                phrase
                for phrase in locale_pack["medicalSafety"]["prohibitedClaims"]
                if _canonical(str(phrase)) in canonical
            ),
            None,
        )
        if prohibited:
            raise LocalizedContentError(
                "MEDICAL_OVERCLAIM",
                field,
                f"prohibited medical wording detected in {field}",
            )
    if kind in {ArtifactKind.SCRIPT, ArtifactKind.SCENES}:
        collection = payload["sections"] if kind is ArtifactKind.SCRIPT else payload["scenes"]
        identifiers = [str(item["id"]) for item in collection]
        if len(identifiers) != len(set(identifiers)):
            raise LocalizedContentError(
                "DUPLICATE_ARTIFACT_ID",
                "sections" if kind is ArtifactKind.SCRIPT else "scenes",
                "localized artifacts require unique section and scene IDs",
            )
    if kind is ArtifactKind.SCRIPT:
        narration = " ".join(value for _field, value in audience_fields)
        canonical_narration = _canonical(narration)
        if not any(
            _canonical(str(phrase)) in canonical_narration
            for phrase in locale_pack["medicalSafety"]["softClaims"]
        ):
            raise LocalizedContentError(
                "SOFT_CLAIM_REQUIRED",
                "sections",
                "script must include locale-specific informational medical wording",
            )
    if kind is ArtifactKind.SCENES:
        if payload["scenes"][0]["visualType"] != "graphic":
            raise LocalizedContentError(
                "GRAPHIC_HOOK_REQUIRED",
                "scenes.0.visualType",
                "the first scene must be a graphic visual hook",
            )
        for index, scene in enumerate(payload["scenes"]):
            brief = scene["searchBrief"]
            if brief["language"] != "en":
                raise LocalizedContentError(
                    "SEARCH_LANGUAGE_INVALID",
                    f"scenes.{index}.searchBrief.language",
                    "stock search briefs must use English",
                )
