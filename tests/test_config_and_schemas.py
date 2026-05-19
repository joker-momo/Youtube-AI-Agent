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
    assert data["audience"]["language"] == "es-419"
    assert data["seo"]["language"] == "es-419"
    assert data["render"]["composition"] == "ChannelVideoStandard"


def test_manual_idea_matches_schema():
    schema = load_json("schemas/manual-idea.schema.json")
    data = load_json("inputs/manual_idea.json")
    Draft202012Validator(schema).validate(data)
    assert 45 <= data["target_duration_sec"] <= 60
    assert len(data["key_points"]) >= 4
