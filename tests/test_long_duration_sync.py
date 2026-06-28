"""Regression guard for ``_sync_scene_durations_from_audio`` (long-form).

Bug (C2): whisper ``audio_offset_sec`` values are cumulative *plan* durations,
so Strategy A's per-scene ``offset[i+1] - offset[i]`` reproduces the LLM plan
durations verbatim for every scene except the last, and dumps the entire
measured-vs-plan delta onto the final scene. With cached TTS (the exact case
this function exists for) the middle scenes therefore keep their wrong
estimates while the last scene balloons or clamps. The corrected behaviour
distributes the measured narration length across ALL scenes proportionally,
keeping the +0.35s tail on the last scene only.
"""

from __future__ import annotations

import numpy as np
import soundfile as sf

from video_agent.pipeline import _sync_scene_durations_from_audio
from video_agent.utils.json_io import write_json


def _write_narration(job_dir, seconds: float) -> None:
    assets = job_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    sr = 8000
    sf.write(str(assets / "narration.wav"), np.zeros(int(sr * seconds)), sr)


def _write_whisper(job_dir, scene_ids_offsets) -> None:
    (job_dir / "json").mkdir(parents=True, exist_ok=True)
    write_json(
        job_dir / "json" / "whisper_timestamps.json",
        {"scenes": [{"scene_id": sid, "audio_offset_sec": off, "word_segments": []}
                     for sid, off in scene_ids_offsets]},
    )


def test_correction_distributed_across_all_scenes(tmp_path):
    # Plan: two uniform 5.0s scenes (total 10.0s). Real narration is 20.0s, so
    # the plan underestimates by 2x. Durations are well above the 3.0s floor so
    # the min-clamp does not mask the behaviour under test.
    scene_doc = {"scenes": [
        {"id": "s1", "duration_sec": 5.0},
        {"id": "s2", "duration_sec": 5.0},
    ]}
    _write_whisper(tmp_path, [("s1", 0.0), ("s2", 5.0)])
    _write_narration(tmp_path, 20.0)

    _sync_scene_durations_from_audio(tmp_path, scene_doc)

    durs = [s["duration_sec"] for s in scene_doc["scenes"]]
    # The non-last scene must be corrected upward toward its ~9.8s real share.
    # The old code left s1 at the 5.0s plan value and dumped the whole delta on s2.
    assert durs[0] > 7.0, durs
    # Total matches the measured narration length (within rounding).
    assert abs(sum(durs) - 20.0) < 0.05, durs


def test_last_scene_keeps_tail_pad(tmp_path):
    scene_doc = {"scenes": [
        {"id": "s1", "duration_sec": 5.0},
        {"id": "s2", "duration_sec": 5.0},
    ]}
    _write_whisper(tmp_path, [("s1", 0.0), ("s2", 5.0)])
    _write_narration(tmp_path, 12.0)

    _sync_scene_durations_from_audio(tmp_path, scene_doc)

    durs = [s["duration_sec"] for s in scene_doc["scenes"]]
    # Equal plan shares, but the last scene carries the +0.35s breathing room.
    assert durs[-1] > durs[0], durs
    assert abs(sum(durs) - 12.0) < 0.05, durs
