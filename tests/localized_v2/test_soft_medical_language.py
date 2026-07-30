from __future__ import annotations

import pytest

from video_agent.localized_v2.content_safety import (
    LocalizedContentError,
    validate_localized_content,
)
from video_agent.localized_v2.contracts import ArtifactKind
from video_agent.localized_v2.registry import SUPPORTED_LOCALES

from .locale_fixtures import LOCALE_DATA, snapshots


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
def test_script_requires_locale_specific_soft_claim(locale: str) -> None:
    _channel, locale_pack = snapshots(locale)
    payload = {
        "schemaVersion": "localized-script-v2/v1",
        "locale": locale,
        "title": LOCALE_DATA[locale]["prefer"],
        "sections": [{"id": "opening", "narration": LOCALE_DATA[locale]["prefer"]}],
    }

    with pytest.raises(LocalizedContentError) as error:
        validate_localized_content(ArtifactKind.SCRIPT, payload, locale_pack)

    assert error.value.code == "SOFT_CLAIM_REQUIRED"


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
def test_locale_specific_cure_wording_is_rejected(locale: str) -> None:
    _channel, locale_pack = snapshots(locale)
    payload = {
        "schemaVersion": "localized-script-v2/v1",
        "locale": locale,
        "title": LOCALE_DATA[locale]["prefer"],
        "sections": [
            {
                "id": "opening",
                "narration": (
                    f"{LOCALE_DATA[locale]['soft']} "
                    f"{LOCALE_DATA[locale]['prohibited']}"
                ),
            }
        ],
    }

    with pytest.raises(LocalizedContentError) as error:
        validate_localized_content(ArtifactKind.SCRIPT, payload, locale_pack)

    assert error.value.code == "MEDICAL_OVERCLAIM"
