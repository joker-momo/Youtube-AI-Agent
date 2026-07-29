"""Guard for the shared long/short render_props assembly (overlap #2).

``run_pipeline`` and ``render_operator_job`` previously built near-identical
render_props dicts by hand — the duration math had to be edited in both places
(drift risk). ``_build_render_props`` centralizes it. Behaviour-preserving:
``duration_sec`` always includes branding intro+outro; ``duration_in_frames`` is
pinned only when requested (long-form), never for shorts.
"""

from __future__ import annotations

from video_agent.branding import without_long_form_branding
from video_agent.pipeline import _build_render_props

_BRANDING = {"intro_sec": 2.0, "outro_sec": 2.0}


def _args(**over):
    base = dict(
        channel_config={"channel": {"id": "ch"}, "render": {"fps": 30, "resolution": "1920x1080"}},
        style={"palette": {}},
        render_base={"fps": 30, "resolution": "1920x1080"},
        scene_doc={"scenes": [{"id": "s1", "duration_sec": 2.0}, {"id": "s2", "duration_sec": 3.0}]},
        audio={"narration": None, "music": None},
        seo={"title": "t"},
        branding=_BRANDING,
        fps=30,
        include_duration_in_frames=True,
    )
    base.update(over)
    return base


def test_duration_sec_includes_branding():
    rp = _build_render_props(**_args())
    # scenes 2.0+3.0 = 5.0, + intro 2.0 + outro 2.0 = 9.0
    assert rp["render"]["duration_sec"] == 9.0


def test_includes_duration_in_frames_when_requested():
    rp = _build_render_props(**_args(include_duration_in_frames=True))
    # intro 60 + (60+90) + outro 60 = 270
    assert rp["render"]["duration_in_frames"] == 270


def test_duration_includes_medical_disclaimer():
    branding = {
        "intro_sec": 2.0,
        "outro_sec": 2.0,
        "disclaimer_sec": 8.0,
        "disclaimer_video_path": "branding/ch/disclaimer.mp4",
    }
    rp = _build_render_props(**_args(branding=branding))

    # scenes 5s + intro 2s + disclaimer 8s + outro 2s
    assert rp["render"]["duration_sec"] == 17.0
    assert rp["render"]["duration_in_frames"] == 510


def test_legacy_short_omits_long_form_branding_and_duration_frames():
    branding = {
        "intro_sec": 0.0,
        "outro_sec": 0.0,
        "disclaimer_sec": 8.0,
        "disclaimer_video_path": "branding/ch/disclaimer.mp4",
    }
    rp = _build_render_props(
        **_args(
            include_duration_in_frames=False,
            branding=without_long_form_branding(branding),
        )
    )
    assert "duration_in_frames" not in rp["render"]
    assert rp["render"]["duration_sec"] == 5.0
    assert rp["branding"]["disclaimer_sec"] == 0.0
    assert rp["branding"]["disclaimer_video_path"] is None


def test_passes_through_core_fields():
    rp = _build_render_props(**_args())
    assert rp["channel"] == {"id": "ch"}
    assert rp["scenes"] == [{"id": "s1", "duration_sec": 2.0}, {"id": "s2", "duration_sec": 3.0}]
    assert rp["branding"] is _BRANDING
    assert rp["seo"] == {"title": "t"}
