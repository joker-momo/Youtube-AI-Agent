"""bug-495 reopen (bridge 20260707): the script QA gate must DETERMINISTICALLY
enforce the channel-config-derived minimum narration length, independent of
Gemini's (historically stale) opinion — and must NOT reject an on-floor script.
"""
import json

from video_agent.orchestrator.stages.script import (
    _enforce_script_length_qa,
    _narration_word_count,
)


def _channel_yaml(tmp_path, duration_sec_min=660, pace_wpm=120):
    p = tmp_path / "channel.yaml"
    p.write_text(
        f"content_format:\n  duration_sec_min: {duration_sec_min}\n"
        f"tts:\n  pace_wpm: {pace_wpm}\n",
        encoding="utf-8",
    )
    return p


def _write_script(job_dir, words):
    (job_dir / "json").mkdir(parents=True, exist_ok=True)
    (job_dir / "json" / "script.json").write_text(
        json.dumps({"narration": " ".join(["palabra"] * words)}), encoding="utf-8"
    )


def _qa_output(job_dir, verdict="PASS"):
    p = job_dir / "operator" / "gemini" / "script_qa.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"artifact": "script", "verdict": verdict, "issues": [], "required_changes": []}
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p, payload


def test_floor_is_derived_from_channel_config():
    from video_agent.orchestrator.briefing import _script_length_floor
    f = _script_length_floor({"content_format": {"duration_sec_min": 660}, "tts": {"pace_wpm": 120}})
    assert f["script_word_floor"] == 1320  # 660/60 * 120


def test_on_floor_script_is_NOT_rejected_by_the_gate(tmp_path):
    # The exact production case: ~1700 words, floor 1320 -> must pass untouched.
    job = tmp_path / "job"
    _write_script(job, 1700)
    qa_path, payload = _qa_output(job, verdict="PASS")
    _enforce_script_length_qa(job, qa_path, payload, channel_path=_channel_yaml(tmp_path))
    after = json.loads(qa_path.read_text(encoding="utf-8"))
    assert after["verdict"] == "PASS", "an on-contract script was rejected for length"


def test_below_floor_script_is_deterministically_reworked(tmp_path):
    job = tmp_path / "job"
    _write_script(job, 900)  # below 1320
    qa_path, payload = _qa_output(job, verdict="PASS")  # even if Gemini was lenient
    _enforce_script_length_qa(job, qa_path, payload, channel_path=_channel_yaml(tmp_path))
    after = json.loads(qa_path.read_text(encoding="utf-8"))
    assert after["verdict"] == "NEEDS_REWORK"
    assert any("900 words" in i and "1320" in i for i in after["issues"]), after["issues"]
    assert after["required_changes"], "must tell the model what to fix"


def test_no_upper_bound_a_very_long_script_passes(tmp_path):
    job = tmp_path / "job"
    _write_script(job, 5000)
    qa_path, payload = _qa_output(job, verdict="PASS")
    _enforce_script_length_qa(job, qa_path, payload, channel_path=_channel_yaml(tmp_path))
    assert json.loads(qa_path.read_text(encoding="utf-8"))["verdict"] == "PASS"


def test_floor_scales_with_a_different_channel_duration(tmp_path):
    # A 20-min channel floor (1200s) => 2400-word floor; a 1700-word script now fails.
    job = tmp_path / "job"
    _write_script(job, 1700)
    qa_path, payload = _qa_output(job, verdict="PASS")
    _enforce_script_length_qa(job, qa_path, payload, channel_path=_channel_yaml(tmp_path, duration_sec_min=1200))
    assert json.loads(qa_path.read_text(encoding="utf-8"))["verdict"] == "NEEDS_REWORK"


def test_word_count_helper():
    assert _narration_word_count({"narration": "hola mundo feliz"}) == 3
    assert _narration_word_count({}) == 0
