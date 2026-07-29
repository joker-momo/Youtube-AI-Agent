import json
from pathlib import Path

import pytest
import yaml

from video_agent.shorts.asset_schedule import compute_schedule_hash
from video_agent.shorts.builder.render_props import (
    PreparedPropsError,
    _scene_timing_hash,
    build_prepared_short_render_props,
)
from video_agent.utils.json_io import read_yaml

ROOT = Path(__file__).resolve().parents[1]


def _scene_doc() -> dict:
    return {
        "short_id": "short-01",
        "scene_version": 7,
        "total_duration_sec": 4.0,
        "scenes": [
            {
                "id": "s01",
                "duration_sec": 2.0,
                "narration": "Uno",
                "visual_prompt": "gentle walking",
                "on_screen_text": "UNO",
                "caption": "",
                "motion": "none",
                "asset_refs": {"background": "jobs/job-1__short-01/assets/s01.mp4"},
                "layout": "short_tip",
            },
            {
                "id": "s02",
                "duration_sec": 2.0,
                "narration": "Dos",
                "visual_prompt": "gentle walking",
                "on_screen_text": "DOS",
                "caption": "",
                "motion": "none",
                "asset_refs": {"background": "jobs/job-1__short-01/assets/s01.mp4"},
                "layout": "short_tip",
            },
        ],
    }


def _schedule() -> dict:
    return {
        "schema_version": 2,
        "contract_revision": "3.2.3",
        "compiler_version": 1,
        "short_id": "short-01",
        "fps": 30,
        "timing_source": "tts_final",
        "scene_version": 7,
        "total_duration_in_frames": 120,
        "scene_boundaries": [
            {
                "scene_id": "s01",
                "from_frame": 0,
                "duration_in_frames": 60,
                "end_frame_exclusive": 60,
                "is_graphic": False,
            },
            {
                "scene_id": "s02",
                "from_frame": 60,
                "duration_in_frames": 60,
                "end_frame_exclusive": 120,
                "is_graphic": False,
            },
        ],
        "tracks": [
            {
                "track_id": "vt01",
                "track_type": "background_media",
                "visual_span_id": "vs01",
                "visual_beat_id": None,
                "scene_ids": ["s01", "s02"],
                "asset_ref": "jobs/job-1__short-01/assets/s01.mp4",
                "asset_id": "pexels-1",
                "provider": "pexels_video",
                "render_media_kind": "video",
                "source_media_kind": "native_video",
                "from_frame": 0,
                "duration_in_frames": 120,
                "end_frame_exclusive": 120,
                "trim_before_in_frames": 0,
                "trim_timebase_fps": 30,
                "trim_end_in_frames": None,
                "source_duration_sec": 8.0,
                "playback_rate": 1.0,
                "loop_policy": "forbid",
                "crop_plan": {"mode": "cover", "anchor": "center", "scale": 1.0},
                "motion_plan": {"name": "none", "apply_to_native_video": False},
                "overlay_policy": "scene_controlled",
                "z_index": 0,
                "selection_debug": {"mode": "continuous_clip"},
            }
        ],
        "qa": {"verdict": "PASS", "errors": [], "warnings": []},
    }


def _channel_config() -> dict:
    cfg = read_yaml(ROOT / "configs/vida-plena-45/channel.yaml")
    cfg["render"] = {**(cfg.get("render") or {}), "fps": 30, "resolution": "1080x1920"}
    shorts = dict(cfg.get("shorts") or {})
    shorts["render"] = {**(shorts.get("render") or {}), "fps": 30, "resolution": "1080x1920"}
    shorts["visual_timeline"] = {**(shorts.get("visual_timeline") or {}), "mode": "report_only"}
    cfg["shorts"] = shorts
    return cfg


def _style() -> dict:
    return {"palette": {"background": "#000", "primary": "#111", "secondary": "#222", "accent": "#333", "text": "#fff"}}


def _handoff(scene_doc: dict, schedule: dict) -> dict:
    return {
        "schema_version": 1,
        "contract_revision": "3.2.3",
        "short_id": "short-01",
        "scene_version": 7,
        "scene_timing_hash": _scene_timing_hash(scene_doc, 30),
        "visual_schedule_hash": compute_schedule_hash(schedule),
        "render": {
            "fps": 30,
            "duration_sec": 4.0,
            "duration_in_frames": 120,
            "composition": "ShortVideoStandard",
            "resolution": "1080x1920",
        },
        "audio": {"narration": "audio/short_mix.m4a", "music": None},
        "music_track": None,
        "visual_schedule": schedule,
    }


def test_prepared_short_props_drop_report_only_schedule_and_embed_enforced_schedule(tmp_path: Path):
    scenes = _scene_doc()
    schedule = _schedule()
    handoff = _handoff(scenes, schedule)
    base = {
        "short_dir": tmp_path / "short-01",
        "channel_config": _channel_config(),
        "style": _style(),
        "scenes": scenes,
        "assets_manifest": {"audio": {"narration": "audio/short_mix.m4a", "music": None}},
        "seo": {"title": "t", "description": "d", "thumbnail_path": "outputs/short_cover.jpg"},
        "branding": {"intro_sec": 0, "outro_sec": 0},
        "handoff": handoff,
    }

    report_only = build_prepared_short_render_props(**base, visual_timeline_mode="report_only")
    assert report_only["visual_schedule"] is None
    assert report_only["render"].get("duration_in_frames") is None

    enforced = build_prepared_short_render_props(**base, visual_timeline_mode="enforced")
    assert enforced["visual_schedule"] == schedule
    assert enforced["render"]["duration_in_frames"] == 120

    bad_handoff = {**handoff, "scene_timing_hash": "stale"}
    with pytest.raises(PreparedPropsError, match="scene_timing_hash_mismatch"):
        build_prepared_short_render_props(**{**base, "handoff": bad_handoff}, visual_timeline_mode="enforced")


def test_prepared_short_operator_render_skips_asset_preparation_and_writes_final_props(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from video_agent.pipeline import OperatorRenderOptions, render_operator_job

    channel_path = tmp_path / "channel.yaml"
    channel_path.write_text(yaml.safe_dump(_channel_config()), encoding="utf-8")

    short_dir = tmp_path / "short-01"
    json_dir = short_dir / "json"
    json_dir.mkdir(parents=True)
    scenes = _scene_doc()
    schedule = _schedule()
    handoff = _handoff(scenes, schedule)
    (json_dir / "script.json").write_text(
        json.dumps({"channel_id": "vida-plena-45", "job_id": "short-01", "hook": "h", "sections": [], "narration": "", "cta": "", "qa": {"verdict": "PASS"}}),
        encoding="utf-8",
    )
    (json_dir / "scenes.json").write_text(json.dumps({**scenes, "channel_id": "vida-plena-45", "job_id": "short-01", "qa": {"verdict": "PASS"}}), encoding="utf-8")
    (json_dir / "seo.json").write_text(
        json.dumps(
            {
                "job_id": "short-01",
                "title": "t",
                "description": "d",
                "tags": [],
                "language": "es-ES",
                "ai_disclosure": True,
                "thumbnail_path": "outputs/short_cover.jpg",
                "thumbnail_text": "CAMINA MEJOR",
                "suggested_pinned_comments": "Cuéntanos tu rutina.",
            }
        ),
        encoding="utf-8",
    )
    (json_dir / "assets_manifest.json").write_text(json.dumps({"audio": {"narration": "audio/short_mix.m4a", "music": None}, "scenes": []}), encoding="utf-8")
    (json_dir / "short_render_props.json").write_text(json.dumps(handoff), encoding="utf-8")

    def fail_prepare_assets(*args, **kwargs):
        raise AssertionError("prepare_assets must not run for prepared shorts")

    monkeypatch.setattr("video_agent.pipeline.prepare_assets", fail_prepare_assets)
    # create_visual_contact_sheet was removed by the faceless-render refactor
    # (f671050) — _write_visual_review now sets contact_sheet to the artifact
    # constant directly, no separate generator call to monkeypatch.
    monkeypatch.setattr(
        "video_agent.pipeline._write_visual_review",
        lambda *a, **k: {
            "qa": {"status": "PASS", "issue_count": 0},
            "summary": {"total_scenes": 0},
            "scenes": [],
            "contact_sheet": "outputs/visual_contact_sheet.jpg",
        },
    )
    monkeypatch.setattr("video_agent.pipeline._validate_visual_review", lambda *a, **k: None)

    result = render_operator_job(
        OperatorRenderOptions(
            channel_path=channel_path,
            job_dir=short_dir,
            render=False,
            require_operator_qa=False,
            prepared_short=True,
        )
    )

    final_props = json.loads((result.job_dir / "json" / "render_props.json").read_text(encoding="utf-8"))
    assert final_props["visual_schedule"] is None
    assert final_props["audio"] == {"narration": "audio/short_mix.m4a", "music": None}


def test_prepared_short_props_zero_long_form_intro_outro(tmp_path: Path):
    """bug-478: shorts must never inherit the long-form intro/outro branding.
    The Short composition renders scenes only, so render.duration_sec must equal
    the scene sum and the embedded branding must zero intro/outro (else the encoded
    MP4 duration mismatches render.duration_sec and the render fails)."""
    scenes = _scene_doc()  # scene sum = 4.0
    handoff = _handoff(scenes, _schedule())
    props = build_prepared_short_render_props(
        short_dir=tmp_path / "short-01",
        channel_config=_channel_config(),
        style=_style(),
        scenes=scenes,
        assets_manifest={"audio": {"narration": "audio/short_mix.m4a", "music": None}},
        seo={"title": "t", "description": "d", "thumbnail_path": "outputs/short_cover.jpg"},
        branding={
            "intro_sec": 10.005,
            "outro_sec": 8.0,
            "intro_video_path": "branding/vida-plena-45/intro.mp4",
            "outro_video_path": "branding/vida-plena-45/outro.mp4",
            "medical_disclaimer": {"enabled": True, "duration_sec": 8.0},
        },
        handoff=handoff,
        visual_timeline_mode="report_only",
    )
    # duration counts scenes only — no 18s of long-form intro/outro on a Short.
    assert props["render"]["duration_sec"] == 4.0
    assert props["branding"]["intro_sec"] == 0.0
    assert props["branding"]["outro_sec"] == 0.0
    assert props["branding"]["intro_video_path"] is None
    assert props["branding"]["outro_video_path"] is None
    assert props["branding"]["medical_disclaimer"]["enabled"] is False
    assert props["branding"]["medical_disclaimer"]["duration_sec"] == 0.0
