"""Short job dirs must always render through the prepared-short owner, even from
the CLI / entry points that don't pass `prepared_short=True` (review P1: silent
divergence — same dir rendered differently by entry point)."""
from __future__ import annotations

from pathlib import Path

from video_agent.pipeline import _should_use_prepared_short


def _short_dir_with_inputs(tmp_path: Path, *, manifest: bool = True) -> Path:
    job = tmp_path / "shorts" / "s1"
    (job / "json").mkdir(parents=True)
    (job / "json" / "short_render_props.json").write_text("{}")
    if manifest:
        (job / "json" / "assets_manifest.json").write_text("{}")
    return job


def test_explicit_flag_forces_prepared(tmp_path):
    job = tmp_path / "anything"
    job.mkdir()
    assert _should_use_prepared_short(prepared_short=True, is_short_job=False, job_dir=job)


def test_short_dir_with_both_inputs_auto_routes(tmp_path):
    job = _short_dir_with_inputs(tmp_path)
    # CLI path: prepared_short defaults False, but both prepared inputs are present.
    assert _should_use_prepared_short(prepared_short=False, is_short_job=True, job_dir=job)


def test_handoff_without_manifest_stays_legacy(tmp_path):
    # Partial/older dir: handoff present but assets_manifest.json missing. Must fall
    # back to legacy (which rebuilds assets) instead of crashing in the prepared
    # branch's unconditional read_json(assets_manifest.json).
    job = _short_dir_with_inputs(tmp_path, manifest=False)
    assert not _should_use_prepared_short(prepared_short=False, is_short_job=True, job_dir=job)


def test_short_dir_without_handoff_stays_legacy(tmp_path):
    job = tmp_path / "shorts" / "s2"
    (job / "json").mkdir(parents=True)  # no short_render_props.json
    assert not _should_use_prepared_short(prepared_short=False, is_short_job=True, job_dir=job)


def test_non_short_job_stays_legacy(tmp_path):
    job = tmp_path / "longform" / "j1"
    job.mkdir(parents=True)
    assert not _should_use_prepared_short(prepared_short=False, is_short_job=False, job_dir=job)


def test_inputs_at_root_also_detected(tmp_path):
    job = tmp_path / "shorts" / "s3"
    job.mkdir(parents=True)
    (job / "short_render_props.json").write_text("{}")
    (job / "assets_manifest.json").write_text("{}")
    assert _should_use_prepared_short(prepared_short=False, is_short_job=True, job_dir=job)
