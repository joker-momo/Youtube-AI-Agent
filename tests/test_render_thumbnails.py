import json
from pathlib import Path
from video_agent.stages.render import build_thumbnail_commands


_SENTINEL = object()


def _make_props(tmp_path, variants=_SENTINEL):
    default_variants = [
        {"title": "Best title", "thumbnail_text": "DUERME MEJOR HOY", "score": 85},
        {"title": "Second title", "thumbnail_text": "INSOMNIO SECRETO", "score": 70},
        {"title": "Third title", "thumbnail_text": "NUNCA MÁS INSOMNIO", "score": 55},
    ]
    props = {
        "channel": {"id": "test", "name": "Test", "description": ""},
        "style": {"palette": {"background": "#fff", "primary": "#000", "secondary": "#888",
                              "accent": "#f00", "text": "#333"}},
        "render": {"fps": 30, "resolution": "1920x1080", "duration_sec": 10},
        "scenes": [{"id": "scene-01", "duration_sec": 10, "narration": "",
                    "visual_type": "stock", "visual_prompt": "", "on_screen_text": "",
                    "caption": "", "motion": "", "asset_refs": {"background": "fallback.jpg"}}],
        "audio": {"narration": None, "music": None},
        "seo": {
            "title": "Best title",
            "thumbnail_text": "DUERME MEJOR HOY",
            "description": "", "tags": [], "language": "es-ES",
            "ai_disclosure": True, "thumbnail_path": "",
            "title_variants": default_variants if variants is _SENTINEL else variants,
        },
    }
    p = tmp_path / "render_props.json"
    p.write_text(json.dumps(props), encoding="utf-8")
    return p


def test_build_thumbnail_commands_returns_three(tmp_path):
    props_path = _make_props(tmp_path)
    cmds = build_thumbnail_commands(props_path, tmp_path)
    assert len(cmds) == 3
    assert any("thumbnail_1.jpg" in c for c in cmds[0])
    assert any("thumbnail_2.jpg" in c for c in cmds[1])
    assert any("thumbnail_3.jpg" in c for c in cmds[2])


def test_build_thumbnail_commands_variant_props_differ(tmp_path):
    props_path = _make_props(tmp_path)
    cmds = build_thumbnail_commands(props_path, tmp_path)

    def get_props(cmd):
        idx = cmd.index("--props")
        return json.loads(cmd[idx + 1])

    p0, p1, p2 = get_props(cmds[0]), get_props(cmds[1]), get_props(cmds[2])
    assert p0["seo"]["thumbnail_text"] == "DUERME MEJOR HOY"
    assert p1["seo"]["thumbnail_text"] == "INSOMNIO SECRETO"
    assert p2["seo"]["thumbnail_text"] == "NUNCA MÁS INSOMNIO"


def test_build_thumbnail_commands_fallback_single_when_no_variants(tmp_path):
    props_path = _make_props(tmp_path, variants=[])
    cmds = build_thumbnail_commands(props_path, tmp_path)
    assert len(cmds) == 1
    assert any("thumbnail_1.jpg" in c for c in cmds[0])


def test_build_thumbnail_commands_caps_at_three(tmp_path):
    four_variants = [
        {"title": f"Title {i}", "thumbnail_text": f"HOOK {i}", "score": 80 - i * 10}
        for i in range(4)
    ]
    props_path = _make_props(tmp_path, variants=four_variants)
    cmds = build_thumbnail_commands(props_path, tmp_path)
    assert len(cmds) == 3
