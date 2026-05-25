"""Tests for the opening-retention policy: skip intro/outro by default and
strengthen the first-30-seconds prompt rules."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from video_agent.contracts import repo_root
from video_agent.operator import _chatgpt_scenes_prompt, _chatgpt_script_prompt
from video_agent.pipeline import _prepare_branding


def _load_vida_plena_config() -> dict:
    cfg_path = repo_root() / "configs/vida-plena-45/channel.yaml"
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8"))


# ---------------- branding defaults ----------------


def test_prepare_branding_defaults_intro_outro_to_zero():
    """When branding is absent or enable_intro_outro is false, intro/outro = 0 s."""
    branding = _prepare_branding({"channel": {"id": "vida-plena-45"}})
    assert branding["intro_sec"] == 0.0
    assert branding["outro_sec"] == 0.0
    assert branding["intro_video_path"] is None
    assert branding["outro_video_path"] is None


def test_prepare_branding_skips_intro_outro_when_flag_false():
    config = {
        "channel": {"id": "vida-plena-45"},
        "branding": {
            "enable_intro_outro": False,
            "intro_sec": 3.0,
            "outro_sec": 3.0,
        },
    }
    branding = _prepare_branding(config)
    assert branding["intro_sec"] == 0.0
    assert branding["outro_sec"] == 0.0


def test_prepare_branding_honors_explicit_enable_flag(monkeypatch):
    # Force the resolvers to return None so the test doesn't depend on whatever
    # branding mp4s happen to live under asset_library/source.
    import video_agent.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "_resolve_brand_video_source", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline_module, "_resolve_brand_logo_source", lambda *a, **kw: None)

    config = {
        "channel": {"id": "no-such-channel-for-test"},
        "branding": {
            "enable_intro_outro": True,
            "intro_sec": 2.5,
            "outro_sec": 2.5,
        },
    }
    branding = _prepare_branding(config)
    assert branding["intro_sec"] == 2.5
    assert branding["outro_sec"] == 2.5


def test_vida_plena_channel_config_has_intro_outro_disabled():
    cfg = _load_vida_plena_config()
    assert cfg.get("branding", {}).get("enable_intro_outro") is False, (
        "channel.yaml must keep enable_intro_outro=false to guarantee the "
        "opening-retention policy stays in place."
    )


def test_prepare_branding_hides_channel_name_overlay_by_default():
    branding = _prepare_branding({"channel": {"id": "vida-plena-45"}})
    assert branding["show_channel_name_overlay"] is False


def test_prepare_branding_honors_show_channel_name_overlay_flag(monkeypatch):
    import video_agent.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "_resolve_brand_video_source", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline_module, "_resolve_brand_logo_source", lambda *a, **kw: None)

    config = {
        "channel": {"id": "no-such-channel-for-test"},
        "branding": {"show_channel_name_overlay": True},
    }
    branding = _prepare_branding(config)
    assert branding["show_channel_name_overlay"] is True


def test_vida_plena_channel_config_hides_channel_name_overlay():
    cfg = _load_vida_plena_config()
    assert cfg.get("branding", {}).get("show_channel_name_overlay") is False, (
        "channel.yaml must keep show_channel_name_overlay=false so the "
        "opening frame stays uncluttered."
    )


def test_mark_render_completed_auto_advances_review(tmp_path):
    """After render completes the review stage must auto-complete too.

    Review is a cosmetic HTML write, not an external approval — leaving
    it pending stalls the dashboard on "Review page" forever.
    """
    import json
    from datetime import datetime, timezone

    from video_agent.stages.render import _mark_render_stage_completed

    job_dir = tmp_path / "job-auto-review"
    job_dir.mkdir()
    state = {
        "job_id": "job-auto-review",
        "channel_id": "vida-plena-45",
        "current_stage": "render",
        "stages": [
            {"name": "render", "status": "in_progress", "started_at": "2025-01-01T00:00:00+00:00"},
            {"name": "review", "status": "pending"},
        ],
    }
    (job_dir / "job.json").write_text(json.dumps(state), encoding="utf-8")

    # write_operator_review hits the disk; skip its dependencies by ensuring
    # the call swallows failures (job dir has no script.json etc.).
    _mark_render_stage_completed(job_dir)

    updated = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    stage_by_name = {s["name"]: s for s in updated["stages"]}
    assert stage_by_name["render"]["status"] == "completed"
    assert stage_by_name["review"]["status"] == "completed"
    # No more downstream stages → current_stage stays "review" as a
    # readable "fully done" marker.
    assert updated["current_stage"] == "review"


def test_mark_render_completed_skips_review_when_already_done(tmp_path):
    import json

    from video_agent.stages.render import _mark_render_stage_completed

    job_dir = tmp_path / "job-already-done"
    job_dir.mkdir()
    state = {
        "job_id": "job-already-done",
        "channel_id": "vida-plena-45",
        "current_stage": "render",
        "stages": [
            {"name": "render", "status": "in_progress", "started_at": "2025-01-01T00:00:00+00:00"},
            {"name": "review", "status": "completed", "completed_at": "2025-01-01T00:00:00+00:00"},
        ],
    }
    (job_dir / "job.json").write_text(json.dumps(state), encoding="utf-8")

    _mark_render_stage_completed(job_dir)

    updated = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    stage_by_name = {s["name"]: s for s in updated["stages"]}
    assert stage_by_name["render"]["status"] == "completed"
    # Review already-completed timestamp must not be overwritten.
    assert stage_by_name["review"]["completed_at"] == "2025-01-01T00:00:00+00:00"


def test_channel_video_tsx_gates_channel_name_label():
    """Static smoke check: the channel-name label is wrapped in the flag."""
    src = (repo_root() / "remotion/src/ChannelVideo.tsx").read_text(encoding="utf-8")
    assert "showChannelNameOverlay ?" in src or "showChannelNameOverlay\n" in src
    assert "show_channel_name_overlay" in src


# ---------------- prompt retention guidance ----------------


def _spain_cfg() -> dict:
    return {
        "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+", "description": "Test"},
        "audience": {"language": "es-ES", "age_range": [45, 75], "primary_markets": ["ES"]},
        "seo": {"language": "es-ES", "min_tags": 5, "max_tags": 8},
        "content_format": {
            "target_duration_sec": 840,
            "scenes_count_min": 40,
            "scenes_count_max": 55,
        },
        "positioning": {"forbidden_phrases": [], "preferred_phrases": []},
        "tts": {"pace_wpm": 145},
    }


def test_script_prompt_contains_opening_retention_block():
    prompt = _chatgpt_script_prompt(_spain_cfg(), {"topic": "dormir mejor"})
    assert "OPENING RETENTION RULES (FIRST 30 SECONDS" in prompt
    assert "skips logo intro/outro" in prompt
    assert "first ~12 words MUST hook" in prompt
    # Lists the 4 retention patterns
    assert "Specific pain symptom" in prompt
    assert "Contradiction / pattern interrupt" in prompt
    assert "Concrete number + promise" in prompt
    assert "Vivid micro-scene" in prompt


def test_script_prompt_forbids_meta_introductions():
    prompt = _chatgpt_script_prompt(_spain_cfg(), {"topic": "dormir mejor"})
    assert "Bienvenidos" in prompt  # listed as forbidden opener
    assert "Hola" in prompt
    assert "meta-introduction" in prompt


def test_scenes_prompt_marks_scene_01_as_first_frame():
    script = {
        "channel_id": "vida-plena-45",
        "job_id": "j",
        "hook": "hook",
        "sections": [],
        "narration": "n",
        "cta": "cta",
    }
    prompt = _chatgpt_scenes_prompt(_spain_cfg(), script)
    assert "render skips logo intro/outro" in prompt
    assert "first frame the viewer sees" in prompt
    assert "scenes 01-03" in prompt


def test_complete_stage_falls_back_to_previous_completed_at(tmp_path):
    """When a stage was never explicitly started, ``_complete_stage`` must
    use the previous stage's ``completed_at`` as the new stage's
    ``started_at`` instead of ``now`` — otherwise every stage reports a
    0-second duration."""
    import json
    from datetime import datetime, timedelta, timezone

    from video_agent.orchestrator.stages import _complete_stage

    job_dir = tmp_path / "job-fallback"
    job_dir.mkdir()
    # Stage A finished a minute ago; stage B is the new in-flight one with
    # no explicit started_at (the historical bug).
    a_end = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    state = {
        "job_id": "job-fallback",
        "channel_id": "vida-plena-45",
        "idea_path": "ideas/test.json",
        "current_stage": "stage_b",
        "created_at": a_end,
        "updated_at": a_end,
        "stages": [
            {
                "name": "stage_a",
                "status": "completed",
                "started_at": a_end,
                "completed_at": a_end,
            },
            {"name": "stage_b", "status": "pending"},
        ],
    }
    (job_dir / "job.json").write_text(json.dumps(state), encoding="utf-8")
    output_marker = job_dir / "artifact.txt"
    output_marker.write_text("ok", encoding="utf-8")

    _complete_stage(job_dir, "stage_b", output_marker)

    updated = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    stage_b = next(s for s in updated["stages"] if s["name"] == "stage_b")
    assert stage_b["status"] == "completed"
    # started_at must equal stage_a.completed_at so the duration is non-zero.
    assert stage_b["started_at"] == a_end
    assert stage_b["completed_at"] != stage_b["started_at"]


def test_start_stage_marks_started_at_only_once(tmp_path):
    import json
    from datetime import datetime, timezone

    from video_agent.orchestrator.stages import _start_stage

    job_dir = tmp_path / "job-start"
    job_dir.mkdir()
    state = {
        "job_id": "job-start",
        "channel_id": "vida-plena-45",
        "idea_path": "ideas/test.json",
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
        "current_stage": "stage_a",
        "stages": [
            {"name": "stage_a", "status": "pending"},
        ],
    }
    (job_dir / "job.json").write_text(json.dumps(state), encoding="utf-8")

    _start_stage(job_dir, "stage_a")
    first = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    first_started = first["stages"][0]["started_at"]
    assert first["stages"][0]["status"] == "in_progress"
    assert first_started is not None

    # Idempotent — calling again must not move the timestamp.
    _start_stage(job_dir, "stage_a")
    second = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert second["stages"][0]["started_at"] == first_started
