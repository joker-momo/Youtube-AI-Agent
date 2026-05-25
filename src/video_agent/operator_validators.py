from __future__ import annotations

from collections import Counter
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
ALLOWED_LAYOUTS = {"hook", "subtitle", "checklist", "warning", "quote", "cta"}
QA_REWORKABLE_LANGUAGE_VALUES = {"es-419", "es-LATAM"}


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
        result.merge(_validate_scene_layout(scene, scene_id or f"index {index}"))

    duplicates = sorted({sid for sid, n in Counter(s for s in ids if s).items() if n > 1})
    if duplicates:
        result.errors.append(f"Duplicate scene IDs: {duplicates}")
    return result


def _validate_scene_layout(scene: dict[str, Any], scene_label: str) -> ValidationResult:
    result = ValidationResult()
    layout = str(scene.get("layout") or "subtitle").lower()
    if layout not in ALLOWED_LAYOUTS:
        result.errors.append(
            f"Scene {scene_label}: invalid layout {layout!r}. Allowed: {sorted(ALLOWED_LAYOUTS)}"
        )
    payload = scene.get("layout_payload")
    if payload is not None and not isinstance(payload, dict):
        result.errors.append(f"Scene {scene_label}: layout_payload must be an object.")
        return result
    if layout == "checklist":
        bullets = (payload or {}).get("bullets") if isinstance(payload, dict) else None
        valid = [b for b in bullets or [] if str(b).strip()] if isinstance(bullets, list) else []
        if not 2 <= len(valid) <= 4:
            result.warnings.append(f"Scene {scene_label}: checklist layout should have 2-4 bullets.")
    if layout == "cta":
        cta = (payload or {}).get("cta") if isinstance(payload, dict) else ""
        if not str(cta or "").strip():
            result.warnings.append(f"Scene {scene_label}: CTA layout should include layout_payload.cta.")
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
    expected_language = (
        channel_config.get("seo", {}).get("language")
        or channel_config.get("audience", {}).get("language")
        or "es-ES"
    )
    language = seo.get("language")
    if language != expected_language:
        if str(language) in QA_REWORKABLE_LANGUAGE_VALUES:
            result.warnings.append(
                f"language should be '{expected_language}' from channel_config.seo.language, got '{language}'. "
                "Allowing promotion so Claude QA can force ChatGPT rework."
            )
        else:
            result.errors.append(
                f"language must be '{expected_language}' from channel_config.seo.language, got '{language}'."
            )

    seo_config = channel_config.get("seo", {})
    result.merge(_validate_tags(seo.get("tags"), seo_config.get("min_tags", 5), seo_config.get("max_tags", 8)))
    result.merge(_validate_forbidden_positioning(seo, channel_config))
    result.merge(_validate_locale_style(seo, channel_config))
    return result


_PLACEHOLDER_SOCIAL_MARKERS = (
    "redes adicionales",
    "no proporcionadas",
    "no proporcionados",
    "not provided",
    "sin enlaces",
    "social links not provided",
)


def _validate_locale_style(seo: dict[str, Any], channel_config: dict[str, Any]) -> ValidationResult:
    """Block placeholder missing-resource text and warn about locale lexical mismatches."""
    result = ValidationResult()
    locale_style = channel_config.get("locale_style") or {}
    lexical = locale_style.get("lexical_preferences") or {}
    avoid_terms = [str(term).strip().lower() for term in (lexical.get("avoid") or []) if str(term).strip()]
    expected_language = (
        channel_config.get("seo", {}).get("language")
        or channel_config.get("audience", {}).get("language")
        or "es-ES"
    )

    scan_fields: list[tuple[str, str]] = []
    for field in ("title", "description", "thumbnail_text", "suggested_pinned_comments"):
        value = seo.get(field)
        if isinstance(value, str) and value:
            scan_fields.append((field, value))
    tags = seo.get("tags")
    if isinstance(tags, list):
        joined_tags = " ".join(t for t in tags if isinstance(t, str))
        if joined_tags:
            scan_fields.append(("tags", joined_tags))

    # Block placeholder social-link text outright.
    for field, value in scan_fields:
        lowered = value.lower()
        for marker in _PLACEHOLDER_SOCIAL_MARKERS:
            if marker in lowered:
                result.errors.append(
                    f"SEO {field} contains placeholder social-link text ({marker!r}). "
                    "Remove missing social links instead of mentioning them."
                )
                break

    # Lexical warnings only when configured language is es-ES (Spain-first) — keeps it gentle for other channels.
    if expected_language == "es-ES" and avoid_terms:
        for field, value in scan_fields:
            lowered = value.lower()
            hits = [term for term in avoid_terms if term in lowered]
            for term in hits:
                # Forbidden positioning is reported as an error elsewhere; downgrade to warning here to avoid double-counting.
                result.warnings.append(
                    f"SEO {field} uses '{term}', which is on the locale avoid list for {expected_language}."
                )
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
    duplicates = sorted({tag for tag, n in Counter(normalized).items() if n > 1})
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
