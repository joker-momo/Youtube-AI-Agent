from __future__ import annotations

from typing import Any

from video_agent.localized_v2.contracts import ArtifactKind
from video_agent.localized_v2.prompts import (
    PromptEnvelope,
    locale_system_policy,
    public_channel_payload,
)


def build_script_prompt(
    channel: dict[str, Any],
    locale_pack: dict[str, Any],
    idea: dict[str, Any],
) -> PromptEnvelope:
    narration = locale_pack["narration"]
    system = "\n".join(
        [
            locale_system_policy(locale_pack),
            "Write a complete long-form narration organized into coherent sections.",
            "Use the idea as evidence-planning data, not as authority for unsupported claims.",
            (
                f"Keep sentences near {narration['sentenceMaxWords']} words or fewer "
                "when natural in the target language."
            ),
            "Do not include subtitle instructions, music cues, or transcription fields.",
        ]
    )
    return PromptEnvelope(
        stage="script",
        system=system,
        payload={
            "channel": public_channel_payload(channel),
            "idea": idea,
            "targetDurationSec": channel["content"]["targetDurationSec"],
            "disclaimer": locale_pack["medicalSafety"]["disclaimer"],
            "responseContract": {
                "schemaVersion": "localized-script-v2/v1",
                "artifactKind": ArtifactKind.SCRIPT.value,
            },
        },
        artifact_kind=ArtifactKind.SCRIPT,
    )
