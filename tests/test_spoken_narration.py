"""Tests for spoken-narration normalization + written-style warnings + prompt rules + audio QA + scene duration."""
from __future__ import annotations

from pathlib import Path

import yaml

from video_agent.qa.scene_duration import (
    EARLY_SCENE_STRICT_LIMIT_SEC,
    LONG_SCENE_WARNING_SEC,
    validate_scene_duration,
    validate_scenes_durations,
)
from video_agent.qa.spoken_text import (
    detect_written_style_narration,
    normalize_spoken_text,
)
from video_agent.qa.tts_report import audio_qa_report, build_tts_report


# ── normalize_spoken_text ─────────────────────────────────────────────────────


def test_normalize_spoken_text_preserves_paragraph_breaks():
    text = "No necesitas demostrar nada.\n\nEmpieza con poco.   Sin prisa."
    assert (
        normalize_spoken_text(text)
        == "No necesitas demostrar nada.\n\nEmpieza con poco. Sin prisa."
    )


def test_normalize_spoken_text_collapses_excess_newlines():
    assert normalize_spoken_text("A\n\n\n\nB") == "A\n\nB"


def test_normalize_spoken_text_keeps_single_newline():
    assert normalize_spoken_text("A\nB") == "A\nB"


def test_normalize_spoken_text_strips_inline_whitespace():
    assert normalize_spoken_text("hola   mundo  \t  cómo estás") == "hola mundo cómo estás"


# ── detect_written_style_narration ────────────────────────────────────────────


def test_written_style_warns_long_sentence():
    text = " ".join(["palabra"] * 35) + "."
    warnings = detect_written_style_narration(text, scene_id="scene-12")
    assert any("longer than" in w for w in warnings)


def test_written_style_allows_short_spoken_text():
    text = "No tienes que demostrar nada.\n\nEmpieza con poco."
    warnings = detect_written_style_narration(text, scene_id="scene-01")
    assert warnings == []


def test_written_style_warns_comma_heavy():
    text = "Empieza con poco, elige bien, repítelo sin prisa, y mañana lo notarás."
    warnings = detect_written_style_narration(text)
    assert any("commas" in w.lower() or "essay" in w.lower() for w in warnings)


def test_written_style_warns_long_paragraph():
    text = " ".join(["palabra"] * 80) + "."
    warnings = detect_written_style_narration(text)
    assert any("dense" in w.lower() for w in warnings)


# ── scene duration validator ──────────────────────────────────────────────────


def test_long_scene_warning():
    scene = {"id": "scene-07", "duration_sec": 21.82, "narration": "x"}
    warnings = validate_scene_duration(scene, scene_index=7)
    assert warnings


def test_early_scene_strict_warning():
    scene = {"id": "scene-03", "duration_sec": 17.5, "narration": "x"}
    warnings = validate_scene_duration(scene, scene_index=3)
    assert warnings
    assert any("early scene" in w.lower() for w in warnings)


def test_short_scene_no_warning():
    scene = {"id": "scene-15", "duration_sec": 12.0, "narration": "x"}
    assert validate_scene_duration(scene, scene_index=15) == []


def test_validate_scenes_durations_aggregates():
    scenes = [
        {"id": "scene-01", "duration_sec": 10.0},
        {"id": "scene-09", "duration_sec": 23.0},
    ]
    warnings = validate_scenes_durations(scenes)
    assert any("scene-09" in w for w in warnings)


# ── TTS pacing report ─────────────────────────────────────────────────────────


def test_tts_report_computes_wpm_and_flags_long_scenes():
    scenes = [
        {"id": "scene-01", "duration_sec": 10.0, "narration": "hola " * 20},
        {"id": "scene-02", "duration_sec": 25.0, "narration": "palabras " * 60},
    ]
    report = build_tts_report(
        scenes,
        audio_metadata={"duration_sec": 35.0},
        tts_config={"speed": 1.03, "pace_wpm": 120, "humanize": {"pause_sentence_ms": 500, "pause_paragraph_ms": 700}},
    )
    assert report["estimated_words"] == 80
    assert report["scene_count"] == 2
    assert report["total_audio_sec"] == 35.0
    assert any(ls["scene_id"] == "scene-02" for ls in report["long_scenes"])
    assert report["config"]["pause_sentence_ms"] == 500


# ── audio QA threshold ────────────────────────────────────────────────────────


def test_audio_qa_warns_clipping():
    report = audio_qa_report(integrated_lufs=-14.5, true_peak_dbtp=-0.2, bitrate_kbps=192)
    assert any("true peak" in w.lower() for w in report["warnings"])


def test_audio_qa_warns_too_quiet():
    report = audio_qa_report(integrated_lufs=-20.0, true_peak_dbtp=-2.0, bitrate_kbps=192)
    assert any("too quiet" in w.lower() or "below -18" in w.lower() for w in report["warnings"])


def test_audio_qa_warns_low_bitrate():
    report = audio_qa_report(integrated_lufs=-15.0, true_peak_dbtp=-1.5, bitrate_kbps=96)
    assert any("bitrate" in w.lower() for w in report["warnings"])


def test_audio_qa_clean_within_spec():
    report = audio_qa_report(integrated_lufs=-15.0, true_peak_dbtp=-1.5, bitrate_kbps=192)
    assert report["warnings"] == []


# ── Channel config TTS values ─────────────────────────────────────────────────


def test_channel_yaml_tts_matches_spec():
    cfg_path = Path(__file__).resolve().parent.parent / "configs" / "vida-plena-45" / "channel.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    tts = cfg["tts"]
    assert tts["speed"] == 1.03
    assert tts["pace_wpm"] == 120
    assert tts["scene_lead_in_sec"] == 0.45
    assert tts["humanize"]["pause_sentence_ms"] == 500
    assert tts["humanize"]["pause_paragraph_ms"] == 700
    music = cfg["music"]
    assert music["target_lufs"] == -15
    assert music["target_tp_dbtp"] == -1.5


# ── Prompt content checks ─────────────────────────────────────────────────────


def test_script_prompt_contains_spoken_narration_rules():
    from video_agent.operator import _chatgpt_script_prompt

    channel_cfg = {
        "content_format": {"target_duration_sec": 840},
        "tts": {"pace_wpm": 120},
        "locale_style": {"target_locale": "Spain", "language_code": "es-ES"},
    }
    idea = {"title": "test"}
    prompt = _chatgpt_script_prompt(channel_cfg, idea)
    assert "SPOKEN NARRATION RULES" in prompt
    assert "TTS PROSODY RULES" in prompt
    assert "Write for spoken Spanish" in prompt
    assert "Put important emotional sentences on their own line" in prompt
    assert "DISCLAIMER RULE" in prompt


def test_scenes_prompt_contains_rhythm_rules():
    from video_agent.operator import _chatgpt_scenes_prompt

    channel_cfg = {
        "content_format": {"target_duration_sec": 840, "scenes_count_min": 40, "scenes_count_max": 55},
        "locale_style": {"target_locale": "Spain", "language_code": "es-ES"},
    }
    script = {"channel_id": "vida-plena-45", "job_id": "test"}
    prompt = _chatgpt_scenes_prompt(channel_cfg, script)
    assert "SCENE NARRATION RHYTHM RULES" in prompt
    assert "MANDATORY ENGLISH ONLY" in prompt
    assert "Scene narration must sound natural when read aloud" in prompt


def test_scenes_batch_prompt_contains_rhythm_rules():
    from video_agent.operator import _chatgpt_scenes_batch_prompt

    channel_cfg = {
        "content_format": {"target_duration_sec": 840, "scenes_count_min": 40, "scenes_count_max": 55},
        "locale_style": {"target_locale": "Spain", "language_code": "es-ES"},
    }
    script = {"channel_id": "vida-plena-45", "job_id": "test"}
    plan = {"data": {"batches": [{"batch_index": 1}]}}
    batch = {"batch_index": 1, "batch_total": 1, "scene_start": "scene-01", "scene_end": "scene-06"}
    prompt = _chatgpt_scenes_batch_prompt(channel_cfg, script, plan, batch)
    assert "SCENE NARRATION RHYTHM RULES" in prompt
