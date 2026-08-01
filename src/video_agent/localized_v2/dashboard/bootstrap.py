from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from video_agent.localized_v2.brand_assets import BrandClip, probe_brand_clip
from video_agent.localized_v2.channel_registry import ChannelRegistry
from video_agent.localized_v2.config import ContractValidationError
from video_agent.localized_v2.dashboard.service import EnabledChannel
from video_agent.localized_v2.preflight import CapabilityInventory, run_preflight
from video_agent.localized_v2.registry import LocaleRegistry
from video_agent.localized_v2.runtime import RuntimeSettings

CAPABILITY_SCHEMA_VERSION = "localized-capabilities-v2/v1"
CAPABILITY_FIELDS = frozenset({"schemaVersion", "voices", "fonts", "brandClips"})
VOICE_FIELDS = frozenset({"provider", "language", "voiceId"})

ClipProbe = Callable[[Path, Path], BrandClip | Path]


def _manifest_error(message: str, *, path: Path) -> ContractValidationError:
    return ContractValidationError(
        "INVALID_CAPABILITY_MANIFEST",
        message,
        details={"path": str(path)},
    )


def _read_capability_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ContractValidationError(
            "CAPABILITY_MANIFEST_MISSING",
            "enabled localized V2 channels require a capability manifest",
            details={"path": str(path)},
        )
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise _manifest_error("cannot parse localized V2 capability manifest", path=path) from exc
    if not isinstance(payload, dict) or set(payload) != CAPABILITY_FIELDS:
        raise _manifest_error(
            "localized V2 capability manifest fields do not match the contract",
            path=path,
        )
    if payload.get("schemaVersion") != CAPABILITY_SCHEMA_VERSION:
        raise _manifest_error(
            "unsupported localized V2 capability manifest schemaVersion",
            path=path,
        )
    return payload


def _voices(payload: Any, *, path: Path) -> frozenset[tuple[str, str, str]]:
    if not isinstance(payload, list):
        raise _manifest_error("voices must be a list", path=path)
    voices: set[tuple[str, str, str]] = set()
    for item in payload:
        if not isinstance(item, dict) or set(item) != VOICE_FIELDS:
            raise _manifest_error("voice capability is invalid", path=path)
        key = tuple(str(item[field]).strip() for field in ("provider", "language", "voiceId"))
        if any(not part or len(part) > 128 for part in key) or key in voices:
            raise _manifest_error("voice capability is empty or duplicated", path=path)
        voices.add(key)
    return frozenset(voices)


def _fonts(payload: Any, *, path: Path) -> frozenset[str]:
    if not isinstance(payload, list):
        raise _manifest_error("fonts must be a list", path=path)
    fonts = [str(item).strip() for item in payload]
    if any(not item or len(item) > 128 for item in fonts) or len(fonts) != len(set(fonts)):
        raise _manifest_error("font capability is empty or duplicated", path=path)
    return frozenset(fonts)


def _brand_clips(
    payload: Any,
    *,
    path: Path,
    media_root: Path,
    clip_probe: ClipProbe,
) -> frozenset[Path]:
    if not isinstance(payload, list):
        raise _manifest_error("brandClips must be a list", path=path)
    root = media_root.resolve()
    clips: set[Path] = set()
    for item in payload:
        if not isinstance(item, str) or not item.strip():
            raise _manifest_error("brand clip capability is invalid", path=path)
        relative = Path(item)
        candidate = root / relative
        resolved = candidate.resolve()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not resolved.is_relative_to(root)
            or candidate.is_symlink()
            or not resolved.is_file()
        ):
            raise _manifest_error("brand clip path is missing or unsafe", path=path)
        try:
            probed = clip_probe(resolved, root)
        except Exception as exc:
            raise _manifest_error("brand clip media probe failed", path=path) from exc
        probed_path = probed.path if isinstance(probed, BrandClip) else Path(probed)
        verified = probed_path.resolve()
        if verified != resolved or verified in clips:
            raise _manifest_error("brand clip probe returned an invalid path", path=path)
        clips.add(verified)
    return frozenset(clips)


def load_enabled_channels(
    *,
    channel_root: Path,
    locale_root: Path,
    schema_root: Path,
    settings: RuntimeSettings,
    clip_probe: ClipProbe = probe_brand_clip,
) -> dict[str, EnabledChannel]:
    """Load only approved channels with independently qualified capabilities."""

    registry = ChannelRegistry(
        channel_root,
        schema_root,
        settings.root / "canary-evidence",
    )
    enabled = registry.enabled()
    if not enabled:
        return {}

    locale_registry = LocaleRegistry(locale_root, schema_root)
    manifest_path = settings.root / "capabilities.yaml"
    manifest = _read_capability_manifest(manifest_path)
    inventory = CapabilityInventory(
        media_root=settings.root / "media",
        voices=_voices(manifest["voices"], path=manifest_path),
        fonts=_fonts(manifest["fonts"], path=manifest_path),
        brand_clips=_brand_clips(
            manifest["brandClips"],
            path=manifest_path,
            media_root=settings.root / "media",
            clip_probe=clip_probe,
        ),
    )

    registrations: dict[str, EnabledChannel] = {}
    for channel_id, channel in enabled.items():
        locale_pack = locale_registry.resolve(channel["locale"])
        preflight = run_preflight(channel, locale_pack, inventory)
        if not preflight.ok:
            raise ContractValidationError(
                "CAPABILITY_PREFLIGHT_FAILED",
                f"localized V2 channel {channel_id} failed capability preflight",
                details={
                    "channelId": channel_id,
                    "failures": [failure.to_dict() for failure in preflight.failures],
                },
            )
        registrations[channel_id] = EnabledChannel(channel, locale_pack, inventory)
    return registrations
