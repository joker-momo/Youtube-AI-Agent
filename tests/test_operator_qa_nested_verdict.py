"""Gemini QA sometimes echoes the FULL artifact (which natively carries a ``qa``
field) instead of a bare ``{verdict, ...}`` object, nesting the verdict under
``qa``. ``_normalize_operator_qa`` must find the verdict either way — otherwise
the top-level lookup misses it → verdict "MISSING" → endless QA rework → the
/run-all pipeline stalls at <artifact>_qa (observed on a live UI run, bug-392).
"""

from __future__ import annotations

import pytest

from video_agent.operator import _normalize_operator_qa


def test_verdict_nested_under_qa_is_found():
    parsed = {
        "channel_id": "vida-plena-45",
        "hook": "…",
        "sections": [],
        "narration": "n",
        "cta": "c",
        "qa": {"verdict": "PASS", "issues": [], "required_changes": [], "scores": {}},
    }
    out = _normalize_operator_qa("script", parsed)
    assert out["verdict"] == "PASS"
    assert out["issues"] == []


def test_top_level_verdict_still_works():
    out = _normalize_operator_qa(
        "script", {"verdict": "PASS", "issues": [], "required_changes": [], "scores": {}}
    )
    assert out["verdict"] == "PASS"


def test_nested_non_pass_still_raises():
    parsed = {"qa": {"verdict": "NEEDS_REWORK", "issues": ["fix x"]}}
    with pytest.raises(ValueError):
        _normalize_operator_qa("script", parsed)


def test_top_level_verdict_wins_when_both_present():
    # An explicit top-level verdict takes precedence over a nested qa block.
    parsed = {"verdict": "PASS", "issues": [], "qa": {"verdict": "NEEDS_REWORK"}}
    out = _normalize_operator_qa("script", parsed)
    assert out["verdict"] == "PASS"
