from __future__ import annotations

from typing import Any

from video_agent.localized_v2.contracts import ArtifactKind
from video_agent.localized_v2.prompts import (
    PromptEnvelope,
    locale_system_policy,
    public_channel_payload,
)


def build_seo_prompt(
    channel: dict[str, Any],
    locale_pack: dict[str, Any],
    script: dict[str, Any],
) -> PromptEnvelope:
    system = "\n".join(
        [
            locale_system_policy(locale_pack),
            "Create locale-native YouTube metadata from the script.",
            "Use the locale SEO title, keyword, thumbnail, and pinned-comment rules.",
            "Do not invent a subscription URL or copy branding from another channel.",
        ]
    )
    return PromptEnvelope(
        stage="seo",
        system=system,
        payload={
            "channel": public_channel_payload(channel),
            "script": script,
            "seoRules": locale_pack["seo"],
            "responseContract": {
                "schemaVersion": "localized-seo-v2/v1",
                "artifactKind": ArtifactKind.SEO.value,
            },
        },
        artifact_kind=ArtifactKind.SEO,
    )
