from __future__ import annotations

from typing import Any

from video_agent.localized_v2.contracts import ArtifactKind
from video_agent.localized_v2.prompts import (
    PromptEnvelope,
    locale_system_policy,
    public_channel_payload,
)
from video_agent.localized_v2.text_metrics import budget_for_duration


def build_script_prompt(
    channel: dict[str, Any],
    locale_pack: dict[str, Any],
    idea: dict[str, Any],
    source_context: dict[str, Any] | None = None,
) -> PromptEnvelope:
    narration = locale_pack["narration"]
    budget = budget_for_duration(
        int(channel["content"]["targetDurationSec"]),
        locale_pack,
    )
    system = "\n".join(
        [
            locale_system_policy(locale_pack),
            "Write a complete long-form narration organized into coherent sections.",
            (
                "Treat sourceContext as the canonical operator intent. Preserve its "
                "subject, audience, angle, concerns, and boundaries throughout the script."
            ),
            "Use the idea as evidence-planning data, not as authority for unsupported claims.",
            (
                "No source packet is provided. Do not invent citations, named studies, trial "
                "designs, statistics, effect sizes, mechanisms, or evidence rankings."
            ),
            (
                "Use conservative high-level health information with the locale's soft-claim "
                "wording and clearly direct individual decisions to a healthcare professional."
            ),
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
            "sourceContext": source_context,
            "idea": idea,
            "targetDurationSec": channel["content"]["targetDurationSec"],
            "textBudget": {
                "strategy": budget.strategy,
                "targetUnits": budget.target_units,
            },
            "disclaimer": locale_pack["medicalSafety"]["disclaimer"],
            "evidencePolicy": {
                "sourcePacketProvided": False,
                "allowSpecificStudiesStatisticsOrEffectSizes": False,
            },
            "responseContract": {
                "schemaVersion": "localized-script-v2/v1",
                "artifactKind": ArtifactKind.SCRIPT.value,
            },
        },
        artifact_kind=ArtifactKind.SCRIPT,
    )
