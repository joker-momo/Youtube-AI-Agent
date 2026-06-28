"""Tests for the MeloTTS provider wiring (Elena voice).

Covers the pure logic (pitch math, ffmpeg arg builder, client dispatch) and the
scene-level segmentation branch of ``synthesize_scene_track``. The real worker
(subprocess into the sidecar venv) is exercised by an integration check, not here.
"""
from __future__ import annotations

import wave
from pathlib import Path

import pytest

from video_agent.tts import (
    MeloTTSClient,
    _build_pitch_resample_cmd,
    _pitch_ratio,
    build_tts_client,
    synthesize_scene_track,
)


def write_wav(path: Path, sample_rate: int = 24000, seconds: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * int(sample_rate * seconds))


class RecordingClient:
    """Real test double: records the text it is asked to synthesize."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def synthesize(self, text, output_path, config):
        self.calls.append(text)
        write_wav(Path(output_path), int(config.get("sample_rate", 24000)))
        return {"provider": "fake", "sample_rate": int(config.get("sample_rate", 24000))}


# --- pitch math -----------------------------------------------------------


def test_pitch_ratio_zero_is_identity():
    assert _pitch_ratio(0.0) == pytest.approx(1.0)


def test_pitch_ratio_minus_one_semitone():
    assert _pitch_ratio(-1.0) == pytest.approx(0.943874, abs=1e-5)


def test_pitch_ratio_octave_down():
    assert _pitch_ratio(-12.0) == pytest.approx(0.5, abs=1e-6)


# --- ffmpeg arg builder ---------------------------------------------------


def test_build_pitch_resample_cmd_applies_shift():
    cmd = _build_pitch_resample_cmd("in.wav", "out.wav", -1.0, in_sr=44100, out_sr=24000)
    joined = " ".join(cmd)
    assert "asetrate=" in joined  # pitch shift present
    assert "atempo=" in joined  # tempo restored
    assert "aresample=24000" in joined  # downsample to pipeline rate
    assert "-ac" in cmd and "1" in cmd  # mono out
    # new sample rate for -1 st = round(44100 * 0.943874) = 41625
    assert "asetrate=41625" in joined


def test_build_pitch_resample_cmd_zero_shift_resamples_only():
    cmd = _build_pitch_resample_cmd("in.wav", "out.wav", 0.0, in_sr=44100, out_sr=24000)
    joined = " ".join(cmd)
    assert "aresample=24000" in joined
    assert "asetrate=" not in joined  # no pitch manipulation when shift is zero


# --- client dispatch ------------------------------------------------------


def test_build_tts_client_melo_returns_lazy_client():
    client = build_tts_client({"provider": "melo", "language": "ES"})
    assert isinstance(client, MeloTTSClient)
    assert client._proc is None  # worker not started until first synth


def test_build_tts_client_unknown_provider_still_raises():
    with pytest.raises(ValueError):
        build_tts_client({"provider": "does-not-exist"})


# --- scene vs clause segmentation ----------------------------------------


def _two_clause_scene_doc():
    return {
        "total_duration_sec": 6,
        "scenes": [
            {
                "id": "scene-01",
                "duration_sec": 6,
                "narration": "Primera idea clara. Segunda idea con una pausa breve.",
            }
        ],
    }


def test_scene_segmentation_synthesizes_whole_scene_in_one_call(tmp_path):
    client = RecordingClient()
    doc = _two_clause_scene_doc()
    synthesize_scene_track(
        doc,
        tmp_path / "narration.wav",
        {"provider": "melo", "segmentation": "scene", "sample_rate": 24000, "humanize": {"enabled": False}},
        client,
    )
    assert len(client.calls) == 1
    # the single call receives the full scene narration, not split clauses
    assert "Primera idea clara." in client.calls[0]
    assert "Segunda idea con una pausa breve." in client.calls[0]


def test_clause_segmentation_default_splits_into_multiple_calls(tmp_path):
    client = RecordingClient()
    doc = _two_clause_scene_doc()
    synthesize_scene_track(
        doc,
        tmp_path / "narration.wav",
        {"provider": "kokoro", "sample_rate": 24000, "humanize": {"enabled": False}},
        client,
    )
    # default clause segmentation splits the two sentences into separate synth calls
    assert len(client.calls) >= 2


def test_scene_segmentation_dynamic_sync_false_pads_to_plan(tmp_path):
    # Shorts path: scene segmentation + dynamic_sync False → pad short audio up to
    # the planned scene duration (never shrink the plan to raw speech).
    client = RecordingClient()  # writes 1s per call
    doc = {
        "total_duration_sec": 3,
        "scenes": [{"id": "s1", "duration_sec": 3, "narration": "Una frase corta."}],
    }
    synthesize_scene_track(
        doc,
        tmp_path / "n.wav",
        {
            "provider": "melo",
            "segmentation": "scene",
            "sample_rate": 24000,
            "dynamic_sync": False,
            "humanize": {"enabled": False},
        },
        client,
    )
    assert len(client.calls) == 1
    assert doc["scenes"][0]["duration_sec"] == pytest.approx(3.0, abs=0.01)


def test_scene_segmentation_sets_dynamic_duration(tmp_path):
    client = RecordingClient()
    doc = _two_clause_scene_doc()
    meta = synthesize_scene_track(
        doc,
        tmp_path / "narration.wav",
        {"provider": "melo", "segmentation": "scene", "sample_rate": 24000, "humanize": {"enabled": False}},
        client,
    )
    assert meta["sample_rate"] == 24000
    # RecordingClient writes 1s per call → scene duration tracks synthesized audio
    assert doc["scenes"][0]["duration_sec"] == pytest.approx(1.0, abs=0.05)
