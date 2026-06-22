"""Unit tests for post-render production continuity QA (spec §34).

The verdict logic is exercised with synthetic probe + boundary-luma inputs so it
runs without a real render. The real ffprobe/ffmpeg path is covered by the
``integration``-marked end-to-end continuity test.
"""
from __future__ import annotations

from video_agent.shorts import render_continuity_qa as rcq
from video_agent.shorts.render_continuity_qa import RenderProbe, evaluate_render_continuity


def _schedule() -> dict:
    return {
        "fps": 30,
        "scene_version": 1,
        "total_duration_in_frames": 180,
        "scene_boundaries": [
            {"scene_id": "s01", "from_frame": 0, "duration_in_frames": 60, "end_frame_exclusive": 60},
            {"scene_id": "s02", "from_frame": 60, "duration_in_frames": 75, "end_frame_exclusive": 135},
            {"scene_id": "s03", "from_frame": 135, "duration_in_frames": 45, "end_frame_exclusive": 180},
        ],
        "tracks": [],
    }


def _good_probe() -> RenderProbe:
    return RenderProbe(
        frame_count=180, duration_sec=6.0, width=1080, height=1920,
        fps=30.0, has_audio=True, decode_ok=True,
    )


def _good_boundary_luma() -> dict[int, float | None]:
    # cut frames for boundaries at 60 and 135 => {59,60,134,135} plus first/last
    return {f: 120.0 for f in (0, 59, 60, 134, 135, 179)}


def _props() -> dict:
    return {"render": {"resolution": "1080x1920", "fps": 30}}


def test_clean_render_passes() -> None:
    qa = evaluate_render_continuity(
        probe=_good_probe(), boundary_luma=_good_boundary_luma(),
        visual_schedule=_schedule(), render_props=_props(), fps=30,
    )
    assert qa["verdict"] == "PASS", qa
    assert qa["checks"]["frame_count_matches_schedule"] == "PASS"
    assert qa["checks"]["no_black_boundary_frames"] == "PASS"
    assert qa["checks"]["audio_stream_present"] == "PASS"


def test_frame_count_mismatch_fails() -> None:
    probe = _good_probe()
    probe.frame_count = 150  # encoder dropped 30 frames
    qa = evaluate_render_continuity(
        probe=probe, boundary_luma=_good_boundary_luma(),
        visual_schedule=_schedule(), render_props=_props(), fps=30,
    )
    assert qa["verdict"] == "FAIL"
    assert qa["checks"]["frame_count_matches_schedule"] == "FAIL"


def test_black_boundary_frame_fails() -> None:
    luma = _good_boundary_luma()
    luma[60] = 2.0  # black frame at the s01->s02 cut
    qa = evaluate_render_continuity(
        probe=_good_probe(), boundary_luma=luma,
        visual_schedule=_schedule(), render_props=_props(), fps=30,
    )
    assert qa["verdict"] == "FAIL"
    assert qa["checks"]["no_black_boundary_frames"] == "FAIL"
    assert 60 in qa["black_boundary_frames"]


def test_unextractable_boundary_frame_fails() -> None:
    luma = _good_boundary_luma()
    luma[134] = None  # decode failed at this boundary frame
    qa = evaluate_render_continuity(
        probe=_good_probe(), boundary_luma=luma,
        visual_schedule=_schedule(), render_props=_props(), fps=30,
    )
    assert qa["verdict"] == "FAIL"
    assert qa["checks"]["no_black_boundary_frames"] == "FAIL"


def test_missing_audio_fails() -> None:
    probe = _good_probe()
    probe.has_audio = False
    qa = evaluate_render_continuity(
        probe=probe, boundary_luma=_good_boundary_luma(),
        visual_schedule=_schedule(), render_props=_props(), fps=30,
    )
    assert qa["verdict"] == "FAIL"
    assert qa["checks"]["audio_stream_present"] == "FAIL"


def test_decode_failure_fails() -> None:
    probe = RenderProbe(0, 0.0, 0, 0, 0.0, False, decode_ok=False, error="probe_failed")
    qa = evaluate_render_continuity(
        probe=probe, boundary_luma={}, visual_schedule=_schedule(),
        render_props=_props(), fps=30,
    )
    assert qa["verdict"] == "FAIL"
    assert qa["checks"]["decode_ok"] == "FAIL"


def test_wrong_resolution_fails() -> None:
    probe = _good_probe()
    probe.width, probe.height = 720, 1280
    qa = evaluate_render_continuity(
        probe=probe, boundary_luma=_good_boundary_luma(),
        visual_schedule=_schedule(), render_props=_props(), fps=30,
    )
    assert qa["verdict"] == "FAIL"
    assert qa["checks"]["resolution_correct"] == "FAIL"


def test_legacy_no_schedule_skips_boundary_checks() -> None:
    # No compiled schedule (legacy render): frame/decode/audio still checked,
    # boundary checks are not applicable rather than a failure.
    qa = evaluate_render_continuity(
        probe=_good_probe(), boundary_luma={},
        visual_schedule=None, render_props=_props(), fps=30,
    )
    assert qa["verdict"] == "PASS", qa
    assert qa["checks"]["no_black_boundary_frames"] == "N/A"
    assert qa["checks"]["frame_count_matches_schedule"] == "N/A"


def test_probe_render_tolerates_na_stream_duration(monkeypatch) -> None:
    # ffprobe emits "N/A" for stream-level duration on many MP4s; probe_render must
    # fall back to the container/format duration instead of crashing on float("N/A").
    calls = {"n": 0}

    def fake_run_json(cmd):
        calls["n"] += 1
        if calls["n"] == 1:  # video probe
            return {
                "streams": [{
                    "nb_read_frames": "180", "width": 1080, "height": 1920,
                    "avg_frame_rate": "30/1", "duration": "N/A",
                }],
                "format": {"duration": "6.0"},
            }
        return {"streams": [{"index": 1}]}  # audio probe

    monkeypatch.setattr(rcq, "_run_json", fake_run_json)
    probe = rcq.probe_render(__import__("pathlib").Path("/tmp/x.mp4"))
    assert probe.decode_ok is True
    assert probe.frame_count == 180
    assert probe.duration_sec == 6.0  # from format, not a crash
    assert probe.width == 1080 and probe.height == 1920
    assert probe.has_audio is True


def test_schedule_hash_recorded() -> None:
    qa = evaluate_render_continuity(
        probe=_good_probe(), boundary_luma=_good_boundary_luma(),
        visual_schedule=_schedule(), render_props=_props(), fps=30,
    )
    assert qa["schedule_hash"]
    assert isinstance(qa["schedule_hash"], str)
