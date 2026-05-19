from __future__ import annotations

from dataclasses import dataclass, field
import re
from pathlib import Path
from typing import Any

from video_agent.contracts import repo_root
from video_agent.utils.json_io import read_yaml


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def merge(self, other: "ValidationResult") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)

    def format_report(self) -> str:
        lines: list[str] = []
        if self.errors:
            lines.append("ERRORS (block promotion):")
            lines.extend(f"  - {error}" for error in self.errors)
        if self.warnings:
            lines.append("WARNINGS:")
            lines.extend(f"  - {warning}" for warning in self.warnings)
        return "\n".join(lines) if lines else "OK"


SCENE_ID_PATTERN = re.compile(r"^scene-\d{2}$")
SPANISH_SPECIFIC_CHARS = set("áéíóúñ¿¡üÁÉÍÓÚÑÜ")
ALLOWED_ASSET_REF_KEYS = {"background", "primary", "secondary", "bg", "overlay"}
FORBIDDEN_QA_VALUES = {"PASS", "PASSED", "TRUE", "OK", "APPROVED", "VERIFIED"}


def load_operator_channel_config(channel_path: Path | None, parsed: dict[str, Any]) -> dict[str, Any]:
    if channel_path is not None:
        return read_yaml(channel_path)
    channel_id = parsed.get("channel_id")
    if not channel_id:
        return {}
    inferred_path = repo_root() / "configs" / str(channel_id) / "channel.yaml"
    return read_yaml(inferred_path) if inferred_path.exists() else {}


def validate_operator_artifact(
    artifact: str,
    parsed: dict[str, Any],
    expected_job_id: str,
    channel_config: dict[str, Any] | None = None,
) -> ValidationResult:
    result = ValidationResult()
    result.merge(_validate_job_id(parsed, expected_job_id))

    if artifact == "scenes":
        result.merge(_validate_scenes(parsed))
    elif artifact == "seo":
        result.merge(_validate_seo(parsed, channel_config or {}))

    return result


def _validate_job_id(parsed: dict[str, Any], expected_job_id: str) -> ValidationResult:
    result = ValidationResult()
    actual = parsed.get("job_id")
    if not actual:
        result.errors.append("missing 'job_id' field; cannot verify artifact belongs to this job.")
    elif actual != expected_job_id:
        result.errors.append(
            f"job_id mismatch. Expected: {expected_job_id}. Got: {actual}. "
            "This is likely stale output from a previous ChatGPT tab."
        )
    return result


def _validate_scenes(parsed: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    result.merge(_detect_prefilled_qa(parsed, "scenes"))
    scenes = parsed.get("scenes")
    if not isinstance(scenes, list):
        result.errors.append("'scenes' field must be a list.")
        return result
    if not scenes:
        result.errors.append("'scenes' list is empty.")
        return result

    ids = [scene.get("id") if isinstance(scene, dict) else None for scene in scenes]
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            result.errors.append(f"Scene at index {index}: must be an object.")
            continue
        scene_id = str(scene.get("id", ""))
        expected = f"scene-{index + 1:02d}"
        if not SCENE_ID_PATTERN.match(scene_id):
            result.errors.append(
                f"Scene at index {index}: id '{scene_id}' invalid format. Expected scene-NN, e.g. scene-01."
            )
        elif scene_id != expected:
            result.errors.append(f"Scene at index {index}: expected id '{expected}', got '{scene_id}'.")
        result.merge(_validate_asset_refs(scene, scene_id or f"index {index}"))
        result.merge(_validate_visual_prompt(scene, scene_id or f"index {index}"))

    duplicates = sorted({scene_id for scene_id in ids if scene_id and ids.count(scene_id) > 1})
    if duplicates:
        result.errors.append(f"Duplicate scene IDs: {duplicates}")
    return result


def _validate_asset_refs(scene: dict[str, Any], scene_label: str) -> ValidationResult:
    result = ValidationResult()
    refs = scene.get("asset_refs")
    if refs is None:
        result.errors.append(f"Scene {scene_label}: missing asset_refs field.")
        return result
    if isinstance(refs, list):
        result.errors.append(f"Scene {scene_label}: asset_refs is a list, must be an object.")
        return result
    if not isinstance(refs, dict):
        result.errors.append(f"Scene {scene_label}: asset_refs must be an object, got {type(refs).__name__}.")
        return result
    unknown = sorted(set(refs) - ALLOWED_ASSET_REF_KEYS)
    if unknown:
        result.warnings.append(f"Scene {scene_label}: unknown asset_refs keys {unknown}.")
    for key, value in refs.items():
        if not isinstance(value, str):
            result.errors.append(f"Scene {scene_label}: asset_refs['{key}'] must be a string.")
    return result


def _validate_visual_prompt(scene: dict[str, Any], scene_label: str) -> ValidationResult:
    result = ValidationResult()
    prompt = str(scene.get("visual_prompt") or "")
    if not prompt:
        result.errors.append(f"Scene {scene_label}: missing or empty visual_prompt.")
        return result
    if any(char in SPANISH_SPECIFIC_CHARS for char in prompt):
        result.warnings.append(f"Scene {scene_label}: visual_prompt should be English for stock/image generation.")
    return result


def _detect_prefilled_qa(parsed: dict[str, Any], artifact: str) -> ValidationResult:
    result = ValidationResult()
    qa = parsed.get("qa")
    if isinstance(qa, dict):
        verdict = str(qa.get("verdict", "")).upper()
        if verdict in FORBIDDEN_QA_VALUES:
            result.errors.append(
                f"ChatGPT prefilled qa.verdict={qa.get('verdict')!r} for {artifact}. "
                "QA must come from Gemini, not ChatGPT."
            )
    return result


def _validate_seo(seo: dict[str, Any], channel_config: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    expected_language = channel_config.get("seo", {}).get("language", "es-419")
    language = seo.get("language")
    if language != expected_language:
        result.errors.append(f"language must be '{expected_language}' (Latin American Spanish), got '{language}'.")

    seo_config = channel_config.get("seo", {})
    result.merge(_validate_tags(seo.get("tags"), seo_config.get("min_tags", 5), seo_config.get("max_tags", 8)))
    result.merge(_validate_forbidden_positioning(seo, channel_config))
    return result


def _validate_tags(tags: Any, min_tags: int, max_tags: int) -> ValidationResult:
    result = ValidationResult()
    if not isinstance(tags, list):
        result.errors.append(f"tags must be a list, got {type(tags).__name__}.")
        return result
    if len(tags) < min_tags:
        result.errors.append(f"Too few tags: {len(tags)}. Aim for {min_tags}-{max_tags} focused tags.")
    if len(tags) > max_tags:
        result.errors.append(f"Too many tags: {len(tags)}. Aim for {min_tags}-{max_tags}.")

    normalized: list[str] = []
    for index, tag in enumerate(tags):
        if not isinstance(tag, str):
            result.errors.append(f"Tag at index {index} is not a string.")
            continue
        clean = tag.strip().lower()
        if not clean:
            result.errors.append(f"Tag at index {index} is empty or whitespace.")
        normalized.append(clean)
    duplicates = sorted({tag for tag in normalized if normalized.count(tag) > 1})
    if duplicates:
        result.errors.append(f"Duplicate tags: {duplicates}")

    total_length = sum(len(tag) for tag in tags if isinstance(tag, str)) + max(0, len(tags) - 1)
    if total_length > 500:
        result.errors.append(f"Total tags length {total_length} exceeds YouTube limit 500.")
    return result


def _validate_forbidden_positioning(seo: dict[str, Any], channel_config: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    forbidden = channel_config.get("positioning", {}).get("forbidden_phrases", [])
    preferred = channel_config.get("positioning", {}).get("preferred_phrases", [])
    text_parts = [
        str(seo.get("title") or ""),
        str(seo.get("description") or ""),
        " ".join(tag for tag in seo.get("tags", []) if isinstance(tag, str)),
    ]
    all_text = " ".join(text_parts).lower()
    for phrase in forbidden:
        if str(phrase).lower() in all_text:
            message = f"Forbidden positioning '{phrase}' found in SEO text."
            if preferred:
                message += f" Use instead: {', '.join(preferred)}."
            result.errors.append(message)
    return result
