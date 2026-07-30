from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.localized_v2.config import ContractValidationError, load_locale_pack

SUPPORTED_LOCALES = ("en-US", "fr-FR", "pt-BR", "ko-KR", "ja-JP")


class LocaleRegistry:
    def __init__(self, pack_root: Path, schema_root: Path):
        self._packs: dict[str, dict[str, Any]] = {}
        for path in sorted(pack_root.glob("*.yaml")):
            pack = load_locale_pack(path, schema_root)
            locale = pack["locale"]
            if locale not in SUPPORTED_LOCALES:
                raise ContractValidationError(
                    "UNSUPPORTED_LOCALE",
                    f"unsupported locale pack: {locale}",
                    details={"path": str(path), "locale": locale},
                )
            if path.stem != locale:
                raise ContractValidationError(
                    "LOCALE_FILENAME_MISMATCH",
                    f"locale pack {path.name} declares {locale}",
                    details={"path": str(path), "locale": locale},
                )
            if locale in self._packs:
                raise ContractValidationError(
                    "DUPLICATE_LOCALE",
                    f"duplicate locale pack: {locale}",
                    details={"locale": locale},
                )
            self._packs[locale] = pack

    def resolve(self, locale: str) -> dict[str, Any]:
        if locale not in SUPPORTED_LOCALES:
            raise KeyError(f"unsupported locale: {locale}")
        try:
            return self._packs[locale]
        except KeyError as exc:
            raise KeyError(f"missing locale pack: {locale}") from exc
