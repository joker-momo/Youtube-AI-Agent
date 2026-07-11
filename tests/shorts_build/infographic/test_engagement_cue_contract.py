from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
INFOGRAPHIC_SHORT = REPO / "remotion" / "src" / "shorts" / "InfographicShort.tsx"
END_CUE = REPO / "remotion" / "src" / "shorts" / "EndEngagementCue.tsx"


def test_infographic_short_mounts_engagement_cue_only_in_final_sequence() -> None:
    source = INFOGRAPHIC_SHORT.read_text(encoding="utf-8")

    assert "showEngagementCue" in source
    assert "engagementCueDurationSec" in source
    assert "Sequence" in source
    assert "cueStart" in source and "cueFrames" in source
    assert "durationInFrames - cueFrames" in source
    assert "<EndEngagementCue" in source


def test_end_cue_contains_required_spanish_states_and_deterministic_motion() -> None:
    source = END_CUE.read_text(encoding="utf-8")

    assert "useCurrentFrame" in source
    assert "spring" in source
    assert "interpolate" in source
    assert "ME GUSTA" in source
    assert "SUSCRÍBETE" in source
    assert "SUSCRITO" in source
    assert "channelName" in source
    assert "bell" in source.lower()
    assert "Math.random" not in source
    assert "http://" not in source and "https://" not in source


def test_end_cue_declares_mobile_safe_area_bounds() -> None:
    source = END_CUE.read_text(encoding="utf-8")

    # Named constants make the YouTube overlay-safe region reviewable rather
    # than hiding magic positioning across nested style objects.
    assert "SAFE_LEFT" in source
    assert "SAFE_RIGHT" in source
    assert "SAFE_TOP" in source
    assert "SAFE_BOTTOM" in source

