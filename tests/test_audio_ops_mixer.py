"""Shared narration+BGM mixer primitive (video_agent.assets.audio_ops).

Regression (2026-07-10, operator listening test on an infographic Short):
raising the BGM's pre-mix gain had NO audible effect because the final
``loudnorm`` stage re-normalizes the WHOLE mixed signal to a fixed integrated
loudness target, discarding any voice/BGM balance set upstream. Narrated
Shorts (long, varied narration) still want that safety net; short, single-image
infographic Shorts need the manual balance preserved instead.
"""
from video_agent.assets.audio_ops import _mix_bgm_with_narration


def test_apply_loudnorm_false_omits_loudnorm_from_filter_graph(tmp_path, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        # Simulate ffmpeg producing a non-empty output file.
        out_path = cmd[-1]
        from pathlib import Path as _P
        _P(out_path).write_bytes(b"m4a")

    monkeypatch.setattr("video_agent.assets.audio_ops.subprocess.run", fake_run)
    narration = tmp_path / "narration.wav"
    narration.write_bytes(b"wav")
    bgm = tmp_path / "bgm.m4a"
    bgm.write_bytes(b"bgm")
    out = tmp_path / "mix.m4a"

    ok = _mix_bgm_with_narration(narration, bgm, out, apply_loudnorm=False)
    assert ok
    filter_arg = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert "loudnorm" not in filter_arg
    assert "alimiter" in filter_arg  # peak safety stays even without loudnorm
    assert "sidechaincompress" in filter_arg  # ducking is unaffected


def test_apply_loudnorm_true_keeps_existing_behavior(tmp_path, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        from pathlib import Path as _P
        _P(cmd[-1]).write_bytes(b"m4a")

    monkeypatch.setattr("video_agent.assets.audio_ops.subprocess.run", fake_run)
    narration = tmp_path / "narration.wav"
    narration.write_bytes(b"wav")
    bgm = tmp_path / "bgm.m4a"
    bgm.write_bytes(b"bgm")
    out = tmp_path / "mix.m4a"

    ok = _mix_bgm_with_narration(narration, bgm, out)  # default unchanged
    assert ok
    filter_arg = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert "loudnorm" in filter_arg
