from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

CONTRACT_VERSION = "localized-v2/v1"


class ArtifactKind(StrEnum):
    SCRIPT = "script"
    SCENES = "scenes"
    SEO = "seo"
    AUDIO_TIMING = "audio-timing"
    ASSET_MANIFEST = "asset-manifest"
    RENDER_PROPS = "render-props"


@dataclass(frozen=True, slots=True)
class CapabilityFailure:
    locale: str
    capability: str
    provider: str
    code: str
    remediation: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PreflightResult:
    failures: tuple[CapabilityFailure, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "failures": [failure.to_dict() for failure in self.failures],
        }
