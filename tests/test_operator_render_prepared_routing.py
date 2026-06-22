"""Short job dirs must always render through the prepared-short owner, even from
the CLI / entry points that don't pass `prepared_short=True` (review P1: silent
divergence — same dir rendered differently by entry point)."""
from __future__ import annotations

from pathlib import Path

from video_agent.pipeline import _should_use_prepared_short


def _short_dir_with_handoff(tmp_path: Path) -> Path:
    job = tmp_path / "shorts" / "s1"
    (job / "json").mkdir(parents=True)
    (job / "json" / "short_render_props.json").write_text("{}")
    return job


def test_explicit_flag_forces_prepared(tmp_path):
    job = tmp_path / "anything"
    job.mkdir()
    assert _should_use_prepared_short(prepared_short=True, is_short_job=False, job_dir=job)


def test_short_dir_with_handoff_auto_routes(tmp_path):
    job = _short_dir_with_handoff(tmp_path)
    # CLI path: prepared_short defaults False, but the handoff is present.
    assert _should_use_prepared_short(prepared_short=False, is_short_job=True, job_dir=job)


def test_short_dir_without_handoff_stays_legacy(tmp_path):
    job = tmp_path / "shorts" / "s2"
    (job / "json").mkdir(parents=True)  # no short_render_props.json
    assert not _should_use_prepared_short(prepared_short=False, is_short_job=True, job_dir=job)


def test_non_short_job_stays_legacy(tmp_path):
    job = tmp_path / "longform" / "j1"
    job.mkdir(parents=True)
    assert not _should_use_prepared_short(prepared_short=False, is_short_job=False, job_dir=job)


def test_handoff_at_root_also_detected(tmp_path):
    job = tmp_path / "shorts" / "s3"
    job.mkdir(parents=True)
    (job / "short_render_props.json").write_text("{}")
    assert _should_use_prepared_short(prepared_short=False, is_short_job=True, job_dir=job)
