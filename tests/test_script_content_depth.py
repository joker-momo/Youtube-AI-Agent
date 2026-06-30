"""Script content-depth rules from the competitor study (2026-06-30).

Body sections must add mechanism, analogy, an honest observed-pattern opener,
reassurance, nuance, and a signature close — WITHOUT faking medical credentials.
See docs/competitor_teardown_2026-06-29.md.
"""

from __future__ import annotations

from video_agent.operator import _chatgpt_script_prompt

CFG = {
    "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+"},
    "audience": {"language": "es-ES", "age_range": [45, 75]},
    "content_format": {"duration_sec_min": 660},
    "tts": {"pace_wpm": 120},
}


def _p() -> str:
    return _chatgpt_script_prompt(CFG, {"topic": "el mejor pan"})


def test_mechanism_and_analogy_required():
    p = _p()
    assert "MECHANISM:" in p
    assert "ANALOGY:" in p


def test_honest_opener_blocks_fake_credentials():
    p = _p()
    assert "HONEST OBSERVED-PATTERN OPENER" in p
    assert "soy médico" in p          # named as forbidden
    assert "must not pretend to be" in p


def test_reassurance_nuance_and_signature_close():
    p = _p()
    assert "REASSURANCE:" in p
    assert "no es tu culpa" in p
    assert "NUANCE OVER ABSOLUTES" in p
    assert "SIGNATURE CLOSE:" in p
