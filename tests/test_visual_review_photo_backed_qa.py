"""Regression: bug-455 follow-up (Codex requested action #2) -- surface
photo-backed long-form backgrounds in visual_review.json so an operator can
see which scenes ended up on a still photo instead of real video footage.

The asset-selection cascade (assets/service.py) already tries stock VIDEO
before falling back to a stock PHOTO at every strictness tier, and the
renderer (ChannelVideo.tsx, bug-455) already avoids treating a photo-backed
mp4 as a static slide (Ken Burns motion / brand-gradient hybrid background
instead of a frozen frame). What was still missing was VISIBILITY: nothing
told the operator which scenes actually landed on a photo. _scene_visual_issues
now flags this as a WARNING (never blocks render -- a photo fallback can be a
legitimate, on-topic choice when Pexels has no matching video for a niche
topic) whenever a non-graphic-layout scene's background resolved to an image.
"""
from __future__ import annotations

from video_agent.pipeline import _scene_visual_issues


def _base_scene(**overrides) -> dict:
    scene = {
        "scene_id": "scene-01",
        "layout": "subtitle",
        "source": "asset_library",
        "provider": "pexels",
        "provider_asset_id": "12345",
        "media_kind": "video",
        "selection": {"score": 80, "asset_match_status": "strong_match"},
    }
    scene.update(overrides)
    return scene


def _issue_types(scene: dict) -> set[str]:
    return {issue["type"] for issue in _scene_visual_issues(scene)}


def test_photo_backed_non_graphic_scene_is_flagged():
    scene = _base_scene(media_kind="image", layout="subtitle")

    issues = _scene_visual_issues(scene)

    photo_issues = [i for i in issues if i["type"] == "PHOTO_BACKED_BACKGROUND"]
    assert len(photo_issues) == 1
    assert photo_issues[0]["severity"] == "warning"  # never blocks render


def test_video_backed_scene_is_not_flagged():
    scene = _base_scene(media_kind="video", layout="subtitle")

    assert "PHOTO_BACKED_BACKGROUND" not in _issue_types(scene)


def test_photo_backed_graphic_layout_scene_is_exempt():
    """Graphic scenes (checklist/warning/quote/cta/...) show a generated card
    as the primary visual -- the living background behind it is secondary, so
    a photo there isn't the same quality concern as a full-bleed photo scene."""
    for layout in ("checklist", "warning", "quote", "cta", "hook"):
        scene = _base_scene(media_kind="image", layout=layout)
        assert "PHOTO_BACKED_BACKGROUND" not in _issue_types(scene), layout


def test_photo_backed_scene_never_escalates_to_error():
    """A photo fallback can be a legitimate on-topic choice -- must never be
    severity=error (which would block the render via _validate_visual_review)."""
    scene = _base_scene(media_kind="image", layout="subtitle")

    issues = [i for i in _scene_visual_issues(scene) if i["type"] == "PHOTO_BACKED_BACKGROUND"]
    assert all(i["severity"] != "error" for i in issues)


def test_missing_media_kind_is_not_flagged():
    """Older jobs / non-stock sources without media_kind plumbed through must
    not false-positive."""
    scene = _base_scene(media_kind=None, layout="subtitle")

    assert "PHOTO_BACKED_BACKGROUND" not in _issue_types(scene)


# ── GRAPHIC_CARD_MISSING (Codex 20260704-130051) ────────────────────────────
# A graphic-layout scene whose designed card never materialized (image gen
# failed, late-recovery sweep found nothing) silently downgraded to a plain
# video background and QA PASSed with zero signal. _scene_visual_issues now
# flags it as a WARNING (never blocks -- the fallback is real footage).


def test_graphic_layout_scene_without_card_is_flagged():
    scene = _base_scene(
        layout="recipe_snapshot",
        graphic={"needed": True, "failed": True, "error": "browser-worker chatgpt/image request failed: "},
    )

    issues = [i for i in _scene_visual_issues(scene) if i["type"] == "GRAPHIC_CARD_MISSING"]
    assert len(issues) == 1
    assert issues[0]["severity"] == "warning"
    assert "request failed" in issues[0]["message"]


def test_graphic_layout_scene_with_card_is_not_flagged():
    scene = _base_scene(
        layout="recipe_snapshot",
        graphic={"needed": True, "image_ref": "jobs/j1/assets/graphic-scene-01.png"},
    )
    assert "GRAPHIC_CARD_MISSING" not in _issue_types(scene)


def test_graphic_layout_scene_opted_out_is_not_flagged():
    scene = _base_scene(layout="recipe_snapshot", graphic={"needed": False})
    assert "GRAPHIC_CARD_MISSING" not in _issue_types(scene)


def test_graphic_card_missing_without_failure_marker_still_flagged():
    """Pre-fix jobs have graphic=null on the lost-card scene (the failure path
    never stamped anything) -- those must still be flagged."""
    scene = _base_scene(layout="recipe_snapshot", graphic=None)
    issues = [i for i in _scene_visual_issues(scene) if i["type"] == "GRAPHIC_CARD_MISSING"]
    assert len(issues) == 1


def test_non_graphic_layout_scene_never_flagged_for_missing_card():
    scene = _base_scene(layout="subtitle", graphic=None)
    assert "GRAPHIC_CARD_MISSING" not in _issue_types(scene)


def test_graphic_card_missing_never_escalates_to_error():
    scene = _base_scene(layout="recipe_snapshot", graphic={"needed": True, "failed": True})
    issues = [i for i in _scene_visual_issues(scene) if i["type"] == "GRAPHIC_CARD_MISSING"]
    assert all(i["severity"] != "error" for i in issues)
