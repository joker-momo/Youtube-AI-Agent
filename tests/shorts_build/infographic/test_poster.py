import asyncio
from pathlib import Path

from video_agent.shorts.infographic.poster import generate_poster
from video_agent.orchestrator.image_prompt_log import read_image_prompt_index


def test_generate_poster_writes_png_logs_and_passes_raw_body(tmp_path):
    short_dir = tmp_path / "short-01"
    plan = {
        "poster_format": "category_grid",
        "title": "Foods",
        "items": [{"label": "Chía"}] * 5,
        "audience_min_age": 60,
    }
    captured = {}

    async def fake_image_fn(*, prompt, project_name, out_path, aspect_ratio="16:9"):
        assert aspect_ratio == "9:16"
        captured["prompt"] = prompt
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG\r\n")
        return {"src": "https://chatgpt/img", "bytes": 6}

    out = asyncio.run(generate_poster(short_dir, plan, fake_image_fn))
    assert out.exists() and out.name == "poster.png"
    # image_fn receives the RAW body (driver adds the dimension instruction) -> no double-wrap.
    assert "Foods" in captured["prompt"]
    assert "1080x1920" not in captured["prompt"]
    # but the LOGGED prompt is the wrapped one.
    idx = read_image_prompt_index(short_dir)
    logged = [r for r in idx if r["kind"] == "infographic_poster"]
    assert logged and "1080x1920" in logged[0]["prompt"]
