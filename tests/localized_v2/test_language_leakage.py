from __future__ import annotations

from pathlib import Path

import pytest

from video_agent.localized_v2.content_safety import (
    LocalizedContentError,
    validate_localized_content,
)
from video_agent.localized_v2.contracts import ArtifactKind
from video_agent.localized_v2.prompts.scenes import build_scenes_prompt
from video_agent.localized_v2.providers import (
    MAX_STRUCTURED_RESPONSE_BYTES,
    ProviderBoundaryError,
    validate_structured_response,
)
from video_agent.localized_v2.registry import SUPPORTED_LOCALES

from .locale_fixtures import LOCALE_DATA, snapshots

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
def test_locale_native_script_passes_deterministic_leakage_gate(locale: str) -> None:
    _channel, locale_pack = snapshots(locale)
    payload = {
        "schemaVersion": "localized-script-v2/v1",
        "locale": locale,
        "title": LOCALE_DATA[locale]["prefer"],
        "sections": [
            {
                "id": "opening",
                "narration": f"{LOCALE_DATA[locale]['soft']} {LOCALE_DATA[locale]['prefer']}",
            }
        ],
    }

    validate_localized_content(ArtifactKind.SCRIPT, payload, locale_pack)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Vida Plena 45+ copied material", "LEGACY_LANGUAGE_LEAKAGE"),
        (
            "https://www.youtube.com/channel/UCKUswqsAaLsEkcsgzTuKAmw",
            "LEGACY_LANGUAGE_LEAKAGE",
        ),
        ("This habit cures disease.", "MEDICAL_OVERCLAIM"),
    ],
)
def test_legacy_markers_and_hard_medical_claims_are_rejected(
    text: str,
    code: str,
) -> None:
    _channel, locale_pack = snapshots("en-US")
    payload = {
        "schemaVersion": "localized-script-v2/v1",
        "locale": "en-US",
        "title": "A careful title",
        "sections": [{"id": "opening", "narration": f"research suggests {text}"}],
    }

    with pytest.raises(LocalizedContentError) as error:
        validate_localized_content(ArtifactKind.SCRIPT, payload, locale_pack)

    assert error.value.code == code


def test_provider_boundary_rejects_non_english_search_brief() -> None:
    channel, locale_pack = snapshots("fr-FR")
    script = {
        "schemaVersion": "localized-script-v2/v1",
        "locale": "fr-FR",
        "title": "Une habitude réaliste",
        "sections": [{"id": "opening", "narration": "les recherches suggèrent un bénéfice possible"}],
    }
    prompt = build_scenes_prompt(channel, locale_pack, script)
    payload = {
        "schemaVersion": "localized-scenes-v2/v1",
        "locale": "fr-FR",
        "scenes": [
            {
                "id": "opening",
                "narration": "les recherches suggèrent un bénéfice possible",
                "visualType": "video",
                "visualPrompt": "Routine quotidienne réaliste",
                "searchBrief": {
                    "language": "fr",
                    "queries": ["adulte marchant dans un parc"],
                },
            }
        ],
    }

    with pytest.raises(ProviderBoundaryError) as error:
        validate_structured_response(
            payload,
            prompt=prompt,
            locale_pack=locale_pack,
            schema_root=SCHEMA_ROOT,
            provider="fake",
        )

    assert error.value.code == "INVALID_PROVIDER_RESPONSE"


def test_search_brief_cannot_claim_english_while_using_non_latin_query() -> None:
    channel, locale_pack = snapshots("ja-JP")
    prompt = build_scenes_prompt(
        channel,
        locale_pack,
        {
            "schemaVersion": "localized-script-v2/v1",
            "locale": "ja-JP",
            "title": "毎日の習慣",
            "sections": [{"id": "opening", "narration": "研究では役立つ可能性があります。"}],
        },
    )
    payload = {
        "schemaVersion": "localized-scenes-v2/v1",
        "locale": "ja-JP",
        "scenes": [
            {
                "id": "opening",
                "narration": "研究では役立つ可能性があります。",
                "visualType": "video",
                "visualPrompt": "毎日の散歩",
                "searchBrief": {
                    "language": "en",
                    "queries": ["公園を歩く日本人"],
                },
            }
        ],
    }

    with pytest.raises(ProviderBoundaryError):
        validate_structured_response(
            payload,
            prompt=prompt,
            locale_pack=locale_pack,
            schema_root=SCHEMA_ROOT,
            provider="fake",
        )


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '{"locale":"en-US","locale":"fr-FR"}',
        b"\xff\xfe",
        "x" * (MAX_STRUCTURED_RESPONSE_BYTES + 1),
    ],
)
def test_malformed_and_oversized_provider_output_is_rejected(
    raw,
) -> None:
    channel, locale_pack = snapshots("en-US")
    prompt = build_scenes_prompt(
        channel,
        locale_pack,
        {
            "schemaVersion": "localized-script-v2/v1",
            "locale": "en-US",
            "title": "Safe",
            "sections": [{"id": "opening", "narration": "research suggests"}],
        },
    )

    with pytest.raises(ProviderBoundaryError) as error:
        validate_structured_response(
            raw,
            prompt=prompt,
            locale_pack=locale_pack,
            schema_root=SCHEMA_ROOT,
            provider="fake",
        )

    assert error.value.code == "INVALID_PROVIDER_RESPONSE"
