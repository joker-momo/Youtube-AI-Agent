from __future__ import annotations

from typing import Any

from video_agent.localized_v2.contracts import ArtifactKind
from video_agent.localized_v2.prompts import (
    PromptEnvelope,
    locale_system_policy,
    public_channel_payload,
)


def build_idea_prompt(
    channel: dict[str, Any],
    locale_pack: dict[str, Any],
    topic: str,
) -> PromptEnvelope:
    system = "\n".join(
        [
            locale_system_policy(locale_pack),
            "Develop one evidence-oriented angle for the requested long-form topic.",
            "Local relevance must be practical and must not rely on stereotypes.",
            "List evidence questions that a later script stage must answer.",
        ]
    )
    return PromptEnvelope(
        stage="idea",
        system=system,
        payload={
            "channel": public_channel_payload(channel),
            "topic": topic,
            "visualGuidance": locale_pack["visuals"],
            "responseContract": {
                "schemaVersion": "localized-idea-v2/v1",
                "artifactKind": ArtifactKind.IDEA.value,
            },
        },
        artifact_kind=ArtifactKind.IDEA,
    )
