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
    # The safe-area constants and panel-placement math live in the EXECUTABLE
    # timing module (endEngagementCueTiming.ts) so tests run the same math the
    # render uses; the cue component must consume the exported helpers.
    timing_source = (END_CUE.parent / "endEngagementCueTiming.ts").read_text(encoding="utf-8")
    for name in ("SAFE_LEFT", "SAFE_RIGHT", "SAFE_TOP", "SAFE_BOTTOM"):
        assert f"export const {name}" in timing_source
    source = END_CUE.read_text(encoding="utf-8")
    assert "endEngagementCueTiming" in source
    assert "panelLeftFor" in source and "panelTopFor" in source
