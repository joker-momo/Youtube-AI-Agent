from __future__ import annotations

from typing import Any

from video_agent.localized_v2.contracts import ArtifactKind
from video_agent.localized_v2.prompts import PromptEnvelope, locale_system_policy


def build_qa_prompt(
    locale_pack: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
) -> PromptEnvelope:
    system = "\n".join(
        [
            locale_system_policy(locale_pack),
            "Audit the candidate artifacts without rewriting them.",
            "Fail locale leakage, medical overclaims, unsupported evidence, stereotypes,",
            "non-English stock-search queries, subtitle instructions, or schema inconsistency.",
            (
                "No external source packet was provided. A missing citation alone is not a QA "
                "failure for a conservative, high-level, clearly hedged health statement."
            ),
            (
                "Fail any invented specific study, statistic, effect size, or evidence ranking, "
                "and fail any unhedged medical recommendation."
            ),
            "A PASS verdict must have an empty failures array.",
        ]
    )
    return PromptEnvelope(
        stage="qa",
        system=system,
        payload={
            "artifacts": artifacts,
            "responseContract": {
                "schemaVersion": "localized-qa-v2/v1",
                "artifactKind": ArtifactKind.QA.value,
            },
        },
        artifact_kind=ArtifactKind.QA,
    )
