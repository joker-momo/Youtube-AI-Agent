from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from video_agent.localized_v2.audio.capabilities import VoiceCapabilityRegistry
from video_agent.localized_v2.audio.tts import LocalizedTTS, TTSFailure
from video_agent.localized_v2.job_state import JobInput
from video_agent.localized_v2.orchestrator import (
    LocalizedOrchestrator,
    LocalizedPromptRunner,
)
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.queue import LocalizedQueue
from video_agent.localized_v2.worker import LocalizedWorker

from .audio_fixtures import FakeTTSBackend
from .locale_fixtures import snapshots
from .test_localized_orchestrator import FakeLocalizedProvider

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


def _worker(
    tmp_path: Path,
    *,
    tts_backend: FakeTTSBackend,
) -> tuple[LocalizedWorker, LocalizedQueue]:
    legacy = tmp_path / "legacy-jobs"
    legacy.mkdir()
    paths = RuntimePaths.build(tmp_path / "localized-runtime", legacy_jobs_root=legacy)
    paths.initialize()
    queue = LocalizedQueue(paths.queue_db)
    channel, locale_pack = snapshots("en-US")
    queue.create_job(
        JobInput(
            job_id="voice-only-job",
            channel_id=channel["channelId"],
            locale="en-US",
            topic="A realistic walking habit",
            channel_snapshot=channel,
            locale_snapshot=locale_pack,
        )
    )
    prompt_runner = LocalizedPromptRunner(
        paths,
        SCHEMA_ROOT,
        FakeLocalizedProvider(),
    )
    tts = LocalizedTTS(
        {"kokoro": tts_backend},
        VoiceCapabilityRegistry(frozenset({("kokoro", "a", "af_heart")})),
    )
    runner = LocalizedOrchestrator(prompt_runner, tts)
    return LocalizedWorker("voice-worker", paths, queue, runner), queue


def test_completed_job_contains_narration_timing_without_transcription(
    tmp_path: Path,
) -> None:
    worker, queue = _worker(tmp_path, tts_backend=FakeTTSBackend(duration_sec=0.25))

    assert worker.run_once()

    job = queue.get_job("voice-only-job")
    artifact_names = {
        artifact["name"] for artifact in queue.list_artifacts("voice-only-job")
    }
    timing_path = next(
        Path(artifact["path"])
        for artifact in queue.list_artifacts("voice-only-job")
        if artifact["name"] == "audio-timing.json"
    )
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    assert job["status"] == "COMPLETED"
    assert job["input"]["channel_snapshot"]["render"]["subtitles"]["enabled"] is False
    assert timing["totalDurationSec"] == 0.25
    assert {"opening.wav", "narration.wav", "audio-timing.json"} <= artifact_names
    assert not any(
        forbidden in name
        for name in artifact_names
        for forbidden in (
            "whisper",
            "subtitle",
            "word_segments",
            "music",
        )
    )
    assert not any("whisper" in module for module in sys.modules if module.startswith("video_agent"))


def test_tts_failure_stops_before_timing_and_promotes_no_audio(tmp_path: Path) -> None:
    worker, queue = _worker(tmp_path, tts_backend=FakeTTSBackend(fail=True))

    with pytest.raises(TTSFailure):
        worker.run_once()

    job = queue.get_job("voice-only-job")
    artifacts = queue.list_artifacts("voice-only-job")
    assert job["status"] == "FAILED"
    assert job["failure"]["code"] == "TTS_FAILED"
    assert job["failure"]["locale"] == "en-US"
    assert job["failure"]["voiceId"] == "af_heart"
    assert queue.completed_stages("voice-only-job") == (
        "idea",
        "script",
        "scenes",
        "seo",
        "qa",
    )
    assert not any(artifact["stage"] in {"audio", "timing"} for artifact in artifacts)
