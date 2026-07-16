import asyncio
import json as _json
import re as _re
from pathlib import Path

from video_agent.orchestrator.image_prompt_log import read_image_prompt_index
from video_agent.shorts.infographic.poster import generate_poster


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


# ── bug-541 U4: palette provenance (logged prompt == sent body) ─────────────



def _palette_contract(text: str) -> str:
    """Extract the role->hex contract section from a prompt/body."""
    m = _re.search(r"PALETTE CONTRACT \(MANDATORY.*?\)\:(.*?)\. Every surface", text, _re.S)
    assert m, "no palette contract found"
    return m.group(1).strip()


def _cfg(tmp_path, palette: dict) -> dict:
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    p = Path(tmp_path) / "style-dna.json"
    p.write_text(_json.dumps({"version": "t", "palette": palette}), encoding="utf-8")
    return {"channel": {"name": "Vida Plena 45+"}, "style_dna": {"path": str(p)}}


_PAL_X = {"background": "#101010", "primary": "#AA1111", "secondary": "#22BB22", "accent": "#3333CC", "text": "#F0F0F0"}
_PAL_Y = {"background": "#FFFFFF", "primary": "#EE7700", "secondary": "#00AACC", "accent": "#9900AA", "text": "#111111"}
_PLAN = {
    "poster_format": "warning_list",
    "title": "Errores con la sal",
    "items": [{"label": f"error {n}", "note": "cuidado"} for n in range(1, 6)],
}


def _capture(tmp_path, plan, cfg) -> tuple[str, str]:
    """Run generate_poster with a fake image_fn; return (raw body, logged prompt)."""
    short_dir = tmp_path / "short-01"
    sent: dict = {}

    async def image_fn(*, prompt, project_name, out_path, aspect_ratio="9:16"):
        sent["prompt"] = prompt
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG")
        return {"bytes": 4}

    asyncio.run(generate_poster(short_dir, plan, image_fn, cfg))
    entries = read_image_prompt_index(short_dir)
    return sent["prompt"], entries[0]["prompt"]


def test_logged_prompt_and_sent_body_share_one_palette_mapping(tmp_path):
    """R7/KTD4: the audit log must expose exactly the palette image_fn received."""
    raw, logged = _capture(tmp_path, _PLAN, _cfg(tmp_path, _PAL_X))
    assert _palette_contract(raw) == _palette_contract(logged)
    # And it is the CONFIGURED palette, not a model default.
    assert _PAL_X["primary"] in raw and _PAL_X["primary"] in logged


def test_wrapper_adds_dimensions_once_without_touching_the_contract(tmp_path):
    """U4-2: wrapping for the log must not duplicate/alter the palette contract."""
    raw, logged = _capture(tmp_path, _PLAN, _cfg(tmp_path, _PAL_X))
    assert raw in logged                       # body embedded verbatim
    assert logged.count("PALETTE CONTRACT") == 1
    assert logged.lower().count("1080x1920") >= 1


def test_two_qa_attempts_send_the_same_palette_contract(tmp_path):
    """U4-3/R8: a retry for the same plan+config reuses one mapping."""
    cfg = _cfg(tmp_path, _PAL_X)
    a_raw, _ = _capture(tmp_path / "a", _PLAN, cfg)
    b_raw, _ = _capture(tmp_path / "b", _PLAN, cfg)
    assert _palette_contract(a_raw) == _palette_contract(b_raw)


def test_different_palette_config_changes_the_logged_prompt(tmp_path):
    """U4-4: a different configured palette must change the logged prompt."""
    x_raw, x_log = _capture(tmp_path / "x", _PLAN, _cfg(tmp_path / "px", _PAL_X))
    y_raw, y_log = _capture(tmp_path / "y", _PLAN, _cfg(tmp_path / "py", _PAL_Y))
    assert _palette_contract(x_raw) != _palette_contract(y_raw)
    assert x_log != y_log


def test_prompt_log_failure_is_non_fatal_and_body_unchanged(tmp_path, monkeypatch):
    """U4-5: logging failure must not change what image_fn receives.

    The safety contract lives in ``safe_log_image_prompt``, which swallows
    ``log_image_prompt`` failures — so break the INNER writer and prove poster
    generation still completes with a byte-identical body."""
    from video_agent.orchestrator import image_prompt_log as log_mod

    cfg = _cfg(tmp_path / "cfg", _PAL_X)
    expected, _ = _capture(tmp_path / "ok", _PLAN, cfg)

    def boom(*a, **k):
        raise RuntimeError("log sink down")

    monkeypatch.setattr(log_mod, "log_image_prompt", boom)
    short_dir = tmp_path / "nolog"
    sent: dict = {}

    async def image_fn(*, prompt, project_name, out_path, aspect_ratio="9:16"):
        sent["prompt"] = prompt
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG")
        return {"bytes": 4}

    try:
        asyncio.run(generate_poster(short_dir, _PLAN, image_fn, cfg))
    except RuntimeError as exc:  # pragma: no cover - guards the contract
        raise AssertionError("prompt-log failure must stay non-fatal") from exc
    assert sent["prompt"] == expected
