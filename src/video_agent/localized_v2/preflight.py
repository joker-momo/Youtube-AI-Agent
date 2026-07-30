from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from video_agent.localized_v2.contracts import CapabilityFailure, PreflightResult


@dataclass(frozen=True, slots=True)
class CapabilityInventory:
    media_root: Path
    voices: frozenset[tuple[str, str, str]]
    fonts: frozenset[str]


def _failure(
    locale: str,
    capability: str,
    provider: str,
    code: str,
    remediation: str,
) -> CapabilityFailure:
    return CapabilityFailure(
        locale=locale,
        capability=capability,
        provider=provider,
        code=code,
        remediation=remediation,
    )


def _brand_clip_failure(
    locale: str,
    media_root: Path,
    relative_path: object,
    capability: str,
) -> CapabilityFailure | None:
    if not isinstance(relative_path, str) or not relative_path:
        return _failure(
            locale,
            capability,
            "filesystem",
            "MISSING_BRAND_CLIP",
            f"configure a locale-specific {capability} clip",
        )
    root = media_root.resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return _failure(
            locale,
            capability,
            "filesystem",
            "MISSING_BRAND_CLIP",
            f"provide {relative_path} inside the V2 media root",
        )
    return None


def run_preflight(
    channel: dict,
    locale_pack: dict,
    inventory: CapabilityInventory,
) -> PreflightResult:
    locale = str(channel.get("locale", "unknown"))
    failures: list[CapabilityFailure] = []
    if locale_pack.get("locale") != locale:
        failures.append(
            _failure(
                locale,
                "localePack",
                "localized-v2",
                "LOCALE_MISMATCH",
                "select the locale pack matching the channel locale",
            )
        )

    voice = channel.get("voice") or {}
    voice_key = (
        str(voice.get("provider", "")),
        str(voice.get("language", "")),
        str(voice.get("voiceId", "")),
    )
    if voice_key not in inventory.voices:
        failures.append(
            _failure(
                locale,
                "voice",
                voice_key[0] or "unconfigured",
                "VOICE_UNAVAILABLE",
                "qualify and register the exact local provider/language/voice ID",
            )
        )

    fonts = locale_pack.get("fonts") or {}
    required_fonts = fonts.get("families") or []
    missing_fonts = [font for font in required_fonts if font not in inventory.fonts]
    if not required_fonts or missing_fonts:
        failures.append(
            _failure(
                locale,
                "font",
                "filesystem",
                "FONT_UNAVAILABLE",
                f"install and register required fonts: {missing_fonts or 'none configured'}",
            )
        )

    safety = locale_pack.get("medicalSafety") or {}
    if (
        not safety.get("softClaims")
        or not safety.get("prohibitedClaims")
        or not safety.get("disclaimer")
    ):
        failures.append(
            _failure(
                locale,
                "medicalSafety",
                "locale-pack",
                "SAFETY_PACK_INCOMPLETE",
                "provide localized soft-claim wording and disclaimer text",
            )
        )

    metrics = locale_pack.get("textMetrics") or {}
    if not metrics.get("charsPerWord") or not metrics.get("expansionRatio"):
        failures.append(
            _failure(
                locale,
                "textMetrics",
                "locale-pack",
                "TEXT_METRICS_INCOMPLETE",
                "provide positive chars-per-word and expansion-ratio metrics",
            )
        )

    brand = channel.get("brand") or {}
    for field, capability in (
        ("introClip", "intro"),
        ("disclaimerClip", "disclaimer"),
        ("outroClip", "outro"),
    ):
        failure = _brand_clip_failure(
            locale,
            inventory.media_root,
            brand.get(field),
            capability,
        )
        if failure:
            failures.append(failure)

    render = channel.get("render") or {}
    if render.get("concurrency") != "auto":
        failures.append(
            _failure(
                locale,
                "render",
                "remotion",
                "INVALID_RENDER_CONCURRENCY",
                'set render.concurrency to "auto"',
            )
        )
    if (render.get("subtitles") or {}).get("enabled") is not False:
        failures.append(
            _failure(
                locale,
                "render",
                "remotion",
                "SUBTITLES_NOT_ALLOWED",
                "disable V2 subtitles; narration is voice-only",
            )
        )
    return PreflightResult(tuple(failures))
