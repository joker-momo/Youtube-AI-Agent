import inspect
import json

from video_agent.shorts import paths
from video_agent.shorts.infographic.build import render_selected_infographic_ideas


def test_worker_dispatches_infographic_command():
    from video_agent.orchestrator import worker
    src = inspect.getsource(worker)
    assert "shorts_render_infographic" in src
    assert "_run_short_infographic_job" in src
    assert "synthesize_infographic_voiceover" not in src


def test_studio_command_for_short_type():
    from video_agent.web.routes.shorts_studio import command_for_short_type
    assert command_for_short_type("infographic") == "shorts_render_infographic"
    assert command_for_short_type("narrated") == "shorts_render_selected_ideas"
    assert command_for_short_type("") == "shorts_render_selected_ideas"
    # Casing/whitespace must normalize (routing + duplicate guard must agree).
    assert command_for_short_type("  Infographic ") == "shorts_render_infographic"
    assert command_for_short_type("INFOGRAPHIC") == "shorts_render_infographic"


def _fake_deps():
    async def image_fn(*, prompt, project_name, out_path, aspect_ratio="16:9"):
        from pathlib import Path
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG")
        return {"bytes": 4}

    def llm_fn(prompt):
        if "SEO copywriter" in prompt:
            return json.dumps({"title": "Si tienes más de 60: cuida vista",
                               "description": "x", "hashtags": ["#vista", "#shorts"],
                               "pinned_comment": "¿?"})
        return json.dumps({"poster_format": "category_grid", "title": "Vista",
                           "hook_line": "Si tienes más de 60: cuida tu vista",
                           "items": [{"label": f"i{n}"} for n in range(6)], "cta": "Sigue"})

    def music_fn(short_dir, music_track, cfg, duration_sec):
        from pathlib import Path
        p = Path(short_dir) / "audio" / "infographic_bgm.m4a"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RIFF")
        return p

    def render_fn(short_dir, props):
        from pathlib import Path
        out = Path(short_dir) / "outputs" / "short.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out

    return image_fn, llm_fn, music_fn, render_fn


def test_render_selected_infographic_ideas_builds_and_manifests(tmp_path):
    job = tmp_path / "job-1"
    ideas_path = paths.short_ideas_path(job)
    ideas_path.parent.mkdir(parents=True, exist_ok=True)
    ideas_path.write_text(json.dumps({"ideas": [
        {"idea_id": "idea-1", "title": "Alimentos para la vista", "topic": "vista después de los 60"},
    ]}), encoding="utf-8")

    image_fn, llm_fn, music_fn, render_fn = _fake_deps()
    result = render_selected_infographic_ideas(
        job, {"audience": {"age_range": [45, 75]}, "channel": {"name": "Vida Plena 45+"}},
        ["idea-1"], image_fn=image_fn, llm_fn=llm_fn, music_fn=music_fn, render_fn=render_fn,
        read_text_fn=None,
    )

    entry = result["shorts"][0]
    assert entry["idea_id"] == "idea-1"
    assert entry["short_type"] == "infographic"
    assert entry["rendered"] is True
    # Per-short status persisted with the variant tag.
    status = json.loads(paths.short_status_path(job, entry["short_id"]).read_text())
    assert status["short_type"] == "infographic"
    assert status["audio_mode"] == "music_only"
    assert status["idea_id"] == "idea-1"
    # Manifest updated with the infographic short.
    manifest = json.loads(paths.manifest_path(job).read_text())
    assert any(s["short_id"] == entry["short_id"] and s["short_type"] == "infographic"
               for s in manifest["shorts"])


def test_infographic_run_refreshes_stale_failed_manifest_status(tmp_path):
    """A rendered infographic must flip the manifest's top-level status.
    Regression: an earlier narrated run left status='failed'; the infographic
    run appended its rendered short but never recomputed the top-level status,
    so the UI kept showing the job as Failed despite a finished video."""
    job = tmp_path / "job-1"
    ideas_path = paths.short_ideas_path(job)
    ideas_path.parent.mkdir(parents=True, exist_ok=True)
    ideas_path.write_text(json.dumps({"ideas": [
        {"idea_id": "idea-1", "title": "Alimentos para la vista", "topic": "vista"},
    ]}), encoding="utf-8")
    manifest_path = paths.manifest_path(job)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "status": "failed",
        "shorts": [{"short_id": "old-narrated", "idea_id": "idea-9",
                    "status": "failed", "rendered": False}],
    }), encoding="utf-8")

    image_fn, llm_fn, music_fn, render_fn = _fake_deps()
    render_selected_infographic_ideas(
        job, {"audience": {"age_range": [45, 75]}, "channel": {"name": "Vida Plena 45+"}},
        ["idea-1"], image_fn=image_fn, llm_fn=llm_fn, music_fn=music_fn, render_fn=render_fn,
        read_text_fn=None,
    )

    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "completed"
    # Old entries stay; the new rendered infographic is appended.
    ids = [s["short_id"] for s in manifest["shorts"]]
    assert "old-narrated" in ids and len(ids) == 2


def test_infographic_short_writes_live_progress_stages(tmp_path):
    """The Renders tab reads short_status.json to show a card + stage progress.
    Regression: the infographic pipeline wrote the status file only at the END,
    so an in-flight short was invisible in the UI for the whole render."""
    import json as _json
    from video_agent.shorts.infographic.build import run_infographic_short

    job = tmp_path / "job-1"
    short_dir = job / "shorts" / "short-01_idea-1_x"
    seen: dict = {}

    base_image, llm_fn, music_fn, render_fn = _fake_deps()

    async def image_fn(**kwargs):
        # Mid-poster: the status file must already exist and show progress.
        doc = _json.loads((short_dir / "short_status.json").read_text())
        seen["mid_poster"] = doc
        return await base_image(**kwargs)

    status = run_infographic_short(
        short_dir, {"audience": {"age_range": [45, 75]}, "channel": {"name": "V"}},
        {"topic": "vista", "title": "Vista"},
        image_fn=image_fn, llm_fn=llm_fn, music_fn=music_fn, render_fn=render_fn,
        read_text_fn=None,
    )

    mid = seen["mid_poster"]
    assert mid["status"] == "generating"
    assert mid["short_type"] == "infographic"
    stage_by_name = {s["name"]: s["status"] for s in mid["stages"]}
    assert stage_by_name["plan"] == "completed"
    assert stage_by_name["poster"] == "in_progress"
    assert stage_by_name["render"] == "pending"
    # Terminal status keeps the full stage list; voice/mix are "skipped" (voice
    # disabled by default) rather than "completed" — no-op stages must read as such.
    final_stages = {s["name"]: s["status"] for s in status["stages"]}
    assert final_stages["voice"] == "skipped"
    assert final_stages["mix"] == "skipped"
    non_skipped = {k: v for k, v in final_stages.items() if k not in ("voice", "mix")}
    assert all(v == "completed" for v in non_skipped.values()), final_stages


def test_studio_active_state_recognizes_infographic_render_command():
    """The job badge must show 'rendering_selected' while shorts_render_infographic
    runs. Regression: the active-command list only knew the narrated commands, so
    the UI kept saying ideas_ready during an infographic render."""
    from video_agent.web.routes.shorts_studio import _queued_or_running_synthesis_state
    from pathlib import Path as _P
    state = _queued_or_running_synthesis_state(
        _P("/nonexistent/job-x"),
        [{"job_id": "job-x", "command": "shorts_render_infographic"}],
    )
    assert state == "rendering_selected"


def test_infographic_run_writes_studio_render_run(tmp_path):
    """The Studio job badge reads studio_render_run.json FIRST (before the
    manifest), so the infographic pipeline must write it like the narrated
    pipeline does. Regression: a stale failed run doc from an earlier narrated
    attempt kept the whole job badged 'failed' after a successful infographic
    render."""
    from video_agent.shorts.idea_store import read_studio_render_run

    job = tmp_path / "job-1"
    ideas_path = paths.short_ideas_path(job)
    ideas_path.parent.mkdir(parents=True, exist_ok=True)
    ideas_path.write_text(json.dumps({"generation_id": "ideas-gen-7", "ideas": [
        {"idea_id": "idea-1", "title": "Alimentos para la vista", "topic": "vista"},
    ]}), encoding="utf-8")
    # Stale failed run doc from a previous narrated attempt.
    from video_agent.shorts.idea_store import write_studio_render_run
    write_studio_render_run(job, {"generation_id": "ideas-gen-7", "status": "failed"})

    image_fn, llm_fn, music_fn, render_fn = _fake_deps()
    render_selected_infographic_ideas(
        job, {"audience": {"age_range": [45, 75]}, "channel": {"name": "Vida Plena 45+"}},
        ["idea-1"], image_fn=image_fn, llm_fn=llm_fn, music_fn=music_fn, render_fn=render_fn,
        read_text_fn=None,
    )

    run = read_studio_render_run(job)
    assert run["status"] == "completed"
    assert run["generation_id"] == "ideas-gen-7"
    assert run["rendered_count"] == 1
    assert run["mode"] == "synthesis_ideas"


def test_render_selected_ideas_passes_idea_format_to_poster_plan(tmp_path):
    """The idea's format (alias-mapped) must seed the poster plan so the LLM
    does not re-pick a random layout for an idea conceived as, say, a
    mistake_list -> warning_list poster."""
    from video_agent.shorts.infographic.build import render_selected_infographic_ideas

    job = tmp_path / "job-1"
    ideas_path = paths.short_ideas_path(job)
    ideas_path.parent.mkdir(parents=True, exist_ok=True)
    ideas_path.write_text(json.dumps({"ideas": [
        {"idea_id": "idea-1", "title": "Errores con café", "topic": "café",
         "format": "mistake_list"},
    ]}), encoding="utf-8")

    seen_prompts: list[str] = []
    image_fn, base_llm, music_fn, render_fn = _fake_deps()

    def llm_fn(prompt):
        seen_prompts.append(prompt)
        if "poster_format" in prompt and "SEO" not in prompt:
            return json.dumps({"poster_format": "warning_list", "title": "Errores café",
                               "hook_line": "Errores con tu café diario",
                               "items": [{"label": f"e{n}", "note": "n"} for n in range(5)],
                               "cta": "Sigue"})
        return base_llm(prompt)

    render_selected_infographic_ideas(
        job, {"audience": {"age_range": [45, 75]}, "channel": {"name": "V"}},
        ["idea-1"], image_fn=image_fn, llm_fn=llm_fn, music_fn=music_fn, render_fn=render_fn,
        read_text_fn=None,
    )
    plan_prompts = [p for p in seen_prompts if "poster_format" in p and "Schema" in p]
    assert plan_prompts, "no poster-plan prompt captured"
    assert 'Use poster_format "warning_list"' in plan_prompts[0]
