from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.localized_v2.config import (
    ContractValidationError,
    load_channel_config,
)
from video_agent.localized_v2.registry import SUPPORTED_LOCALES

ROLLOUT_ORDER = {locale: index for index, locale in enumerate(SUPPORTED_LOCALES, 1)}


class ChannelRegistry:
    """Load only localized V2 channels that passed the ordered canary gate."""

    def __init__(self, channel_root: Path, schema_root: Path, evidence_root: Path):
        self._channels: dict[str, dict[str, Any]] = {}
        self._by_locale: dict[str, dict[str, Any]] = {}
        self._evidence_root = evidence_root.resolve()

        for path in sorted(channel_root.glob("*/channel.yaml")):
            channel = load_channel_config(path, schema_root)
            self._register(channel, path)

        missing = set(SUPPORTED_LOCALES) - set(self._by_locale)
        if missing:
            raise ContractValidationError(
                "INCOMPLETE_CHANNEL_MATRIX",
                f"missing channel templates: {', '.join(sorted(missing))}",
                details={"missing": sorted(missing)},
            )
        self._validate_rollout_dependencies()

    def _register(self, channel: dict[str, Any], path: Path) -> None:
        channel_id = channel["channelId"]
        locale = channel["locale"]
        expected_order = ROLLOUT_ORDER[locale]
        if channel["rolloutOrder"] != expected_order:
            raise ContractValidationError(
                "INVALID_ROLLOUT_ORDER",
                f"{locale} must use rollout order {expected_order}",
                details={"path": str(path), "locale": locale},
            )
        if channel_id in self._channels:
            raise ContractValidationError(
                "DUPLICATE_CHANNEL",
                f"duplicate channel id: {channel_id}",
                details={"path": str(path), "channelId": channel_id},
            )
        if locale in self._by_locale:
            raise ContractValidationError(
                "DUPLICATE_CHANNEL_LOCALE",
                f"duplicate channel locale: {locale}",
                details={"path": str(path), "locale": locale},
            )
        if channel["enabled"]:
            self._validate_evidence(channel, path)
        self._channels[channel_id] = channel
        self._by_locale[locale] = channel

    def _validate_evidence(self, channel: dict[str, Any], path: Path) -> None:
        for raw_path in channel["canary"]["evidence"]:
            relative = Path(raw_path)
            candidate = self._evidence_root / relative
            resolved = candidate.resolve()
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not resolved.is_relative_to(self._evidence_root)
                or candidate.is_symlink()
                or not resolved.is_file()
            ):
                raise ContractValidationError(
                    "INVALID_CANARY_EVIDENCE",
                    f"missing or unsafe canary evidence: {raw_path}",
                    details={"path": str(path), "evidence": raw_path},
                )

    def _validate_rollout_dependencies(self) -> None:
        qualification_locales = [
            locale
            for locale in SUPPORTED_LOCALES
            if self._by_locale[locale].get("qualification") is True
        ]
        if len(qualification_locales) > 1:
            raise ContractValidationError(
                "MULTIPLE_QUALIFICATION_CHANNELS",
                "only one localized V2 channel may be under qualification",
                details={"locales": qualification_locales},
            )
        earlier_enabled = True
        for locale in SUPPORTED_LOCALES:
            channel = self._by_locale[locale]
            active = channel["enabled"] or channel.get("qualification") is True
            if active and not earlier_enabled:
                raise ContractValidationError(
                    "ROLLOUT_DEPENDENCY_FAILED",
                    f"{locale} cannot be enabled or qualified before all earlier locales",
                    details={"locale": locale},
                )
            earlier_enabled = earlier_enabled and channel["enabled"]

    def enabled(self) -> dict[str, dict[str, Any]]:
        return {
            channel_id: channel
            for channel_id, channel in self._channels.items()
            if channel["enabled"]
        }

    def runnable(self) -> dict[str, dict[str, Any]]:
        """Return production channels plus explicitly isolated canary channels."""

        return {
            channel_id: channel
            for channel_id, channel in self._channels.items()
            if channel["enabled"] or channel.get("qualification") is True
        }

    def all(self) -> dict[str, dict[str, Any]]:
        return dict(self._channels)
