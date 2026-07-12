"""EXECUTABLE CTA timing checks (bridge task 20260711-065504 P1-C).

Source-string greps proved insufficient (the shipped cue had the pointer off
both buttons at press time despite green contract tests). These tests compile
remotion/src/shorts/endEngagementCueTiming.ts — the single timing/geometry
source the component renders from — and EXECUTE it under node, asserting the
pointer sits exactly on each control at its press frame.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TIMING_TS = REPO / "remotion" / "src" / "shorts" / "endEngagementCueTiming.ts"


@pytest.fixture(scope="module")
def timing(tmp_path_factory) -> dict:
    """Compile the timing module and evaluate it at the press frames (30 fps)."""
    out_dir = tmp_path_factory.mktemp("cue_timing_js")
    subprocess.run(
        [
            "npx", "tsc", str(TIMING_TS),
            "--outDir", str(out_dir),
            "--module", "commonjs", "--target", "es2019", "--skipLibCheck",
        ],
        check=True, capture_output=True, cwd=REPO / "remotion", timeout=120,
    )
    script = f"""
const m = require({json.dumps(str(out_dir / 'endEngagementCueTiming.js'))});
const fps = 30;
const f = m.pressFrames(fps);
console.log(JSON.stringify({{
  pressFrames: f,
  pointerAtLike: m.pointerPositionAt(f.like, fps),
  pointerAtSubscribe: m.pointerPositionAt(f.subscribe, fps),
  stateAtLike: m.cueStateAt(f.like, fps),
  stateJustBeforeSubscribed: m.cueStateAt(f.subscribed - 1, fps),
  stateAtSubscribed: m.cueStateAt(f.subscribed, fps),
  likeTarget: m.LIKE_TARGET,
  likeIconBox: m.LIKE_ICON_BOX,
  subTarget: m.SUB_TARGET,
  panel: {{width: m.PANEL_WIDTH, height: m.PANEL_HEIGHT}},
  safe: {{left: m.SAFE_LEFT, right: m.SAFE_RIGHT, top: m.SAFE_TOP, bottom: m.SAFE_BOTTOM}},
  panelLeft1080: m.panelLeftFor(1080),
  pointerAtSubscribed: m.pointerPositionAt(f.subscribed, fps),
  pointerOpacityAtSubscribed: m.pointerOpacityAt(f.subscribed, fps),
  pointerOpacityAtLike: m.pointerOpacityAt(f.like, fps),
  sfx: m.sfxFrames(fps),
  sfxFiles: m.SFX_FILES,
}}));
"""
    out = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True, timeout=60
    ).stdout
    return json.loads(out)


def test_pointer_sits_exactly_on_like_at_its_press_frame(timing):
    assert timing["pointerAtLike"]["x"] == pytest.approx(timing["likeTarget"]["x"])
    assert timing["pointerAtLike"]["y"] == pytest.approx(timing["likeTarget"]["y"])


def test_pointer_sits_exactly_on_subscribe_at_its_press_frame(timing):
    assert timing["pointerAtSubscribe"]["x"] == pytest.approx(timing["subTarget"]["x"])
    assert timing["pointerAtSubscribe"]["y"] == pytest.approx(timing["subTarget"]["y"])


def test_press_state_sequence_matches_the_spec_timeline(timing):
    f = timing["pressFrames"]
    assert f["like"] < f["subscribe"] < f["subscribed"]
    # Like activates at its press; SUSCRITO must NOT appear during the press
    # phase — only at the subscribed frame.
    assert timing["stateAtLike"]["liked"] is True
    assert timing["stateAtLike"]["subscribePressed"] is False
    assert timing["stateJustBeforeSubscribed"]["subscribed"] is False
    assert timing["stateAtSubscribed"]["subscribed"] is True


def test_panel_fits_the_declared_safe_area(timing):
    safe, panel = timing["safe"], timing["panel"]
    assert panel["width"] <= safe["right"] - safe["left"]
    assert panel["height"] <= safe["bottom"] - safe["top"]


def test_like_target_is_the_exact_center_of_the_like_icon_box(timing):
    box = timing["likeIconBox"]
    assert timing["likeTarget"]["x"] == box["left"] + box["size"] / 2
    assert timing["likeTarget"]["y"] == box["top"] + box["size"] / 2


def test_panel_is_horizontally_centered_at_1080_with_equal_margins(timing):
    left = timing["panelLeft1080"]
    width = timing["panel"]["width"]
    assert left + width / 2 == 540
    assert left == 1080 - (left + width)  # equal margins


def test_pointer_is_invisible_and_outside_the_panel_at_the_subscribed_frame(timing):
    assert timing["pointerOpacityAtSubscribed"] == 0
    assert timing["pointerOpacityAtLike"] == 1
    pos = timing["pointerAtSubscribed"]
    panel = timing["panel"]
    assert pos["x"] > panel["width"] or pos["y"] > panel["height"]


def test_sfx_schedule_shares_the_press_frame_constants(timing):
    f = timing["pressFrames"]
    assert timing["sfx"]["likePop"] == f["like"]
    assert timing["sfx"]["bellDing"] == f["subscribe"]
    assert timing["sfxFiles"]["likePop"].endswith("like_pop.wav")
    assert timing["sfxFiles"]["bellDing"].endswith("bell_ding.wav")


def test_sfx_assets_exist_and_are_short_press_sounds():
    """Assets must exist in remotion/public and be bounded press blips, not
    full tracks (review round 2)."""
    for rel in ("sfx/like_pop.wav", "sfx/bell_ding.wav"):
        path = REPO / "remotion" / "public" / rel
        assert path.exists(), rel
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        duration = float(out)
        assert 0.05 <= duration <= 1.5, (rel, duration)
