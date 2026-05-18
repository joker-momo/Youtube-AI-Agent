import json

from video_agent.utils.json_io import read_json, write_json
from video_agent.utils.logging import EventLogger
from video_agent.utils.paths import create_job_dir, slugify
from video_agent.utils.validation import validate_json


def test_slugify_creates_stable_slug():
    assert slugify("Vida Plena 45+ Hábitos") == "vida-plena-45-habitos"


def test_write_and_read_json(tmp_path):
    path = tmp_path / "nested" / "data.json"
    write_json(path, {"ok": True})
    assert read_json(path) == {"ok": True}


def test_validate_json_accepts_valid_payload(tmp_path):
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps({"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}), encoding="utf-8")
    validate_json({"name": "Vida"}, schema_path)


def test_event_logger_writes_jsonl(tmp_path):
    logger = EventLogger(tmp_path / "events.jsonl")
    logger.log("SCRIPTED", {"job_id": "job-test", "cost_usd": 0})
    line = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["event"] == "SCRIPTED"
    assert event["data"]["job_id"] == "job-test"


def test_create_job_dir_contains_slug_and_timestamp(tmp_path):
    job_dir = create_job_dir(tmp_path, "vida-plena-45", "Dormir mejor", timestamp="20260518-120000")
    assert job_dir.name == "20260518-120000-vida-plena-45-dormir-mejor"
    assert job_dir.exists()
