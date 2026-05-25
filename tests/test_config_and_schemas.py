from pathlib import Path

import json
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_sample_channel_config_matches_schema():
    schema = load_json("schemas/channel-config.schema.json")
    data = yaml.safe_load((ROOT / "configs/vida-plena-45/channel.yaml").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(data)
    assert data["channel"]["id"] == "vida-plena-45"
    assert data["audience"]["language"] == "es-ES"
    assert data["audience"]["primary_markets"] == ["ES"]
    assert data["seo"]["language"] == "es-ES"
    assert data["content_format"]["publish_schedule"]["timezone"] == "Europe/Madrid"
    assert data["content_format"]["publish_schedule"]["time_local"] == "20:00"
    assert data["locale_style"]["language_code"] == "es-ES"
    assert data["locale_style"]["target_locale"] == "Spain"
    assert data["render"]["composition"] == "ChannelVideoStandard"


def test_manual_idea_matches_schema():
    schema = load_json("schemas/manual-idea.schema.json")
    data = load_json("inputs/manual_idea.json")
    Draft202012Validator(schema).validate(data)
    assert 45 <= data["target_duration_sec"] <= 60
    assert len(data["key_points"]) >= 4


def test_render_props_schema_accepts_subtitles():
    schema = load_json("schemas/render-props.schema.json")
    data = {
        "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+", "description": ""},
        "style": {"palette": {"accent": "#F2C94C"}},
        "render": {
            "fps": 30,
            "resolution": "1920x1080",
            "duration_sec": 12,
            "subtitles": {
                "enabled": True,
                "mode": "word_highlight",
                "words_per_page": 10,
                "max_lines": 2,
                "position": "bottom",
                "offset_sec": 0,
                "font_size": 54,
                "active_scale": 1.08,
                "background_opacity": 0.58,
            },
        },
        "scenes": [],
        "audio": {"narration": None, "music": None},
        "seo": {"title": "", "description": "", "thumbnail_path": ""},
    }
    Draft202012Validator(schema).validate(data)
