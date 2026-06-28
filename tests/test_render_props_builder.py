"""Guard for the shared long/short render_props assembly (overlap #2).

``run_pipeline`` and ``render_operator_job`` previously built near-identical
render_props dicts by hand — the duration math had to be edited in both places
(drift risk). ``_build_render_props`` centralizes it. Behaviour-preserving:
``duration_sec`` always includes branding intro+outro; ``duration_in_frames`` is
pinned only when requested (long-form), never for shorts.
"""

from __future__ import annotations

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


def test_omits_duration_in_frames_for_shorts():
    rp = _build_render_props(**_args(include_duration_in_frames=False))
    assert "duration_in_frames" not in rp["render"]


def test_passes_through_core_fields():
    rp = _build_render_props(**_args())
    assert rp["channel"] == {"id": "ch"}
    assert rp["scenes"] == [{"id": "s1", "duration_sec": 2.0}, {"id": "s2", "duration_sec": 3.0}]
    assert rp["branding"] is _BRANDING
    assert rp["seo"] == {"title": "t"}
