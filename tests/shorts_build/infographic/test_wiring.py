import inspect
import json

from video_agent.shorts import paths
from video_agent.shorts.infographic.build import render_selected_infographic_ideas


def test_worker_dispatches_infographic_command():
    from video_agent.orchestrator import worker
    src = inspect.getsource(worker)
    assert "shorts_render_infographic" in src
    assert "_run_short_infographic_job" in src


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

    def tts_fn(short_dir, plan, cfg):
        from pathlib import Path
        p = Path(short_dir) / "audio" / "short_narration.wav"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RIFF")
        return p

    def render_fn(short_dir, props):
        from pathlib import Path
        out = Path(short_dir) / "outputs" / "short.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out

    return image_fn, llm_fn, tts_fn, render_fn


def test_render_selected_infographic_ideas_builds_and_manifests(tmp_path):
    job = tmp_path / "job-1"
    ideas_path = paths.short_ideas_path(job)
    ideas_path.parent.mkdir(parents=True, exist_ok=True)
    ideas_path.write_text(json.dumps({"ideas": [
        {"idea_id": "idea-1", "title": "Alimentos para la vista", "topic": "vista después de los 60"},
    ]}), encoding="utf-8")

    image_fn, llm_fn, tts_fn, render_fn = _fake_deps()
    result = render_selected_infographic_ideas(
        job, {"audience": {"age_range": [45, 75]}, "channel": {"name": "Vida Plena 45+"}},
        ["idea-1"], image_fn=image_fn, llm_fn=llm_fn, tts_fn=tts_fn, render_fn=render_fn,
        read_text_fn=None,
    )

    entry = result["shorts"][0]
    assert entry["idea_id"] == "idea-1"
    assert entry["short_type"] == "infographic"
    assert entry["rendered"] is True
    # Per-short status persisted with the variant tag.
    status = json.loads(paths.short_status_path(job, entry["short_id"]).read_text())
    assert status["short_type"] == "infographic"
    assert status["idea_id"] == "idea-1"
    # Manifest updated with the infographic short.
    manifest = json.loads(paths.manifest_path(job).read_text())
    assert any(s["short_id"] == entry["short_id"] and s["short_type"] == "infographic"
               for s in manifest["shorts"])
