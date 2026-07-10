"""Presenter identity is guided by TEXT, not an attached reference photo.

The reference-photo attachment was dropped (it was buggy — echoed the photo
verbatim, upload-registration failures). The prompts must no longer claim a
photo is attached, but must still describe the recurring presenter so identity
stays roughly consistent via words.
"""

from __future__ import annotations

from video_agent.persona import PERSONA_SCENE_INSTRUCTION
from video_agent.thumbnail_planner import build_thumbnail_prompt


def test_scene_instruction_has_no_attached_photo_wording():
    t = PERSONA_SCENE_INSTRUCTION.lower()
    assert "attach" not in t
    assert "reference photo" not in t
    # Still guides identity via description.
    assert "presenter" in t


def test_thumbnail_persona_lock_uses_text_not_attachment():
    plan = {
        "variant_title": "Dormir mejor durante los partidos del Mundial",
        "thumbnail_text": "5 GESTOS",
        "persona": "natural mature Mediterranean Spanish woman",
        "persona_locked": True,
    }
    p = build_thumbnail_prompt(plan).lower()
    assert "attach" not in p
    assert "reference photo" not in p
    assert "presenter" in p


def test_thumbnail_without_persona_lock_still_builds():
    plan = {"variant_title": "x", "thumbnail_text": "Y", "persona_locked": False}
    p = build_thumbnail_prompt(plan).lower()
    assert "attach" not in p


def test_shorts_asset_image_gen_sends_no_attachment():
    """The shorts assets path was the last caller still attaching the persona
    reference photo (bug-488 family) — identity must be text-only there too."""
    import inspect

    from video_agent.shorts.assets import prepare

    src = inspect.getsource(prepare._default_sync_image_gen)
    assert "attachment_path" not in src
    # Text identity instruction still applied when a persona is configured.
    assert "PERSONA_SCENE_INSTRUCTION" in src
