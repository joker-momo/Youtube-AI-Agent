"""Perceptual colour mixing (OKLab) for per-video accent resolution (bug-466).

``graphic_images.py`` used to inject a per-video ``seo.topic_accent_color``
directly as the brand accent — for a topic colour that clashes with the
channel palette, that risk makes graphics look off-brand or visually jarring.
Blending the topic colour with the channel's own brand accent instead
(``resolved_accent_color``) keeps every video recognisably on-brand while
still giving each topic its own highlight.

OKLab (Björn Ottosson, https://bottosson.github.io/posts/oklab/) is used
because linear interpolation in sRGB/RGB is perceptually uneven (a 50/50 RGB
mix of two saturated colours often looks duller or shifts hue unexpectedly);
OKLab's axes are designed so linear interpolation between two points tracks
much closer to how the blend actually looks.
"""
from __future__ import annotations

import re

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _hex_to_srgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.strip().lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return r, g, b


def _srgb_channel_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_channel_to_srgb(c: float) -> float:
    c = min(1.0, max(0.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def _linear_srgb_to_oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def _oklab_to_linear_srgb(L: float, a: float, b_: float) -> tuple[float, float, float]:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b_
    m_ = L - 0.1055613458 * a - 0.0638541728 * b_
    s_ = L - 0.0894841775 * a - 1.2914855480 * b_
    l, m, s = l_**3, m_**3, s_**3
    return (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )


def hex_to_oklab(hex_color: str) -> tuple[float, float, float]:
    r, g, b = _hex_to_srgb(hex_color)
    return _linear_srgb_to_oklab(
        _srgb_channel_to_linear(r), _srgb_channel_to_linear(g), _srgb_channel_to_linear(b)
    )


def oklab_to_hex(lab: tuple[float, float, float]) -> str:
    r, g, b = _oklab_to_linear_srgb(*lab)
    r, g, b = _linear_channel_to_srgb(r), _linear_channel_to_srgb(g), _linear_channel_to_srgb(b)
    return "#{:02X}{:02X}{:02X}".format(
        round(min(1.0, max(0.0, r)) * 255),
        round(min(1.0, max(0.0, g)) * 255),
        round(min(1.0, max(0.0, b)) * 255),
    )


def mix_hex_colors_oklab(base_hex: str, mix_hex: str, mix_weight: float) -> str:
    """Blend ``mix_hex`` into ``base_hex`` in OKLab space.

    ``mix_weight`` is the weight of ``mix_hex`` in [0, 1] — e.g. 0.3 means
    "70% base, 30% mix". ``mix_weight=0`` returns ``base_hex`` unchanged
    (normalised casing); ``mix_weight=1`` returns ``mix_hex``.
    """
    w = min(1.0, max(0.0, float(mix_weight)))
    base_lab = hex_to_oklab(base_hex)
    mix_lab = hex_to_oklab(mix_hex)
    blended = tuple(bl * (1 - w) + ml * w for bl, ml in zip(base_lab, mix_lab))
    return oklab_to_hex(blended)


def _resolve_topic_blend(
    brand_base_color: str, raw_topic_color: str | None, *, mix_ratio: float
) -> tuple[str | None, str, str, float]:
    """Shared blend logic behind every ``resolve_topic_*_color`` wrapper.

    Returns ``(valid_topic, normalised_base, resolved, effective_mix_ratio)``.
    """
    valid_topic = raw_topic_color if (
        isinstance(raw_topic_color, str) and _HEX_RE.match(raw_topic_color.strip())
    ) else None
    normalised_base = oklab_to_hex(hex_to_oklab(brand_base_color))
    resolved = (
        mix_hex_colors_oklab(brand_base_color, valid_topic, mix_ratio)
        if valid_topic
        else normalised_base
    )
    return valid_topic, normalised_base, resolved, (mix_ratio if valid_topic else 0.0)


def resolve_topic_accent_color(
    brand_anchor_color: str, raw_topic_accent_color: str | None, *, mix_ratio: float = 0.3
) -> dict[str, object]:
    """Resolve a per-video accent from the channel's brand anchor + a raw
    per-topic accent hex, blended 70% anchor / 30% topic (default) in OKLab.

    Returns a dict with every input/output needed for audit/regeneration:
    ``raw_topic_accent_color``, ``brand_anchor_color``, ``resolved_accent_color``,
    ``mix_ratio``. When ``raw_topic_accent_color`` is missing/invalid, the
    brand anchor is used unchanged (``resolved_accent_color == brand_anchor_color``)
    and ``raw_topic_accent_color`` is recorded as ``None``.
    """
    valid_topic, normalised_base, resolved, effective_ratio = _resolve_topic_blend(
        brand_anchor_color, raw_topic_accent_color, mix_ratio=mix_ratio
    )
    return {
        "raw_topic_accent_color": valid_topic,
        "brand_anchor_color": normalised_base,
        "resolved_accent_color": resolved,
        "mix_ratio": effective_ratio,
    }


def resolve_topic_background_color(
    brand_background_color: str, raw_topic_accent_color: str | None, *, mix_ratio: float = 0.12
) -> dict[str, object]:
    """Resolve a per-video panel background from the channel's brand background
    + the same per-topic accent hex used for ``resolve_topic_accent_color``,
    blended at a much lighter ratio (default 12%) than the accent's 30% —
    background is a large-area colour, so a heavy blend would drift the card
    away from the channel's recognisable cream/base tone and risks eroding
    text/background contrast.
    """
    valid_topic, normalised_base, resolved, effective_ratio = _resolve_topic_blend(
        brand_background_color, raw_topic_accent_color, mix_ratio=mix_ratio
    )
    return {
        "raw_topic_accent_color": valid_topic,
        "brand_background_color": normalised_base,
        "resolved_background_color": resolved,
        "mix_ratio": effective_ratio,
    }


def resolve_topic_text_color(
    brand_text_color: str, raw_topic_accent_color: str | None, *, mix_ratio: float = 0.12
) -> dict[str, object]:
    """Resolve a per-video text colour from the channel's brand text colour +
    the same per-topic accent hex, blended at a light ratio (default 12%) —
    same large-area/contrast-risk reasoning as ``resolve_topic_background_color``.
    """
    valid_topic, normalised_base, resolved, effective_ratio = _resolve_topic_blend(
        brand_text_color, raw_topic_accent_color, mix_ratio=mix_ratio
    )
    return {
        "raw_topic_accent_color": valid_topic,
        "brand_text_color": normalised_base,
        "resolved_text_color": resolved,
        "mix_ratio": effective_ratio,
    }
