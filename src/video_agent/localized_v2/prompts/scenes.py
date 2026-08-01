from __future__ import annotations

from typing import Any

from video_agent.localized_v2.contracts import ArtifactKind
from video_agent.localized_v2.prompts import (
    PromptEnvelope,
    locale_system_policy,
    public_channel_payload,
)


def build_scenes_prompt(
    channel: dict[str, Any],
    locale_pack: dict[str, Any],
    script: dict[str, Any],
) -> PromptEnvelope:
    system = "\n".join(
        [
            locale_system_policy(locale_pack),
            "Split the narration into a clear visual sequence without changing its meaning.",
            "Keep narration in the target language.",
            "Write each searchBrief query in concise English for stock-media providers.",
            "Every searchBrief query must describe a real, filmable video background, even when visualType is graphic.",
            "Visual prompts must be topic-faithful and avoid the locale pack's exclusions.",
            "Each visualPrompt must be a positive visible scene description only.",
            "The first scene must use visualType graphic for a strong visual hook.",
            "Use visualType video for later scenes when real footage best supports the narration.",
            "Production controls are enforced downstream and must not appear in output fields.",
        ]
    )
    return PromptEnvelope(
        stage="scenes",
        system=system,
        payload={
            "channel": public_channel_payload(channel),
            "script": script,
            "visualGuidance": locale_pack["visuals"],
            "responseContract": {
                "schemaVersion": "localized-scenes-v2/v1",
                "artifactKind": ArtifactKind.SCENES.value,
                "searchBriefLanguage": "en",
            },
        },
        artifact_kind=ArtifactKind.SCENES,
    )
