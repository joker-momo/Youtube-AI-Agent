"""bug-469: whisper_timestamps must record its own started_at, not fall back
to a stale earlier stage's completed_at.

run_whisper_timestamps_stage never called _start_stage, so its started_at was
always guessed by _complete_stage's cross-stage fallback (nearest EARLIER
stage's completed_at). That guess is fine when stages run strictly
sequentially, but graphic_images/thumbnail_image/whisper_timestamps/
visual_schedule are dispatched concurrently by the DAG scheduler -- if the
much-slower graphic_images/thumbnail_image stages hadn't finished yet
(completed_at still None) by the time whisper_timestamps completed, the
fallback walked past them to a genuinely stale, unrelated stage's completed_at
(observed in production: seo_qa's timestamp from a PREVIOUS day's run), making
a ~2min transcription look like it took ~17 hours on the dashboard.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from video_agent.orchestrator import load_job
from video_agent.orchestrator.orchestrator import create_job
from video_agent.orchestrator.stages import audio as audio_stage


def _make_job(tmp_path: Path) -> Path:
    job_dir = tmp_path / "job"
    create_job(
        job_dir,
        "j1",
        "vida-plena-45",
        "json/idea.json",
        stages=["seo_qa", "graphic_images", "thumbnail_image", "whisper_timestamps", "visual_schedule"],
    )
    return job_dir


def test_whisper_timestamps_start_is_not_stale_earlier_stage_timestamp(tmp_path, monkeypatch):
    job_dir = _make_job(tmp_path)
    state = load_job(job_dir)

    # Simulate the exact production scenario: seo_qa completed on a PREVIOUS
    # day's run, and the concurrently-dispatched graphic_images/thumbnail_image
    # are still in flight (no completed_at yet) when whisper_timestamps starts.
    stale_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    for s in state.stages:
        if s.name == "seo_qa":
            s.status = "completed"
            s.completed_at = stale_ts
    state.current_stage = "whisper_timestamps"
    from video_agent.orchestrator import save_job

    save_job(job_dir, state)

    # Force the inline path (no real subprocess/whisper model needed for this
    # timing assertion) and stub out the actual transcription work.
    monkeypatch.setenv(audio_stage._AUDIO_SUBPROCESS_ENV, "1")

    def _fake_inline(job_dir_arg: Path) -> Path:
        from video_agent.orchestrator.stages._shared import _complete_stage

        output = job_dir_arg / "json" / "whisper_timestamps.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("{}")
        _complete_stage(job_dir_arg, "whisper_timestamps", output)
        return output

    monkeypatch.setattr(audio_stage, "_run_whisper_timestamps_stage_inline", _fake_inline)

    before = datetime.now(timezone.utc)
    audio_stage.run_whisper_timestamps_stage(job_dir)
    after = datetime.now(timezone.utc)

    final_state = load_job(job_dir)
    whisper_stage = final_state.stage("whisper_timestamps")
    assert whisper_stage.status == "completed"
    started_at = datetime.fromisoformat(whisper_stage.started_at.replace("Z", "+00:00"))

    assert started_at != datetime.fromisoformat(stale_ts)
    assert before - timedelta(seconds=5) <= started_at <= after + timedelta(seconds=5)
