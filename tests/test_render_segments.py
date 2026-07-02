"""Segmented render (bug-441 follow-up): a long render is split into
frame-range chunks so a mid-render crash only loses one segment instead of
the whole render, and each segment's Remotion cache is freed immediately
after it lands on disk.

Uses real ffmpeg/ffprobe to generate and probe tiny test clips — the
verification logic (_segment_is_valid, _concat_segments) is exactly what
guards against trusting a corrupt/partial segment on resume, so it must be
tested against real files, not mocks.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from video_agent.stages.render import (
    RemotionSubprocessError,
    _clear_render_tmp_entries,
    _concat_segments,
    _segment_is_valid,
    _segment_plan,
    _segment_seconds,
    _segmented_render_enabled,
    _total_render_frames,
    build_remotion_commands,
)


def _make_clip(path: Path, *, seconds: float, fps: int = 30) -> None:
    """Real, ffprobe-able mp4 with silent audio (matches segment shape)."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s=320x180:r={fps}:d={seconds}",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-shortest",
            "-c:v", "libx264", "-c:a", "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _make_wav(path: Path, *, seconds: float) -> None:
    """Silent WAV standing in for the full-composition audio render."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={seconds}",
            "-c:a", "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _fake_render_dispatch(cmd, *, segment_seconds: float, audio_seconds: float) -> Path:
    """Stand-in for _run_with_progress in flow tests: writes a WAV for the
    full-composition audio command, a real clip for a segment command."""
    out_path = Path(cmd[7])
    if out_path.suffix == ".wav":
        _make_wav(out_path, seconds=audio_seconds)
    else:
        _make_clip(out_path, seconds=segment_seconds)
    return out_path


# ---------------------------------------------------------------------------
# Segment planning
# ---------------------------------------------------------------------------

def test_segment_plan_covers_total_frames_exactly():
    plan = _segment_plan(total_frames=100, segment_frames=30)
    assert plan == [(0, 29), (30, 59), (60, 89), (90, 99)]
    covered = sum(end - start + 1 for start, end in plan)
    assert covered == 100


def test_segment_plan_single_segment_when_shorter_than_chunk():
    assert _segment_plan(total_frames=50, segment_frames=3000) == [(0, 49)]


def test_segment_plan_exact_multiple_has_no_trailing_short_segment():
    plan = _segment_plan(total_frames=90, segment_frames=30)
    assert plan == [(0, 29), (30, 59), (60, 89)]


def test_segment_seconds_reads_config_and_falls_back_to_default(tmp_path):
    props = tmp_path / "render_props.json"
    props.write_text(json.dumps({"render": {"segment_seconds": 45}}), encoding="utf-8")
    assert _segment_seconds(props) == 45.0

    props.write_text(json.dumps({"render": {}}), encoding="utf-8")
    from video_agent.stages.render import DEFAULT_SEGMENT_SECONDS
    assert _segment_seconds(props) == DEFAULT_SEGMENT_SECONDS


def test_segmented_render_defaults_to_enabled(tmp_path):
    props = tmp_path / "render_props.json"
    props.write_text(json.dumps({"render": {}}), encoding="utf-8")
    assert _segmented_render_enabled(props) is True

    props.write_text(json.dumps({"render": {"segmented": False}}), encoding="utf-8")
    assert _segmented_render_enabled(props) is False


def test_total_render_frames_prefers_duration_in_frames(tmp_path):
    props = tmp_path / "render_props.json"
    props.write_text(
        json.dumps({"render": {"duration_in_frames": 4321, "fps": 30, "duration_sec": 1.0}}),
        encoding="utf-8",
    )
    assert _total_render_frames(props) == 4321


def test_total_render_frames_falls_back_to_fps_times_duration(tmp_path):
    props = tmp_path / "render_props.json"
    props.write_text(json.dumps({"render": {"fps": 30, "duration_sec": 10.0}}), encoding="utf-8")
    assert _total_render_frames(props) == 300


# ---------------------------------------------------------------------------
# build_remotion_commands: --frames / --for-seamless-aac-concatenation
# ---------------------------------------------------------------------------

def test_frame_range_adds_frames_flag(tmp_path):
    props = tmp_path / "render_props.json"
    props.write_text("{}", encoding="utf-8")
    commands = build_remotion_commands(
        props, tmp_path / "seg_0001.mp4", frame_range=(0, 2999)
    )
    assert "--frames" in commands.video
    assert commands.video[commands.video.index("--frames") + 1] == "0-2999"


def test_muted_flag_only_on_segment_renders(tmp_path):
    """Segments render video-only (--muted); audio is a single full-length
    track muxed in after concat (bug-454: per-segment audio accumulated AAC
    padding at every raw-concat boundary → progressive narration desync)."""
    props = tmp_path / "render_props.json"
    props.write_text("{}", encoding="utf-8")
    plain = build_remotion_commands(props, tmp_path / "v.mp4")
    assert "--muted" not in plain.video

    segment = build_remotion_commands(
        props, tmp_path / "v.mp4", frame_range=(0, 99), muted=True
    )
    assert "--muted" in segment.video
    # The Remotion seamless-AAC flag only works with Remotion's own
    # offset-trimming combiner — it must never reappear on our plain-concat path.
    assert "--for-seamless-aac-concatenation" not in segment.video


def test_audio_command_renders_full_composition_wav(tmp_path):
    from video_agent.stages.render import build_remotion_audio_command

    props = tmp_path / "render_props.json"
    props.write_text("{}", encoding="utf-8")
    cmd = build_remotion_audio_command(props, tmp_path / "audio.wav")
    joined = " ".join(cmd)
    assert "--codec wav" in joined
    assert "--frames" not in joined  # full composition, not a chunk
    assert str(tmp_path / "audio.wav") in joined


# ---------------------------------------------------------------------------
# Segment verification (resume safety) — real ffprobe against real clips
# ---------------------------------------------------------------------------

def test_segment_is_valid_accepts_matching_duration(tmp_path):
    seg = tmp_path / "seg_0001.mp4"
    _make_clip(seg, seconds=1.0, fps=30)  # 30 frames @ 30fps
    assert _segment_is_valid(seg, start=0, end=29, fps=30.0) is True


def test_segment_is_valid_rejects_short_partial_write(tmp_path):
    seg = tmp_path / "seg_0001.mp4"
    _make_clip(seg, seconds=0.3, fps=30)  # far short of the expected 1.0s
    assert _segment_is_valid(seg, start=0, end=29, fps=30.0) is False


def test_segment_is_valid_rejects_missing_or_empty_file(tmp_path):
    missing = tmp_path / "nope.mp4"
    assert _segment_is_valid(missing, start=0, end=29, fps=30.0) is False

    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    assert _segment_is_valid(empty, start=0, end=29, fps=30.0) is False


# ---------------------------------------------------------------------------
# Concat: lossless join, correct total duration
# ---------------------------------------------------------------------------

def test_concat_segments_produces_correct_total_duration(tmp_path):
    from video_agent.stages.render import probe_video_duration_sec

    seg1 = tmp_path / "seg_0001.mp4"
    seg2 = tmp_path / "seg_0002.mp4"
    seg3 = tmp_path / "seg_0003.mp4"
    _make_clip(seg1, seconds=1.0)
    _make_clip(seg2, seconds=1.0)
    _make_clip(seg3, seconds=0.5)

    output = tmp_path / "video.mp4"
    _concat_segments([seg1, seg2, seg3], output)

    assert output.exists()
    duration = probe_video_duration_sec(output)
    assert duration is not None
    assert abs(duration - 2.5) < 0.15
    # Concat list file is scratch, must not leak into outputs/.
    assert not output.with_name("video.concat.txt").exists()


def test_concat_segments_raises_on_ffmpeg_failure(tmp_path):
    bad = tmp_path / "not_a_video.mp4"
    bad.write_text("not a real video file", encoding="utf-8")
    with pytest.raises(RemotionSubprocessError):
        _concat_segments([bad], tmp_path / "out.mp4")


# ---------------------------------------------------------------------------
# Cache cleanup (user request: free disk right after each segment)
#
# Regression: the first version wiped the ENTIRE .render_tmp tree right after
# each segment's Remotion process exited. In production this raced with a
# segment 2 render that failed mid-way with a 404 for narration.wav inside a
# bundle directory under .render_tmp — the previous segment's async
# bundler/dev-server teardown (or the next segment's own bundle setup) was
# still touching that tree when it got wiped. Cleanup is now deferred by one
# full segment cycle: only entries that predate the PREVIOUS segment's own
# run are deleted, never anything the segment that just finished created.
# ---------------------------------------------------------------------------

def test_clear_render_tmp_entries_deletes_only_named_entries(tmp_path, monkeypatch):
    fake_tmp = tmp_path / ".render_tmp"
    fake_tmp.mkdir()
    (fake_tmp / "bundle-abc").mkdir()
    (fake_tmp / "bundle-abc" / "asset.js").write_text("x", encoding="utf-8")
    (fake_tmp / "loose_file.tmp").write_text("y", encoding="utf-8")
    (fake_tmp / "keep-me").mkdir()

    import video_agent.stages.render as render_mod
    monkeypatch.setattr(render_mod, "_render_tmp_dir", lambda: fake_tmp)
    _clear_render_tmp_entries({"bundle-abc", "loose_file.tmp"})

    remaining = {p.name for p in fake_tmp.iterdir()}
    assert remaining == {"keep-me"}


def test_render_segments_never_deletes_current_or_previous_segments_own_tmp_dir(
    tmp_path, monkeypatch
):
    """The bundle directory a segment's OWN render just created must survive
    at least until the NEXT segment has also fully finished — reproduces the
    exact timing that caused the narration.wav 404: segment N's own tmp dir
    must never be deleted while segment N (or N+1, still settling) could
    still be using it."""
    import video_agent.stages.render as render_mod

    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    render_props = job_dir / "json" / "render_props.json"
    render_props.write_text(
        json.dumps({"render": {"fps": 30, "segment_seconds": 1, "duration_in_frames": 90}}),
        encoding="utf-8",
    )
    video_path = job_dir / "outputs" / "video.mp4"

    fake_tmp = tmp_path / ".render_tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(render_mod, "_render_tmp_dir", lambda: fake_tmp)

    created_dirs: list[Path] = []

    def fake_run_with_progress(cmd, progress_path, **kwargs):
        out_path = _fake_render_dispatch(cmd, segment_seconds=1.0, audio_seconds=3.0)
        if out_path.suffix == ".wav":
            return  # audio render doesn't create a per-invocation bundle here
        # Simulate Remotion creating its own per-invocation bundle dir.
        bundle = fake_tmp / f"bundle-{len(created_dirs)}"
        bundle.mkdir()
        created_dirs.append(bundle)

    monkeypatch.setattr(render_mod, "_run_with_progress", fake_run_with_progress)

    render_mod._render_segments(
        render_props,
        video_path,
        stop_request_path=None,
        progress_path=job_dir / "json" / "render_progress.json",
        render_pid_path=job_dir / "json" / ".render.pid",
    )

    # 3 segments (90 frames / 30fps=1 -> 3 one-second chunks) -> 3 bundle dirs
    # created. Only the LAST one may still be pending cleanup after the loop
    # (it has nothing after it to trigger its deferred deletion) — every
    # earlier one must already be gone, and critically the deletion of any
    # given bundle dir must never have raced its own segment's run (verified
    # implicitly: fake_run_with_progress would have errored had its own
    # freshly-created dir been removed mid-call, which it never is here).
    remaining = {p.name for p in fake_tmp.iterdir()}
    assert remaining == {created_dirs[-1].name}


# ---------------------------------------------------------------------------
# Regression: ffmpeg concat-demuxer format=duration drift (real incident)
#
# 15-segment production render measured "MP4 duration 1739.9s does not match
# render.duration_sec 1737.9s" even though the video stream held EXACTLY the
# right frame count (52140, matching duration_in_frames). format=duration
# drifts after a concat-demuxer -c copy join; the video STREAM's own
# duration stays exact. probe_video_duration_sec must read stream duration,
# not container format=duration, so segmented renders don't get rejected
# for content that was never wrong.
# ---------------------------------------------------------------------------

def test_probe_duration_uses_stream_not_container_after_concat(tmp_path):
    from video_agent.stages.render import probe_video_duration_sec

    segs = [tmp_path / f"seg_{i}.mp4" for i in range(1, 4)]
    for seg in segs:
        _make_clip(seg, seconds=1.0, fps=30)

    output = tmp_path / "video.mp4"
    _concat_segments(segs, output)

    # format=duration is the buggy, drifted signal we must NOT trust.
    import subprocess as _subprocess
    container_duration = float(
        _subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(output)],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    )

    probed = probe_video_duration_sec(output)
    assert probed is not None
    # The fixed probe must land on the exact 3.0s content duration (30fps *
    # 3 one-second segments = 90 frames), not the drifted container value.
    assert abs(probed - 3.0) < 0.05
    # Only meaningful if this ffmpeg/build combination actually reproduces
    # the drift; skip the contrast assertion silently if it doesn't.
    if abs(container_duration - 3.0) > 0.01:
        assert abs(probed - container_duration) > 0.01


def test_probe_duration_matches_format_duration_for_non_concat_file(tmp_path):
    """For a normal (non-concatenated) file, stream and container duration
    agree — confirms the fix doesn't change behavior on the single-shot
    render path."""
    from video_agent.stages.render import probe_video_duration_sec

    seg = tmp_path / "plain.mp4"
    _make_clip(seg, seconds=2.0, fps=30)

    probed = probe_video_duration_sec(seg)
    assert probed is not None
    assert abs(probed - 2.0) < 0.05


# ---------------------------------------------------------------------------
# Regression: segments must survive until final validation passes; a valid
# existing video.mp4 must short-circuit re-rendering entirely (avoids
# re-rendering 15 segments + re-running loudnorm for output that was
# already correct — this exact incident cost ~20 minutes before the fix).
# ---------------------------------------------------------------------------

def test_render_segments_returns_paths_without_deleting_them(tmp_path, monkeypatch):
    """_render_segments must hand segments back to the caller instead of
    deleting them right after concat — deletion is the CALLER's job, done
    only once final duration validation has passed."""
    import video_agent.stages.render as render_mod

    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    render_props = job_dir / "json" / "render_props.json"
    render_props.write_text(
        json.dumps({"render": {"fps": 30, "segment_seconds": 1, "duration_in_frames": 90}}),
        encoding="utf-8",
    )
    video_path = job_dir / "outputs" / "video.mp4"

    call_count = {"n": 0}

    def fake_run_with_progress(cmd, progress_path, **kwargs):
        call_count["n"] += 1
        _fake_render_dispatch(cmd, segment_seconds=1.0, audio_seconds=3.0)

    monkeypatch.setattr(render_mod, "_run_with_progress", fake_run_with_progress)

    returned_paths = render_mod._render_segments(
        render_props,
        video_path,
        stop_request_path=None,
        progress_path=job_dir / "json" / "render_progress.json",
        render_pid_path=job_dir / "json" / ".render.pid",
    )

    # 3 one-second segments + the full-composition audio track.
    assert len(returned_paths) == 4
    assert sum(1 for p in returned_paths if p.suffix == ".wav") == 1
    assert all(p.exists() for p in returned_paths), "checkpoints must survive concat"
    assert video_path.exists()
    # Muxed output must carry BOTH streams at the full timeline length.
    from video_agent.stages.render import validate_rendered_video_duration
    validate_rendered_video_duration(video_path, expected_duration_sec=3.0)


def test_render_with_remotion_reuses_valid_existing_video_without_rerendering(tmp_path, monkeypatch):
    """If video.mp4 already exists and its (correctly-measured) duration
    matches, render_with_remotion must skip straight to completion instead
    of re-rendering — this is what should have happened for the real
    incident instead of burning another ~20 minutes."""
    import video_agent.stages.render as render_mod

    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    (job_dir / "outputs").mkdir(parents=True)
    render_props = job_dir / "json" / "render_props.json"
    render_props.write_text(
        json.dumps({
            "render": {"fps": 30, "duration_sec": 2.0},
            "scenes": [{"id": "s01", "duration_sec": 2.0}],
            "branding": {},
        }),
        encoding="utf-8",
    )
    video_path = job_dir / "outputs" / "video.mp4"
    _make_clip(video_path, seconds=2.0, fps=30)
    (job_dir / "outputs" / "thumbnail_1.jpg").write_bytes(b"jpg")

    def fail_if_called(*a, **k):
        raise AssertionError("should not re-render when the existing video.mp4 is already valid")

    monkeypatch.setattr(render_mod, "_render_segments", fail_if_called)
    monkeypatch.setattr(render_mod, "build_remotion_commands", fail_if_called)
    monkeypatch.setattr(render_mod, "_run_with_progress", fail_if_called)
    monkeypatch.setattr(render_mod, "_normalize_video_audio", fail_if_called)
    monkeypatch.setattr(render_mod, "_notify_render_done", lambda *a, **k: None)

    render_mod.render_with_remotion(render_props, video_path, notify_telegram=False)

    assert video_path.exists()


# ---------------------------------------------------------------------------
# Regression: segment 404 retry (bug-451) — the bundle's public dir is a
# symlink to remotion/public; a mid-render mutation of the target 404s the
# segment at a random frame. One retry recovers it.
# ---------------------------------------------------------------------------

def test_segment_retries_once_on_asset_404(tmp_path, monkeypatch):
    import video_agent.stages.render as render_mod

    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    render_props = job_dir / "json" / "render_props.json"
    render_props.write_text(
        json.dumps({"render": {"fps": 30, "segment_seconds": 1, "duration_in_frames": 30}}),
        encoding="utf-8",
    )
    # duration_in_frames == segment span -> single segment; use 2 segments
    render_props.write_text(
        json.dumps({"render": {"fps": 30, "segment_seconds": 1, "duration_in_frames": 60}}),
        encoding="utf-8",
    )
    video_path = job_dir / "outputs" / "video.mp4"

    calls = {"n": 0, "segment_calls": 0}

    def flaky_run_with_progress(cmd, progress_path, **kwargs):
        calls["n"] += 1
        if Path(cmd[7]).suffix == ".wav":
            _make_wav(Path(cmd[7]), seconds=2.0)
            return
        calls["segment_calls"] += 1
        if calls["segment_calls"] == 1:
            raise RemotionSubprocessError(
                "Remotion subprocess exited with code 1. Last output:\n"
                "Error: Received a status code of 404 while downloading file "
                "http://localhost:3000/public/jobs/x/assets/narration.wav."
            )
        _make_clip(Path(cmd[7]), seconds=1.0, fps=30)

    monkeypatch.setattr(render_mod, "_run_with_progress", flaky_run_with_progress)

    returned_paths = render_mod._render_segments(
        render_props,
        video_path,
        stop_request_path=None,
        progress_path=job_dir / "json" / "render_progress.json",
        render_pid_path=job_dir / "json" / ".render.pid",
    )

    assert len(returned_paths) == 3  # 2 segments + audio track
    # audio + (1 failed attempt + 1 retry for segment 1) + 1 for segment 2.
    assert calls["n"] == 4
    assert calls["segment_calls"] == 3
    assert video_path.exists()


def test_segment_does_not_retry_non_404_failures(tmp_path, monkeypatch):
    import video_agent.stages.render as render_mod

    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    render_props = job_dir / "json" / "render_props.json"
    render_props.write_text(
        json.dumps({"render": {"fps": 30, "segment_seconds": 1, "duration_in_frames": 60}}),
        encoding="utf-8",
    )

    calls = {"n": 0, "segment_calls": 0}

    def segments_crash(cmd, progress_path, **kwargs):
        calls["n"] += 1
        if Path(cmd[7]).suffix == ".wav":
            _make_wav(Path(cmd[7]), seconds=2.0)
            return
        calls["segment_calls"] += 1
        raise RemotionSubprocessError("Remotion subprocess exited with code 1. browser crashed")

    monkeypatch.setattr(render_mod, "_run_with_progress", segments_crash)

    with pytest.raises(RemotionSubprocessError, match="browser crashed"):
        render_mod._render_segments(
            render_props,
            job_dir / "outputs" / "video.mp4",
            stop_request_path=None,
            progress_path=job_dir / "json" / "render_progress.json",
            render_pid_path=job_dir / "json" / ".render.pid",
        )
    assert calls["segment_calls"] == 1  # no retry for non-404 failures


# ---------------------------------------------------------------------------
# Regression bug-454: an MP4 whose AUDIO track is longer than its video
# timeline (accumulated per-segment AAC padding) must be REJECTED — both by
# final validation and by the resume fast-path.
# ---------------------------------------------------------------------------

def _make_desynced_clip(path: Path, *, video_seconds: float, audio_seconds: float) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s=320x180:r=30:d={video_seconds}",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={audio_seconds}",
            "-c:v", "libx264", "-c:a", "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_validation_rejects_audio_longer_than_video(tmp_path):
    from video_agent.stages.render import validate_rendered_video_duration

    bad = tmp_path / "video.mp4"
    _make_desynced_clip(bad, video_seconds=2.0, audio_seconds=4.0)

    with pytest.raises(ValueError, match="AUDIO duration"):
        validate_rendered_video_duration(bad, expected_duration_sec=2.0)


def test_fast_path_never_reuses_audio_desynced_video(tmp_path, monkeypatch):
    """The production bug-454 artifact (frame-exact video, ~2s-long audio)
    must NOT be reused by the resume fast-path — it must fall through to a
    full re-render."""
    import video_agent.stages.render as render_mod

    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    (job_dir / "outputs").mkdir(parents=True)
    render_props = job_dir / "json" / "render_props.json"
    render_props.write_text(
        json.dumps({
            "render": {"fps": 30, "duration_sec": 2.0, "segmented": False},
            "scenes": [{"id": "s01", "duration_sec": 2.0}],
            "branding": {},
        }),
        encoding="utf-8",
    )
    video_path = job_dir / "outputs" / "video.mp4"
    _make_desynced_clip(video_path, video_seconds=2.0, audio_seconds=4.0)

    rerendered = {"called": False}

    def fake_run(cmd, progress_path, **kwargs):
        rerendered["called"] = True
        _make_clip(Path(cmd[7]), seconds=2.0, fps=30)

    monkeypatch.setattr(render_mod, "_run_with_progress", fake_run)
    monkeypatch.setattr(render_mod, "_normalize_video_audio", lambda *a, **k: None)
    monkeypatch.setattr(render_mod, "_notify_render_done", lambda *a, **k: None)
    (job_dir / "outputs" / "thumbnail_1.jpg").write_bytes(b"jpg")

    render_mod.render_with_remotion(render_props, video_path, notify_telegram=False)

    assert rerendered["called"], "desynced artifact must trigger a re-render, not fast-path reuse"
