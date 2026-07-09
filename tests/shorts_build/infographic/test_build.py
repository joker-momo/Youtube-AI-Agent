import json
from pathlib import Path

from video_agent.shorts.infographic.build import run_infographic_short


def _deps(qa_text):
    async def image_fn(*, prompt, project_name, out_path, aspect_ratio="16:9"):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG")
        return {"bytes": 4}

    def llm_fn(prompt):
        # build_short_seo uses a distinct "SEO copywriter" prompt; the poster plan
        # prompt is the other branch.
        if "SEO copywriter" in prompt:
            return json.dumps({
                "title": "Si tienes más de 60: cuida vista",
                "description": "Alimentos para la vista.",
                "hashtags": ["#vista", "#shorts"],
                "pinned_comment": "¿Cuidas tu vista?",
            })
        return json.dumps({
            "poster_format": "category_grid", "title": "Vista",
            "hook_line": "Si tienes más de 60: cuida tu vista",
            "items": [{"label": f"i{n}"} for n in range(6)], "cta": "Sigue",
        })

    def read_text_fn(png):
        return qa_text

    def tts_fn(short_dir, plan, cfg):
        p = Path(short_dir) / "audio" / "short_narration.wav"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RIFF")
        return p

    def render_fn(short_dir, props):
        out = Path(short_dir) / "outputs" / "short.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out

    return image_fn, llm_fn, read_text_fn, tts_fn, render_fn


CFG = {"audience": {"age_range": [45, 75]}, "channel": {"name": "Vida Plena 45+"}}


def test_pass_gate_renders_and_writes_seo(tmp_path):
    image_fn, llm_fn, read_text_fn, tts_fn, render_fn = _deps("vista i0 i1 i2 i3 i4 i5")
    short_dir = tmp_path / "job-1" / "shorts" / "short-01"
    status = run_infographic_short(
        short_dir, CFG, {"topic": "vista despues de los 60"},
        image_fn=image_fn, llm_fn=llm_fn, read_text_fn=read_text_fn, tts_fn=tts_fn, render_fn=render_fn)
    assert status["short_type"] == "infographic"
    assert status["rendered"] is True
    assert (short_dir / "outputs" / "short.mp4").exists()
    # SEO artifact written with a valid <=40-char scroll-stopper title.
    seo_file = short_dir / "json" / "short_seo.json"
    assert seo_file.exists()
    assert len(json.loads(seo_file.read_text())["title"]) <= 40
    # Public refs use the short's own dir name (matches materialize_short_job_aliases).
    props = json.loads((short_dir / "json" / "short_render_props.json").read_text())
    assert props["poster"] == "jobs/short-01/assets/poster.png"
    assert props["audio"] == "jobs/short-01/audio/short_narration.wav"


def test_failed_text_qa_blocks_render(tmp_path):
    image_fn, llm_fn, read_text_fn, tts_fn, render_fn = _deps("totally different words")
    short_dir = tmp_path / "job-1" / "shorts" / "short-02"
    status = run_infographic_short(
        short_dir, CFG, {"topic": "vista"},
        image_fn=image_fn, llm_fn=llm_fn, read_text_fn=read_text_fn, tts_fn=tts_fn, render_fn=render_fn,
        max_poster_attempts=2)
    assert status["status"] == "needs_manual_review"
    assert status["rendered"] is False
    assert not (short_dir / "outputs" / "short.mp4").exists()


def test_qa_disabled_renders_without_reader(tmp_path):
    # No read_text_fn -> QA skipped -> renders even though poster text is unverified.
    image_fn, llm_fn, _read, tts_fn, render_fn = _deps("garbled unreadable poster")
    short_dir = tmp_path / "job-1" / "shorts" / "short-03"
    status = run_infographic_short(
        short_dir, CFG, {"topic": "vista"},
        image_fn=image_fn, llm_fn=llm_fn, tts_fn=tts_fn, render_fn=render_fn)
    assert status["rendered"] is True
    assert status["status"] == "rendered"
    qa = json.loads((short_dir / "json" / "poster_qa.json").read_text())
    assert qa["verdict"] == "skipped"
