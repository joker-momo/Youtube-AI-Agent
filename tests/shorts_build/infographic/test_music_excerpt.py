from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from video_agent.shorts import music_selector
from video_agent.utils.json_io import read_yaml

REPO = Path(__file__).resolve().parents[3]


def _library_config(track_file: Path | str = "assets/music/fresh_fallen_snow_chris_haugen.mp3") -> dict:
    return {
        "shorts": {
            "infographic": {
                "music_source": "library",
                "music_excerpt_min_offset_sec": 5.0,
                "music_excerpt_end_margin_sec": 1.0,
            }
        },
        "music_library": {
            "tracks": {
                "shorts_daily_habit": {
                    "file": str(track_file),
                    "title": "Fresh Fallen Snow",
                    "artist": "Chris Haugen",
                },
                "shorts_sleep_stress": {
                    "file": "assets/music/floating_home_brian_bolger.mp3",
                    "title": "Floating Home",
                },
            }
        },
    }


def test_food_topic_keeps_fresh_fallen_snow_mapping() -> None:
    cfg = _library_config()

    key = music_selector.select_music_track("food", cfg)

    assert key == "shorts_daily_habit"
    assert cfg["music_library"]["tracks"][key]["title"] == "Fresh Fallen Snow"


def test_vida_plena_infographic_config_uses_library_excerpts() -> None:
    cfg = read_yaml(REPO / "configs" / "vida-plena-45" / "channel.yaml")
    infographic = cfg["shorts"]["infographic"]

    assert infographic["music_source"] == "library"
    assert infographic["music_excerpt_min_offset_sec"] == 5.0
    assert infographic["music_excerpt_end_margin_sec"] == 1.0
    track = music_selector.select_music_track("food", cfg)
    assert cfg["music_library"]["tracks"][track]["title"] == "Fresh Fallen Snow"


def test_excerpt_offset_is_deterministic_seeded_by_short_and_track() -> None:
    from video_agent.shorts.infographic.build import deterministic_music_excerpt_offset

    first = deterministic_music_excerpt_offset(214.0, 15.0, "short-a|shorts_daily_habit")
    repeated = deterministic_music_excerpt_offset(214.0, 15.0, "short-a|shorts_daily_habit")
    other_short = deterministic_music_excerpt_offset(214.0, 15.0, "short-b|shorts_daily_habit")
    other_track = deterministic_music_excerpt_offset(214.0, 15.0, "short-a|shorts_sleep_stress")

    assert first == repeated
    assert first != other_short
    assert first != other_track
    assert 5.0 <= first <= 198.0


def test_short_track_uses_zero_offset_loop_fallback() -> None:
    from video_agent.shorts.infographic.build import deterministic_music_excerpt_offset

    assert deterministic_music_excerpt_offset(12.0, 15.0, "short-a|track") == 0.0


def test_exactly_fitting_guard_margins_use_the_valid_minimum_offset() -> None:
    from video_agent.shorts.infographic.build import deterministic_music_excerpt_offset

    # 21s track - 15s bed - 1s end margin leaves exactly the valid 5s start.
    assert deterministic_music_excerpt_offset(21.0, 15.0, "short-a|track") == 5.0


def test_library_bed_seeks_excerpt_and_writes_reproducibility_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from video_agent.shorts.infographic import build as build_mod

    source = tmp_path / "fresh_fallen_snow.mp3"
    source.write_bytes(b"music")
    cfg = _library_config(source)
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        if command[0] == "ffprobe":
            return SimpleNamespace(stdout="214.0")
        commands.append(command)
        Path(command[-1]).write_bytes(b"m4a")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(build_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "video_agent.shorts.audio_mixer.resolve_music_file", lambda *_args: source
    )
    short_dir = tmp_path / "short-food-01"

    build_mod.prepare_infographic_music_bed(
        short_dir, "shorts_daily_habit", cfg, 15.0
    )

    command = commands[-1]
    assert "-ss" in command
    offset = float(command[command.index("-ss") + 1])
    assert 5.0 <= offset <= 198.0
    assert command.index("-ss") < command.index("-i")
    assert "-stream_loop" in command
    metadata = json.loads((short_dir / "json" / "music_selection.json").read_text())
    assert metadata == {
        "source": "library",
        "track_key": "shorts_daily_habit",
        "track_title": "Fresh Fallen Snow",
        "track_file": str(source),
        "track_duration_sec": 214.0,
        "excerpt_offset_sec": pytest.approx(offset),
        "excerpt_duration_sec": 15.0,
        "seed_key": "short-food-01|shorts_daily_habit",
        "selection_mode": "deterministic_random_excerpt",
    }


def test_unreadable_library_duration_fails_instead_of_silently_using_intro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from video_agent.shorts.infographic import build as build_mod

    source = tmp_path / "broken.mp3"
    source.write_bytes(b"broken")
    cfg = _library_config(source)
    monkeypatch.setattr(
        "video_agent.shorts.audio_mixer.resolve_music_file", lambda *_args: source
    )
    monkeypatch.setattr(build_mod, "probe_audio_duration_seconds", lambda _path: 0.0)

    with pytest.raises(RuntimeError, match="duration"):
        build_mod.prepare_infographic_music_bed(
            tmp_path / "short-food-01", "shorts_daily_habit", cfg, 15.0
        )
