"""Presenter identity across AI-generated imagery (persona.py)."""

from __future__ import annotations

from video_agent.persona import PERSONA_SCENE_INSTRUCTION, resolve_persona_reference


def test_resolves_from_config_dict():
    cfg = {"thumbnail": {"persona_reference": "configs/vida-plena-45/persona/thumbnail_face.jpeg"}}
    assert resolve_persona_reference(cfg).endswith("thumbnail_face.jpeg")


def test_empty_when_unconfigured_or_missing_file():
    assert resolve_persona_reference({}) == ""
    assert resolve_persona_reference({"thumbnail": {"persona_reference": "configs/nope.jpg"}}) == ""


def test_env_fallback(monkeypatch, tmp_path):
    cfg = tmp_path / "ch.yaml"
    cfg.write_text('thumbnail:\n  persona_reference: "configs/vida-plena-45/persona/thumbnail_face.jpeg"\n')
    monkeypatch.setenv("CHANNEL_CONFIG", str(cfg))
    assert resolve_persona_reference().endswith("thumbnail_face.jpeg")
    monkeypatch.setenv("CHANNEL_CONFIG", "/nonexistent.yaml")
    assert resolve_persona_reference() == ""


def test_instruction_is_conditional():
    # Must instruct identity WHEN a person appears, and explicitly allow
    # person-less scenes to ignore the reference (no forced presenter).
    assert "if this image shows a person" in PERSONA_SCENE_INSTRUCTION
    assert "same woman" in PERSONA_SCENE_INSTRUCTION
    assert "do NOT add a person" in PERSONA_SCENE_INSTRUCTION
