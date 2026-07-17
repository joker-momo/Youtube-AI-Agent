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
    m = _re.search(r"PALETTE CONTRACT \(MANDATORY\): use these EXACT hex values for the DESIGN layer — (.*?)\. The design layer", text, _re.S)
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
    # And it is the CONFIGURED palette, not a model default (role selection may
    # contrast-reject a specific entry, so assert the palette as a set).
    assert any(h in raw for h in _PAL_X.values())
    assert any(h in logged for h in _PAL_X.values())


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


# ── bug-546 U3: the palette sidecar is the retry authority ────────────────────

_VIDA = {"background": "#F6F1E8", "primary": "#2F6B57", "secondary": "#D98C5F",
         "accent": "#F5C24B", "text": "#26332F"}


def _vida_cfg(tmp_path):
    d = tmp_path / "chan"
    d.mkdir(parents=True, exist_ok=True)
    (d / "style-dna.json").write_text(_json.dumps({"palette": _VIDA}), encoding="utf-8")
    return {"style_dna": {"path": str(d / "style-dna.json")}, "channel": {"name": "Vida Plena 45+"}}


def _poster_plan(fmt="warning_list", title="Errores con la sal"):
    return {"poster_format": fmt, "title": title, "subtitle": "Cuida tu salud",
            "items": [{"label": f"e{n}", "note": "ojo"} for n in range(5)],
            "audience_min_age": 45}


def _recording_image_fn(calls):
    async def fn(*, prompt, project_name, out_path, aspect_ratio="16:9"):
        calls.append({"prompt": prompt, "out_path": out_path})
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG\r\n")
        return {"src": "x", "bytes": 6}
    return fn


def test_palette_is_persisted_before_image_and_retries_reuse_it_exactly(tmp_path):
    """AE3 / R11 + R12: write-before-send, then three retries are byte-identical."""
    from video_agent.shorts.infographic.poster import generate_poster, palette_path

    cfg = _vida_cfg(tmp_path)
    short = tmp_path / "shorts" / "short-01_idea-01_x"
    plan = _poster_plan()
    seen_at_call = {}

    async def fn(*, prompt, project_name, out_path, aspect_ratio="16:9"):
        # the sidecar must already exist by the time pixels are requested
        seen_at_call["existed"] = palette_path(short).exists()
        seen_at_call["bytes"] = palette_path(short).read_bytes()
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG\r\n")
        return {"src": "x", "bytes": 6}

    asyncio.run(generate_poster(short, plan, fn, cfg))
    assert seen_at_call["existed"], "image was requested before the palette was committed"
    first = _json.loads(palette_path(short).read_text(encoding="utf-8"))

    for _ in range(3):
        asyncio.run(generate_poster(short, plan, fn, cfg))
        assert _json.loads(palette_path(short).read_text(encoding="utf-8")) == first
        assert seen_at_call["bytes"] == palette_path(short).read_bytes()
    assert first["selected_at_utc"] and first["short_id"] == short.name


def test_sibling_from_a_separate_batch_is_avoided(tmp_path):
    """AE2 / R9: the second Short must not repeat the first Short's look, even
    though it is generated in a completely separate run."""
    from video_agent.shorts.infographic.poster import generate_poster, palette_path
    from video_agent.shorts.infographic.poster_prompt import dominance_distance

    cfg = _vida_cfg(tmp_path)
    shorts = tmp_path / "shorts"
    a, b = shorts / "short-01_idea-01_a", shorts / "short-01_idea-02_b"
    calls = []
    asyncio.run(generate_poster(a, _poster_plan("warning_list", "Errores con la sal"), _recording_image_fn(calls), cfg))
    asyncio.run(generate_poster(b, _poster_plan("myth_vs_truth", "Mitos del pan"), _recording_image_fn(calls), cfg))

    ca = _json.loads(palette_path(a).read_text(encoding="utf-8"))
    cb = _json.loads(palette_path(b).read_text(encoding="utf-8"))
    assert ca["dominant_signature"] != cb["dominant_signature"]
    d = dominance_distance(ca["dominant_signature"], cb["dominant_signature"])
    assert d["canvas_delta_e"] >= 15.0 or (
        d["changed_positions"] >= 3 and d["mass_changed"] and d["weighted_delta_e"] >= 18.0
    ), f"sibling repeat: {d}"
    # This palette has three canvases, so R7 tier 1 applies and the second poster
    # must land on a different field entirely — not merely reshuffle the same
    # colours, which is what the operator saw and called identical.
    assert ca["roles"]["canvas"] != cb["roles"]["canvas"], "siblings share one canvas"
    assert cb["selection_reason"].startswith("anti_repeat")


def test_third_short_avoids_both_recent_signatures(tmp_path):
    """AE4 / R10."""
    from video_agent.shorts.infographic.poster import generate_poster, palette_path

    cfg = _vida_cfg(tmp_path)
    shorts = tmp_path / "shorts"
    sigs = []
    for n, (fmt, title) in enumerate(
        [("warning_list", "Errores con la sal"), ("myth_vs_truth", "Mitos del pan"), ("numbered_tips", "Cinco pasos del agua")], 1
    ):
        d = shorts / f"short-01_idea-0{n}_x{n}"
        asyncio.run(generate_poster(d, _poster_plan(fmt, title), _recording_image_fn([]), cfg))
        sigs.append(_json.loads(palette_path(d).read_text(encoding="utf-8"))["dominant_signature"])
    assert sigs[2] != sigs[1] and sigs[2] != sigs[0], f"third repeats a recent look: {sigs}"


def test_current_short_and_invalid_siblings_do_not_steer_selection(tmp_path):
    """U3.4 + U3.5: a cancelled/failed sibling with no valid sidecar, and the
    Short's own directory, are invisible to history."""
    from video_agent.shorts.infographic.poster import _recent_sibling_contracts, generate_poster
    from video_agent.shorts.infographic.poster_prompt import effective_palette_fingerprint

    cfg = _vida_cfg(tmp_path)
    shorts = tmp_path / "shorts"
    good = shorts / "short-01_idea-01_ok"
    asyncio.run(generate_poster(good, _poster_plan(), _recording_image_fn([]), cfg))
    (shorts / "short-01_idea-99_cancelled" / "json").mkdir(parents=True)
    (shorts / "short-01_idea-98_broken" / "json").mkdir(parents=True)
    (shorts / "short-01_idea-98_broken" / "json" / "poster_palette.json").write_text("{not json", encoding="utf-8")

    fp = effective_palette_fingerprint(cfg)
    assert _recent_sibling_contracts(good, fp) == (), "own directory leaked into history"
    other = shorts / "short-01_idea-02_next"
    other.mkdir(parents=True)
    recent = _recent_sibling_contracts(other, fp)
    assert [c["short_id"] for c in recent] == [good.name], f"invalid siblings leaked: {recent}"


def test_stale_and_tampered_sidecars_are_replaced_before_any_image_call(tmp_path):
    """AE6 / R14: palette-fingerprint mismatch, unknown schema, bad role value and
    broken contrast evidence must all be discarded before pixels are requested."""
    from video_agent.shorts.infographic.poster import generate_poster, palette_path

    cfg = _vida_cfg(tmp_path)
    short = tmp_path / "shorts" / "short-01_idea-01_x"
    plan = _poster_plan()
    asyncio.run(generate_poster(short, plan, _recording_image_fn([]), cfg))
    valid = _json.loads(palette_path(short).read_text(encoding="utf-8"))

    corruptions = {
        "stale_palette_fingerprint": {**valid, "palette_fingerprint": "deadbeefcafe"},
        "unknown_schema": {**valid, "schema_version": "shorts_poster_palette.v99"},
        "missing_role": {**valid, "roles": {k: v for k, v in valid["roles"].items() if k != "divider_accent"}},
        "off_palette_role": {**valid, "roles": {**valid["roles"], "headline_1": "#FF00FF"}},
        "lying_contrast_evidence": {**valid, "contrast_evidence": {**valid["contrast_evidence"], "body_text_on_canvas": 21.0}},
    }
    for name, bad in corruptions.items():
        palette_path(short).write_text(_json.dumps(bad), encoding="utf-8")
        calls = []

        async def fn(*, prompt, project_name, out_path, aspect_ratio="16:9", _n=name, _calls=calls):
            written = _json.loads(palette_path(short).read_text(encoding="utf-8"))
            assert written["palette_fingerprint"] == valid["palette_fingerprint"], _n
            assert written["schema_version"] == valid["schema_version"], _n
            _calls.append(written)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"\x89PNG\r\n")
            return {"src": "x", "bytes": 6}

        asyncio.run(generate_poster(short, plan, fn, cfg))
        assert calls, f"{name}: image never ran"
        assert "#FF00FF" not in calls[0]["roles"].values(), name


def test_sidecar_write_failure_blocks_generation(tmp_path):
    """U3.8: never send a palette that a retry could not reproduce."""
    import pytest

    from video_agent.shorts.infographic import poster as poster_mod

    cfg = _vida_cfg(tmp_path)
    short = tmp_path / "shorts" / "short-01_idea-01_x"
    called = []

    def boom(path, data):
        raise OSError("disk full")

    original = poster_mod.write_json
    poster_mod.write_json = boom
    try:
        with pytest.raises(RuntimeError, match="could not persist the poster palette"):
            asyncio.run(generate_poster_capture(short, cfg, called))
    finally:
        poster_mod.write_json = original
    assert called == [], "image was generated despite an unpersisted palette"


async def generate_poster_capture(short, cfg, called):
    from video_agent.shorts.infographic.poster import generate_poster

    async def fn(*, prompt, project_name, out_path, aspect_ratio="16:9"):
        called.append(prompt)
        return {}

    return await generate_poster(short, _poster_plan(), fn, cfg)


def test_valid_sidecar_survives_newer_sibling_history(tmp_path):
    """U3.7 / R12: history moves on; a committed Short does not."""
    from video_agent.shorts.infographic.poster import generate_poster, palette_path

    cfg = _vida_cfg(tmp_path)
    shorts = tmp_path / "shorts"
    a = shorts / "short-01_idea-01_a"
    asyncio.run(generate_poster(a, _poster_plan(), _recording_image_fn([]), cfg))
    before = palette_path(a).read_bytes()
    for n in (2, 3):
        d = shorts / f"short-01_idea-0{n}_x"
        asyncio.run(generate_poster(d, _poster_plan("numbered_tips", f"Tema {n}"), _recording_image_fn([]), cfg))
    asyncio.run(generate_poster(a, _poster_plan(), _recording_image_fn([]), cfg))
    assert palette_path(a).read_bytes() == before


def test_concurrent_siblings_cannot_commit_the_same_look(tmp_path):
    """U3.10 / R20: two Shorts starting at once must serialize their decision, and
    the lock must NOT be held across the (slow, external) image call."""
    import threading

    from video_agent.shorts.infographic.poster import generate_poster, palette_path

    cfg = _vida_cfg(tmp_path)
    shorts = tmp_path / "shorts"
    inside_image_fn = threading.Barrier(2, timeout=10)
    results = {}

    def run(name, fmt, title):
        async def fn(*, prompt, project_name, out_path, aspect_ratio="16:9"):
            # If the palette lock were still held here, the second thread could
            # never reach this barrier and it would time out.
            inside_image_fn.wait()
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"\x89PNG\r\n")
            return {"src": "x", "bytes": 6}

        d = shorts / name
        asyncio.run(generate_poster(d, _poster_plan(fmt, title), fn, cfg))
        results[name] = _json.loads(palette_path(d).read_text(encoding="utf-8"))

    threads = [
        threading.Thread(target=run, args=("short-01_idea-01_a", "warning_list", "Errores con la sal")),
        threading.Thread(target=run, args=("short-01_idea-02_b", "myth_vs_truth", "Mitos del pan")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert not any(t.is_alive() for t in threads), "lock was held across the image call"
    assert len(results) == 2
    a, b = results["short-01_idea-01_a"], results["short-01_idea-02_b"]
    assert a["dominant_signature"] != b["dominant_signature"], "both siblings committed one look"


def test_equal_timestamps_break_ties_on_short_id_regardless_of_listing_order(tmp_path):
    """U3.11 / R13: history order comes from immutable fields, not the filesystem."""
    from video_agent.shorts.infographic import poster as poster_mod
    from video_agent.shorts.infographic.poster import (
        _recent_sibling_contracts,
        generate_poster,
        palette_path,
    )
    from video_agent.shorts.infographic.poster_prompt import effective_palette_fingerprint

    cfg = _vida_cfg(tmp_path)
    shorts = tmp_path / "shorts"
    names = ["short-01_idea-01_a", "short-01_idea-02_b", "short-01_idea-03_c"]
    for n, name in enumerate(names, 1):
        asyncio.run(generate_poster(shorts / name, _poster_plan("numbered_tips", f"Tema {n}"), _recording_image_fn([]), cfg))
        # force an exact timestamp collision across all three
        p = palette_path(shorts / name)
        data = _json.loads(p.read_text(encoding="utf-8"))
        data["selected_at_utc"] = "2026-07-17T09:00:00.000000Z"
        p.write_text(_json.dumps(data), encoding="utf-8")

    fp = effective_palette_fingerprint(cfg)
    target = shorts / "short-01_idea-04_d"
    target.mkdir(parents=True)
    expected = ["short-01_idea-03_c", "short-01_idea-02_b"]  # highest short_id wins the tie
    assert [c["short_id"] for c in _recent_sibling_contracts(target, fp)] == expected

    real_iterdir = Path.iterdir
    def shuffled(self):
        return reversed(sorted(real_iterdir(self)))
    poster_mod.Path.iterdir = shuffled
    try:
        assert [c["short_id"] for c in _recent_sibling_contracts(target, fp)] == expected
    finally:
        poster_mod.Path.iterdir = real_iterdir


# ── bug-546 U4: sidecar == sent body == logged prompt ─────────────────────────

def _logged_poster_prompt(short_dir):
    idx = read_image_prompt_index(short_dir)
    rows = [r for r in idx if r["kind"] == "infographic_poster"]
    assert rows, "poster prompt was not logged"
    return rows[-1]["prompt"]


def test_sidecar_sent_body_and_logged_prompt_share_one_scheme(tmp_path):
    """U4.1 / R15 + R16: one decision, provable from three artifacts."""
    from video_agent.shorts.infographic.poster import generate_poster, palette_path

    cfg = _vida_cfg(tmp_path)
    short = tmp_path / "shorts" / "short-01_idea-01_x"
    calls = []
    asyncio.run(generate_poster(short, _poster_plan(), _recording_image_fn(calls), cfg))

    sidecar = _json.loads(palette_path(short).read_text(encoding="utf-8"))
    sent = calls[0]["prompt"]
    logged = _logged_poster_prompt(short)

    for text, label in ((sent, "sent body"), (logged, "logged prompt")):
        assert sidecar["scheme_id"] in text, f"{label} lacks the scheme id"
        assert f"variation_limited={str(sidecar['variation_limited']).lower()}" in text, label
        assert sidecar["selection_reason"] in text, label
        for role, value in sidecar["dominant_signature"].items():
            assert f"{role}={value}" in text, f"{label} lacks dominant {role}"
        for role, value in sidecar["roles"].items():
            assert f"{role} = {value}" in text, f"{label} lacks role {role}"
    # the logged prompt is exactly the sent body plus the driver's dimension line
    assert sent in logged
    assert logged.count(sidecar["scheme_id"]) == 1, "palette contract duplicated by the wrapper"


def test_retry_reproduces_the_identical_prompt_and_a_distinct_sibling_does_not(tmp_path):
    """U4.3 + U4.4."""
    from video_agent.shorts.infographic.poster import generate_poster

    cfg = _vida_cfg(tmp_path)
    shorts = tmp_path / "shorts"
    a = shorts / "short-01_idea-01_a"
    calls_a = []
    asyncio.run(generate_poster(a, _poster_plan(), _recording_image_fn(calls_a), cfg))
    asyncio.run(generate_poster(a, _poster_plan(), _recording_image_fn(calls_a), cfg))
    assert calls_a[0]["prompt"] == calls_a[1]["prompt"], "retry drifted"

    b = shorts / "short-01_idea-02_b"
    calls_b = []
    asyncio.run(generate_poster(b, _poster_plan("myth_vs_truth", "Mitos del pan"), _recording_image_fn(calls_b), cfg))
    assert calls_b[0]["prompt"] != calls_a[0]["prompt"]


def test_prompt_log_failure_is_non_fatal_and_body_still_matches_sidecar(tmp_path):
    """U4.5: logging is best-effort, but only AFTER the palette is committed."""
    from video_agent.shorts.infographic import poster as poster_mod
    from video_agent.shorts.infographic.poster import generate_poster, palette_path

    cfg = _vida_cfg(tmp_path)
    short = tmp_path / "shorts" / "short-01_idea-01_x"
    calls = []

    def exploding_log(*a, **k):
        raise OSError("log volume gone")

    original = poster_mod.safe_log_image_prompt
    poster_mod.safe_log_image_prompt = exploding_log
    try:
        try:
            asyncio.run(generate_poster(short, _poster_plan(), _recording_image_fn(calls), cfg))
        except OSError:
            pass
    finally:
        poster_mod.safe_log_image_prompt = original

    sidecar = _json.loads(palette_path(short).read_text(encoding="utf-8"))
    assert sidecar["scheme_id"], "palette must be committed before logging is attempted"
    if calls:
        assert sidecar["scheme_id"] in calls[0]["prompt"]


def test_no_named_colour_recipe_survives_in_any_format_prompt(tmp_path):
    """U4.6 / R18: bug-541/542's lexical rule still holds, and the new scheme
    metadata must not smuggle a colour NAME back into the prompt."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body

    cfg = _vida_cfg(tmp_path)
    forbidden = ("cream", "navy", "beige", "charcoal", "olive", "teal", "crimson",
                 "scarlet", "amber", "golden", "mustard", "terracotta")
    formats = ("category_grid", "numbered_tips", "warning_list", "myth_vs_truth",
               "timeline_routine", "checklist_score", "comparison", "unknown_xyz")
    for fmt in formats:
        body = build_poster_body(_poster_plan(fmt, "Tema de prueba"), cfg).lower()
        for word in forbidden:
            assert not _re.search(rf"\b{word}\b", body), f"{fmt}: prompt names the colour {word!r}"
