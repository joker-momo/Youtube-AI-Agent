"""Shorts Autopilot v5 — Phase 6: renderer pure helpers."""

from __future__ import annotations

import json
from pathlib import Path


def test_materialize_short_job_aliases_emits_schema_valid_longform(tmp_path: Path):
    from video_agent.shorts import renderer

    sd = tmp_path / "short-01"
    sd.mkdir()
    (sd / "short_script.json").write_text(
        json.dumps({"narration": "n", "hook": "h", "cta": "c"}), encoding="utf-8"
    )
    (sd / "short_scenes.json").write_text(
        json.dumps({"scenes": [{"id": "s1", "duration_sec": 3.0}]}), encoding="utf-8"
    )
    (sd / "short_seo.json").write_text(
        json.dumps({"title": "t", "description": "d", "hashtags": ["#x"]}), encoding="utf-8"
    )
    renderer.materialize_short_job_aliases(sd, {"channel": {"id": "vida-plena-45"}})
    script = json.loads((sd / "json" / "script.json").read_text())
    for k in ("channel_id", "job_id", "hook", "sections", "narration", "cta", "qa"):
        assert k in script, k
    scenes = json.loads((sd / "json" / "scenes.json").read_text())
    for k in ("channel_id", "job_id", "scenes", "total_duration_sec", "qa"):
        assert k in scenes, k
    assert scenes["scenes"][0]["id"] == "s1"
    seo = json.loads((sd / "json" / "seo.json").read_text())
    for k in (
        "job_id",
        "title",
        "description",
        "tags",
        "language",
        "ai_disclosure",
        "thumbnail_path",
        "thumbnail_text",
        "suggested_pinned_comments",
    ):
        assert k in seo, k
    assert seo["tags"] == ["#x"]


def test_materialize_short_job_aliases_rounds_decimal_total_duration_for_schema(tmp_path: Path):
    from video_agent.shorts import renderer

    sd = tmp_path / "short-01"
    sd.mkdir()
    (sd / "short_scenes.json").write_text(
        json.dumps({"total_duration_sec": 26.9, "scenes": [{"id": "s1", "duration_sec": 26.9}]}),
        encoding="utf-8",
    )

    renderer.materialize_short_job_aliases(sd, {"channel": {"id": "vida-plena-45"}})

    scenes = json.loads((sd / "json" / "scenes.json").read_text())
    assert scenes["total_duration_sec"] == 27
    assert isinstance(scenes["total_duration_sec"], int)


def test_materialize_short_job_aliases_mirrors_visual_span_assets(tmp_path: Path, monkeypatch):
    from video_agent.shorts import renderer

    repo = tmp_path / "repo"
    sd = tmp_path / "short-01"
    span_asset = sd / "assets" / "visual_spans" / "vs01-pexels-1.mp4"
    span_asset.parent.mkdir(parents=True)
    span_asset.write_bytes(b"fake video")
    monkeypatch.setattr(renderer, "repo_root", lambda: repo)

    renderer.materialize_short_job_aliases(sd, {"channel": {"id": "vida-plena-45"}})

    mirrored = (
        repo
        / "remotion"
        / "public"
        / "jobs"
        / "short-01"
        / "assets"
        / "visual_spans"
        / span_asset.name
    )
    assert mirrored.read_bytes() == b"fake video"


def test_build_cover_extract_command_uses_frame_sec(tmp_path: Path):
    from video_agent.shorts import renderer

    video = tmp_path / "short.mp4"
    out = tmp_path / "short_cover.jpg"
    cmd = renderer.build_cover_extract_command(video, out, frame_sec=0.3)
    assert cmd[0] == "ffmpeg"
    s = " ".join(cmd)
    assert "-ss" in s and "0.3" in s
    assert "-frames:v 1" in s
    assert str(video) in s and str(out) in s


def test_short_render_resolution_is_vertical():
    from video_agent.shorts import renderer

    assert renderer.SHORT_RESOLUTION == "1080x1920"
