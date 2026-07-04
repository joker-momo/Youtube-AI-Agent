"""Tests for OKLab-based per-video accent colour resolution (bug-466).

graphic_images.py used to inject seo.topic_accent_color directly as the
brand accent, risking off-brand/clashing cards for topics whose colour
doesn't harmonize with the channel palette. resolve_topic_accent_color
blends the topic colour into the channel's own brand accent (OKLab space,
perceptually even interpolation) instead of using either colour raw.
"""
from __future__ import annotations

from video_agent.color_mix import (
    mix_hex_colors_oklab,
    resolve_topic_accent_color,
    resolve_topic_background_color,
    resolve_topic_text_color,
)

BRAND_ANCHOR = "#F5C24B"  # vida-plena-45's real channel accent (golden yellow)
TOPIC_ACCENT = "#A47A3F"  # a real per-video topic accent seen in production
BRAND_BACKGROUND = "#F6F1E8"  # vida-plena-45's real channel panel background (cream)
BRAND_TEXT = "#26332F"  # vida-plena-45's real channel text colour (near-black green)


def test_mix_ratio_zero_returns_base_color():
    result = mix_hex_colors_oklab(BRAND_ANCHOR, TOPIC_ACCENT, 0.0)
    assert result == BRAND_ANCHOR


def test_mix_ratio_one_returns_mix_color():
    result = mix_hex_colors_oklab(BRAND_ANCHOR, TOPIC_ACCENT, 1.0)
    assert result == TOPIC_ACCENT


def test_partial_mix_differs_from_both_pure_colors():
    result = mix_hex_colors_oklab(BRAND_ANCHOR, TOPIC_ACCENT, 0.3)
    assert result not in (BRAND_ANCHOR, TOPIC_ACCENT)


def test_partial_mix_differs_from_naive_rgb_average():
    """The whole point of OKLab mixing is that it does NOT behave like a
    naive per-channel RGB average -- assert the two disagree for a real
    saturated colour pair (proves OKLab space is actually used, not just a
    relabeled RGB lerp)."""
    oklab_result = mix_hex_colors_oklab(BRAND_ANCHOR, TOPIC_ACCENT, 0.5)

    def _naive_rgb_average(a: str, b: str) -> str:
        ar, ag, ab = (int(a[i : i + 2], 16) for i in (1, 3, 5))
        br, bg, bb = (int(b[i : i + 2], 16) for i in (1, 3, 5))
        return "#{:02X}{:02X}{:02X}".format(
            round((ar + br) / 2), round((ag + bg) / 2), round((ab + bb) / 2)
        )

    naive_result = _naive_rgb_average(BRAND_ANCHOR, TOPIC_ACCENT)
    assert oklab_result != naive_result


def test_resolve_topic_accent_color_with_valid_topic():
    result = resolve_topic_accent_color(BRAND_ANCHOR, TOPIC_ACCENT, mix_ratio=0.3)

    assert result["raw_topic_accent_color"] == TOPIC_ACCENT
    assert result["brand_anchor_color"] == BRAND_ANCHOR
    assert result["mix_ratio"] == 0.3
    assert result["resolved_accent_color"] not in (BRAND_ANCHOR, TOPIC_ACCENT)
    assert result["resolved_accent_color"] == mix_hex_colors_oklab(BRAND_ANCHOR, TOPIC_ACCENT, 0.3)


def test_resolve_topic_accent_color_falls_back_to_anchor_when_topic_missing():
    result = resolve_topic_accent_color(BRAND_ANCHOR, None, mix_ratio=0.3)

    assert result["raw_topic_accent_color"] is None
    assert result["resolved_accent_color"] == BRAND_ANCHOR
    assert result["mix_ratio"] == 0.0


def test_resolve_topic_accent_color_falls_back_to_anchor_when_topic_invalid():
    result = resolve_topic_accent_color(BRAND_ANCHOR, "not-a-hex-colour", mix_ratio=0.3)

    assert result["raw_topic_accent_color"] is None
    assert result["resolved_accent_color"] == BRAND_ANCHOR


def test_resolve_topic_background_color_uses_light_default_ratio():
    """bug-467: background/text now flex per-video too, reusing the SAME
    topic accent hex (no separate SEO field) but blended much lighter (12%
    default) than the accent's 30% -- background is a large-area colour, so a
    heavy blend would drift the card off the channel's recognisable cream
    tone and risks eroding text/background contrast."""
    result = resolve_topic_background_color(BRAND_BACKGROUND, TOPIC_ACCENT)

    assert result["raw_topic_accent_color"] == TOPIC_ACCENT
    assert result["brand_background_color"] == BRAND_BACKGROUND
    assert result["mix_ratio"] == 0.12
    assert result["resolved_background_color"] not in (BRAND_BACKGROUND, TOPIC_ACCENT)
    assert result["resolved_background_color"] == mix_hex_colors_oklab(BRAND_BACKGROUND, TOPIC_ACCENT, 0.12)


def test_resolve_topic_background_color_falls_back_when_topic_missing():
    result = resolve_topic_background_color(BRAND_BACKGROUND, None)

    assert result["raw_topic_accent_color"] is None
    assert result["resolved_background_color"] == BRAND_BACKGROUND
    assert result["mix_ratio"] == 0.0


def test_resolve_topic_text_color_uses_light_default_ratio():
    result = resolve_topic_text_color(BRAND_TEXT, TOPIC_ACCENT)

    assert result["raw_topic_accent_color"] == TOPIC_ACCENT
    assert result["brand_text_color"] == BRAND_TEXT
    assert result["mix_ratio"] == 0.12
    assert result["resolved_text_color"] not in (BRAND_TEXT, TOPIC_ACCENT)
    assert result["resolved_text_color"] == mix_hex_colors_oklab(BRAND_TEXT, TOPIC_ACCENT, 0.12)


def test_resolve_topic_text_color_falls_back_when_topic_missing():
    result = resolve_topic_text_color(BRAND_TEXT, None)

    assert result["raw_topic_accent_color"] is None
    assert result["resolved_text_color"] == BRAND_TEXT
    assert result["mix_ratio"] == 0.0


def test_background_and_text_blend_lighter_than_accent():
    """The whole reason background/text default to 12% instead of the
    accent's 30%: they must stay perceptually closer to the channel base
    colour than the accent does, for the same topic colour input."""
    accent_result = resolve_topic_accent_color(BRAND_ANCHOR, TOPIC_ACCENT)
    bg_result = resolve_topic_background_color(BRAND_BACKGROUND, TOPIC_ACCENT)

    from video_agent.color_mix import hex_to_oklab

    def _chroma_distance(a: str, b: str) -> float:
        la = hex_to_oklab(a)
        lb = hex_to_oklab(b)
        return sum((x - y) ** 2 for x, y in zip(la, lb)) ** 0.5

    accent_shift = _chroma_distance(BRAND_ANCHOR, accent_result["resolved_accent_color"])
    bg_shift = _chroma_distance(BRAND_BACKGROUND, bg_result["resolved_background_color"])
    assert bg_shift < accent_shift


def test_hex_roundtrip_is_stable():
    """hex -> OKLab -> hex must not drift for an already-resolved colour
    (idempotent normalisation, needed since resolve_topic_accent_color
    re-encodes the anchor even with no topic colour)."""
    from video_agent.color_mix import hex_to_oklab, oklab_to_hex

    for hex_color in (BRAND_ANCHOR, TOPIC_ACCENT, "#2F6B57", "#FFFFFF", "#000000"):
        assert oklab_to_hex(hex_to_oklab(hex_color)) == hex_color
