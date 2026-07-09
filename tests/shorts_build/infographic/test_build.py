import asyncio, json
from pathlib import Path
from video_agent.shorts.infographic.build import run_infographic_short

def _deps(qa_text):
    async def image_fn(*, prompt, project_name, out_path, aspect_ratio="16:9"):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True); Path(out_path).write_bytes(b"\x89PNG")
        return {"bytes": 4}
    def llm_fn(prompt):
        return json.dumps({"poster_format": "category_grid", "title": "Vista",
                           "hook_line": "Si tienes más de 60: cuida tu vista",
                           "items": [{"label": f"i{n}"} for n in range(6)], "cta": "Sigue"})
    def read_text_fn(png):
        return qa_text
    def tts_fn(short_dir, plan, cfg):
        p = Path(short_dir) / "audio" / "short_narration.wav"; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RIFF"); return p
    def render_fn(short_dir, props):
        out = Path(short_dir) / "outputs" / "short.mp4"; out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00"); return out
    return image_fn, llm_fn, read_text_fn, tts_fn, render_fn

def test_pass_gate_renders(tmp_path):
    image_fn, llm_fn, read_text_fn, tts_fn, render_fn = _deps("vista i0 i1 i2 i3 i4 i5")
    status = asyncio.run(run_infographic_short(
        tmp_path / "short-01", {"audience": {"age_range": [45, 75]}}, {"topic": "vista despues de los 60"},
        image_fn=image_fn, llm_fn=llm_fn, read_text_fn=read_text_fn, tts_fn=tts_fn, render_fn=render_fn))
    assert status["short_type"] == "infographic"
    assert status["rendered"] is True
    assert (tmp_path / "short-01" / "outputs" / "short.mp4").exists()

def test_failed_text_qa_blocks_render(tmp_path):
    image_fn, llm_fn, read_text_fn, tts_fn, render_fn = _deps("totally different words")
    status = asyncio.run(run_infographic_short(
        tmp_path / "short-02", {"audience": {"age_range": [45, 75]}}, {"topic": "vista"},
        image_fn=image_fn, llm_fn=llm_fn, read_text_fn=read_text_fn, tts_fn=tts_fn, render_fn=render_fn,
        max_poster_attempts=2))
    assert status["status"] == "needs_manual_review"
    assert status["rendered"] is False
    assert not (tmp_path / "short-02" / "outputs" / "short.mp4").exists()

def test_qa_disabled_renders_without_reader(tmp_path):
    # No read_text_fn -> QA skipped -> renders even though poster text is unverified.
    image_fn, llm_fn, _read, tts_fn, render_fn = _deps("garbled unreadable poster")
    status = asyncio.run(run_infographic_short(
        tmp_path / "short-03", {"audience": {"age_range": [45, 75]}}, {"topic": "vista"},
        image_fn=image_fn, llm_fn=llm_fn, tts_fn=tts_fn, render_fn=render_fn))
    assert status["rendered"] is True
    assert status["status"] == "rendered"
    qa = json.loads((tmp_path / "short-03" / "json" / "poster_qa.json").read_text())
    assert qa["verdict"] == "skipped"
