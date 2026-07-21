"""Per-format 9:16 infographic poster prompts (AI-only, text baked in).

Color policy (bug-541): this module hardcodes NO color names or hex values.
Every appearance instruction binds a semantic role (canvas, headline_1,
positive, negative, …) to an exact hex resolved from the channel's style DNA.

Variation policy (bug-546): role assignment alone is NOT variation. bug-541
pinned the canvas and permuted three accents, but the contrast filter then handed
every large role back to the same two readable colours — two posters came out
byte-different and eye-identical. Schemes are therefore enumerated CANVAS-FIRST
and compared in CIELAB on the roles that actually cover area, so consecutive
sibling Shorts are visibly different rather than technically different.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from video_agent.browser_worker.drivers.chatgpt_image import build_image_gen_prompt
from video_agent.style_dna import DEFAULT_STYLE, is_valid_hex, load_style_dna_from_config

# Palette entries, in canonical enumeration order. Every entry — canvas included —
# is a candidate for every role; the contrast gates decide what is allowed.
_CHROMATIC_KEYS = ("primary", "secondary", "accent")
_NEUTRAL_KEYS = ("background", "text")

# Persisted palette-decision contract (sidecar + in-memory).
POSTER_PALETTE_SCHEMA = "shorts_poster_palette.v1"


def _validated_palette(channel_config: dict[str, Any] | None) -> dict[str, str]:
    """Canonical palette from style DNA; centralized neutral fallback per key.

    Validates the CONTAINER as well as each key: a truthy but non-mapping
    ``palette`` (e.g. ``{"palette": ["#112233"]}``) must fall back, never raise
    into poster generation (R2 no-crash).
    """
    dna = load_style_dna_from_config(channel_config) or {}
    raw = dna.get("palette") if isinstance(dna, dict) else None
    if not isinstance(raw, dict):
        raw = {}
    fallback = DEFAULT_STYLE["palette"]
    out: dict[str, str] = {}
    for key in (*_NEUTRAL_KEYS, *_CHROMATIC_KEYS):
        value = raw.get(key)
        out[key] = value.strip() if is_valid_hex(value) else fallback[key]
    return out


def _content_fingerprint(plan: dict[str, Any]) -> str:
    """Stable digest of the CONTENT-bearing plan fields only.

    Uses sha256 over a canonical JSON projection — never Python's randomized
    ``hash()``, no timestamps and no retry counters — so the same poster keeps
    one palette across QA retries while different ideas rotate roles (R3/R8).
    """
    items = [
        {
            "label": str(i.get("label") or "").strip(),
            "note": str(i.get("note") or "").strip(),
            "time": str(i.get("time") or "").strip(),
            "group": str(i.get("group") or "").strip(),
        }
        for i in _labels(plan)
    ]
    payload = {
        "format": str(plan.get("poster_format") or "").strip(),
        "title": str(plan.get("title") or "").strip(),
        "subtitle": str(plan.get("subtitle") or "").strip(),
        "hook_line": str(plan.get("hook_line") or "").strip(),
        "items": items,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _srgb_channel(value: float) -> float:
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _linear_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.strip().lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return _srgb_channel(r), _srgb_channel(g), _srgb_channel(b)


def _relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of ``#RRGGBB``."""
    r, g, b = _linear_rgb(hex_color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# CIE standard illuminant D65 white point (the reference sRGB is defined against).
_D65_WHITE = (0.95047, 1.00000, 1.08883)
_LAB_EPSILON = (6 / 29) ** 3
_LAB_KAPPA = 3 * (6 / 29) ** 2


def _lab_f(t: float) -> float:
    return t ** (1 / 3) if t > _LAB_EPSILON else t / _LAB_KAPPA + 4 / 29


def _hex_to_lab(hex_color: str) -> tuple[float, float, float]:
    """CIELAB (D65) coordinates of ``#RRGGBB``.

    WCAG contrast answers "can this be read?"; it says nothing about whether two
    posters LOOK different — #2F6B57 and #26332F sit 5.5:1 apart from the canvas
    yet read as the same dark mass. CIELAB is perceptually uniform, so distances
    in it track what a viewer actually sees (bug-546).
    """
    r, g, b = _linear_rgb(hex_color)
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b
    fx, fy, fz = (_lab_f(c / w) for c, w in zip((x, y, z), _D65_WHITE, strict=True))
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _delta_e76(a: str, b: str) -> float:
    """CIE76 perceptual distance between two hex colors (0 = identical)."""
    la, aa, ba = _hex_to_lab(a)
    lb, ab, bb = _hex_to_lab(b)
    return ((la - lb) ** 2 + (aa - ab) ** 2 + (ba - bb) ** 2) ** 0.5


def _contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two hex colors (1.0 … 21.0)."""
    la, lb = _relative_luminance(a), _relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


# WCAG AA for LARGE/bold text and graphical objects (headlines, state marks).
# Poster headlines and check/cross marks are large by construction, so 3.0 is the
# correct bar; anything below is unreadable at phone size (bug-541 round 2:
# #F5C24B on #F6F1E8 measured 1.47:1).
_MIN_LARGE_CONTRAST = 3.0
# WCAG AA for normal-size text: item notes and lettering inside a filled badge are
# NOT large by construction, so they need the stricter bar (bug-546 KTD3).
_MIN_BODY_CONTRAST = 4.5

# ── perceptual variation gates (bug-546 R7 / KTD8) ─────────────────────────────
# A materially different canvas is the strongest and cheapest signal that two
# posters are different, because the canvas is most of the pixels.
_MIN_CANVAS_DELTA_E = 15.0
# When no such canvas exists, the remaining dominant mass must move this far.
_MIN_DOMINANT_DELTA_E = 18.0
_MIN_CHANGED_DOMINANT_POSITIONS = 3

# Dominant roles carried by EVERY format: the canvas plus the two headline lines.
_UNIVERSAL_DOMINANT_ROLES = ("canvas", "headline_1", "headline_2")
# Roles that actually cover meaningful area in each format. A role a format never
# renders must not manufacture fake distance (R6).
_FORMAT_ACTIVE_ROLES: dict[str, tuple[str, ...]] = {
    "category_grid": ("divider_accent",),
    "numbered_tips": ("badge_fill", "divider_accent"),
    "warning_list": ("negative", "divider_accent"),
    "myth_vs_truth": ("negative", "positive", "divider_accent"),
    "timeline_routine": ("badge_fill", "divider_accent"),
    "checklist_score": ("badge_fill", "divider_accent"),
    "comparison": ("positive", "negative", "divider_accent"),
}
# Relative on-poster area, used to weight the dominance distance.
_CANVAS_AREA_WEIGHT = 4
_ROLE_AREA_WEIGHT = 2


def _active_dominant_roles(poster_format: str) -> tuple[str, ...]:
    return _UNIVERSAL_DOMINANT_ROLES + _FORMAT_ACTIVE_ROLES.get(poster_format, ())


def dominant_signature(roles: dict[str, str], poster_format: str) -> dict[str, str]:
    """The role->hex slots that decide what a poster LOOKS like (R6).

    Deliberately excludes ``body_text`` and the ``*_text`` lettering roles: they
    are contrast-derived consequences of the dominant choices, not independent
    design decisions, and including them would let a poster claim variation it
    does not visibly have.
    """
    return {role: roles[role] for role in _active_dominant_roles(poster_format)}


def _slot_weight(slot: str) -> int:
    return _CANVAS_AREA_WEIGHT if slot == "canvas" else _ROLE_AREA_WEIGHT


def _aligned_slots(
    previous: dict[str, str], candidate: dict[str, str]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Compare the UNION of both signatures' slots (the two posters may use
    different formats). A slot one format does not render falls back to that
    poster's canvas — the colour actually occupying that area (R6/matrix)."""
    slots = tuple(sorted(set(previous) | set(candidate)))
    prev_values = tuple(previous.get(s, previous["canvas"]) for s in slots)
    cand_values = tuple(candidate.get(s, candidate["canvas"]) for s in slots)
    return slots, prev_values, cand_values


def dominance_distance(previous: dict[str, str], candidate: dict[str, str]) -> dict[str, Any]:
    """Structural + perceptual distance between two dominant signatures (R7)."""
    slots, prev_values, cand_values = _aligned_slots(previous, candidate)
    weights = [_slot_weight(s) for s in slots]
    changed = sum(1 for a, b in zip(prev_values, cand_values, strict=True) if a != b)
    # "Colour mass", not "role order": a scheme that shuffles the same colours
    # between roles keeps the same weighted multiset and must not count (KTD8).
    prev_mass = sorted(v for v, w in zip(prev_values, weights, strict=True) for _ in range(w))
    cand_mass = sorted(v for v, w in zip(cand_values, weights, strict=True) for _ in range(w))
    weighted = sum(
        w * _delta_e76(a, b) for w, a, b in zip(weights, prev_values, cand_values, strict=True)
    ) / sum(weights)
    return {
        "canvas_delta_e": _delta_e76(previous["canvas"], candidate["canvas"]),
        "changed_positions": changed,
        "mass_changed": prev_mass != cand_mass,
        "weighted_delta_e": weighted,
    }


def _canvas_is_distinct(previous: dict[str, str], candidate: dict[str, str]) -> bool:
    return _delta_e76(previous["canvas"], candidate["canvas"]) >= _MIN_CANVAS_DELTA_E


def _dominance_is_distinct(previous: dict[str, str], candidate: dict[str, str]) -> bool:
    """R7 fallback tier: same-ish canvas, so the rest of the mass must move."""
    d = dominance_distance(previous, candidate)
    return (
        d["changed_positions"] >= _MIN_CHANGED_DOMINANT_POSITIONS
        and d["mass_changed"]
        and d["weighted_delta_e"] >= _MIN_DOMINANT_DELTA_E
    )


def _best_foreground_on(fill: str, candidates: tuple[str, ...]) -> str:
    """Highest-contrast palette candidate for text over ``fill`` (deterministic:
    ties resolve to the first candidate in the given order) — KTD3."""
    return max(candidates, key=lambda c: _contrast_ratio(c, fill))


# Roles walked cyclically over the readable pool to build one arrangement. The
# ORDER matters: adjacent entries are guaranteed different whenever the pool holds
# two or more colours, which is what keeps the two-tone headline and the
# positive/negative pair from collapsing onto one colour.
_ROTATING_ROLES = (
    "headline_1",
    "headline_2",
    "positive",
    "negative",
    "badge_fill",
    "divider_accent",
)
# Fills that carry lettering, and the role holding that lettering's colour.
_FILL_TEXT_ROLES = (
    ("badge_fill", "badge_text"),
    ("positive", "positive_text"),
    ("negative", "negative_text"),
)
# At most two arrangements per canvas, family capped — enumerate the useful
# corners of the space, never the Cartesian product (KTD9).
_ARRANGEMENTS_PER_CANVAS = 2
_MAX_CANDIDATE_SCHEMES = 10


def _ordered_palette_values(palette: dict[str, str]) -> tuple[str, ...]:
    """Unique palette values in canonical key order (stable across processes and
    dict orderings). Duplicate hexes collapse instead of faking variation."""
    out: list[str] = []
    for key in (*_NEUTRAL_KEYS, *_CHROMATIC_KEYS):
        value = palette[key]
        if value not in out:
            out.append(value)
    return tuple(out)


def _pool_on(values: tuple[str, ...], canvas: str, minimum: float) -> tuple[str, ...]:
    """Palette values readable on ``canvas`` at ``minimum``, most-readable first
    (hex breaks ties, so ordering never depends on dict or float wobble)."""
    ok = [v for v in values if v != canvas and _contrast_ratio(v, canvas) >= minimum]
    return tuple(sorted(ok, key=lambda v: (-_contrast_ratio(v, canvas), v)))


def _scheme_roles(values: tuple[str, ...], canvas: str, offset: int) -> dict[str, str] | None:
    """One complete contrast-valid role mapping for ``canvas``, or None.

    Eligibility is deliberately format-INDEPENDENT and a strict superset of every
    per-format gate in the plan's dominance matrix: every rotating role clears the
    large bar on the canvas, body text clears the body bar, and each filled shape
    can be lettered legibly. A canvas offering fewer than two readable colours is
    rejected outright — the header contract demands a real colour change between
    the two title lines, which one colour cannot express.
    """
    foreground = _pool_on(values, canvas, _MIN_LARGE_CONTRAST)
    body = _pool_on(values, canvas, _MIN_BODY_CONTRAST)
    if len(foreground) < 2 or not body:
        return None
    start = offset % len(foreground)
    rotated = foreground[start:] + foreground[:start]
    roles: dict[str, str] = {"canvas": canvas, "body_text": body[0]}
    for index, role in enumerate(_ROTATING_ROLES):
        roles[role] = rotated[index % len(rotated)]
    for fill_role, text_role in _FILL_TEXT_ROLES:
        lettering = _best_foreground_on(roles[fill_role], values)
        if _contrast_ratio(lettering, roles[fill_role]) < _MIN_BODY_CONTRAST:
            return None
        roles[text_role] = lettering
    return roles


def _stable_digest(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _scheme_id(roles: dict[str, str]) -> str:
    return _stable_digest(roles)[:12]


def palette_fingerprint(palette: dict[str, str]) -> str:
    """Identity of the palette a scheme was derived from. A sidecar written for a
    different Style DNA must never survive a config change (R14)."""
    return _stable_digest(palette)[:12]


def _contrast_evidence(roles: dict[str, str], poster_format: str) -> dict[str, float]:
    """Every foreground/surface pair this format actually renders, with the ratio
    measured on the REAL surface — the evidence a reviewer (or a sidecar
    revalidation) can recheck without rerunning selection (R13)."""
    pairs = [("body_text", "canvas"), ("headline_1", "canvas"), ("headline_2", "canvas")]
    for role in _FORMAT_ACTIVE_ROLES.get(poster_format, ()):
        pairs.append((role, "canvas"))
    # The subtitle pill is part of the shared header, so its lettering is audited
    # for every format, not only the badge-bearing ones.
    for fill_role, text_role in _FILL_TEXT_ROLES:
        if fill_role == "badge_fill" or fill_role in _FORMAT_ACTIVE_ROLES.get(poster_format, ()):
            pairs.append((text_role, fill_role))
    return {
        f"{fg}_on_{surface}": round(_contrast_ratio(roles[fg], roles[surface]), 2)
        for fg, surface in pairs
    }


def _minimum_for_pair(pair_key: str) -> float:
    """Which WCAG bar a recorded evidence pair must clear."""
    foreground = pair_key.split("_on_")[0]
    return _MIN_BODY_CONTRAST if foreground.endswith("_text") else _MIN_LARGE_CONTRAST


# Canvases are drawn from the palette's STRUCTURAL entries first. A style DNA
# calls a colour `accent`/`secondary` precisely because it is meant to punctuate,
# not to cover the poster; flooding a 9:16 field with an accent is palette drift
# and reads as a discount flyer rather than the calm, trustworthy tone this
# audience is here for. background/text/primary already give a light, a dark and a
# deep brand canvas — which is the light/dark spread this fix needs.
_PREFERRED_CANVAS_KEYS = ("background", "text", "primary")
# R4 floor: below this the palette is too cramped to prefer taste over variation,
# so accents become fair game as canvases rather than shipping identical posters.
_MIN_ELIGIBLE_SCHEMES = 3


def _schemes_from_canvases(
    values: tuple[str, ...], canvases: tuple[str, ...]
) -> tuple[dict[str, str], ...]:
    schemes: list[dict[str, str]] = []
    seen: set[str] = set()
    for canvas in canvases:
        for offset in range(_ARRANGEMENTS_PER_CANVAS):
            roles = _scheme_roles(values, canvas, offset)
            if roles is None:
                continue
            identity = _scheme_id(roles)
            if identity in seen:
                continue
            seen.add(identity)
            schemes.append(roles)
            if len(schemes) >= _MAX_CANDIDATE_SCHEMES:
                return tuple(schemes)
    return tuple(schemes)


def _candidate_schemes(palette: dict[str, str]) -> tuple[dict[str, str], ...]:
    """Every eligible scheme for ``palette``, in a stable enumeration order.

    Canvas comes FIRST in the role space (bug-546): the bug-541 selector pinned it
    and permuted three accents, but contrast filtering then handed every large role
    back to the same two colours, so the posters could not differ. Moving the
    canvas is what actually moves the pixels.
    """
    values = _ordered_palette_values(palette)
    preferred: list[str] = []
    for key in _PREFERRED_CANVAS_KEYS:
        value = palette[key]
        if value in values and value not in preferred:
            preferred.append(value)
    schemes = _schemes_from_canvases(values, tuple(preferred))
    if len(schemes) >= _MIN_ELIGIBLE_SCHEMES:
        return schemes
    # Too cramped for taste: widen to every palette entry rather than repeat one look.
    return _schemes_from_canvases(values, values)


def _rejection_reasons(palette: dict[str, str]) -> list[str]:
    values = _ordered_palette_values(palette)
    reasons: list[str] = []
    if len(values) < 3:
        reasons.append(f"palette collapses to {len(values)} unique value(s)")
    for canvas in values:
        readable = len(_pool_on(values, canvas, _MIN_LARGE_CONTRAST))
        body = len(_pool_on(values, canvas, _MIN_BODY_CONTRAST))
        reasons.append(
            f"canvas {canvas}: {readable} value(s) at {_MIN_LARGE_CONTRAST}:1 "
            f"(needs 2), {body} at {_MIN_BODY_CONTRAST}:1 (needs 1)"
        )
    return reasons


def _effective_palette(channel_config: dict[str, Any] | None) -> tuple[dict[str, str], tuple[dict[str, str], ...], bool, list[str]]:
    """The palette schemes are actually built from, plus rejection diagnostics.

    Fail OPEN on diversity, CLOSED on readability (KTD7): a configured palette that
    cannot yield one legible scheme is rejected WHOLE — never patched per-key with
    borrowed colours, which would smuggle a source-owned recipe back in (R5).
    """
    configured = _validated_palette(channel_config)
    schemes = _candidate_schemes(configured)
    if schemes:
        return configured, schemes, False, []
    reasons = _rejection_reasons(configured)
    fallback = dict(DEFAULT_STYLE["palette"])
    fallback_schemes = _candidate_schemes(fallback)
    if not fallback_schemes:  # pragma: no cover - centralized default is contrast-safe
        raise ValueError(
            "no contrast-valid poster palette scheme exists, not even from the "
            f"centralized neutral fallback; configured palette rejected because: {reasons}"
        )
    return fallback, fallback_schemes, True, reasons


def _content_ordered(schemes: tuple[dict[str, str], ...], fingerprint: str) -> list[dict[str, str]]:
    """Rotate the eligible schemes so the poster's own content picks the preferred
    one, then anti-repeat walks outward from there (R8)."""
    start = int(fingerprint[:8], 16) % len(schemes)
    return [*schemes[start:], *schemes[:start]]


def _select_scheme(
    schemes: tuple[dict[str, str], ...],
    poster_format: str,
    fingerprint: str,
    recent: tuple[dict[str, Any], ...],
) -> tuple[dict[str, str], str, list[str]]:
    """Pick one scheme; return it with the reason and the signatures avoided."""
    ordered = _content_ordered(schemes, fingerprint)
    if len(schemes) == 1:
        return ordered[0], "single_eligible_scheme", []
    recent_sigs = [r["dominant_signature"] for r in recent if r.get("dominant_signature")]
    if not recent_sigs:
        return ordered[0], "content_preferred", []
    previous = recent_sigs[0]
    signatures = {id(s): dominant_signature(s, poster_format) for s in ordered}

    # R7 tier 1: when a materially different CANVAS is available it is mandatory —
    # it is the only change big enough to be unmissable in a phone feed.
    tier1 = [s for s in ordered if _canvas_is_distinct(previous, signatures[id(s)])]
    valid = tier1 or [s for s in ordered if _dominance_is_distinct(previous, signatures[id(s)])]
    avoided = [_scheme_id(s) for s in ordered if s not in valid]
    if not valid:
        # Fail open on diversity: still the most different thing available.
        best = max(ordered, key=lambda s: dominance_distance(previous, signatures[id(s)])["weighted_delta_e"])
        return best, _NO_ALTERNATIVE_REASON, avoided

    # R10: with enough room, dodge the two most recent looks, not just the last.
    if len(schemes) >= 3:
        strict = [s for s in valid if all(signatures[id(s)] != sig for sig in recent_sigs[:2])]
        if strict:
            return strict[0], "anti_repeat_two_recent", avoided
    return valid[0], "anti_repeat_previous", avoided


_NO_ALTERNATIVE_REASON = "no_r7_alternative_max_distance_fallback"


def _variation_limits(scheme_count: int, reason: str) -> tuple[bool, str]:
    """Whether this poster had to settle for less variation than the rule wants.

    Two ways that happens. A palette so cramped it yields a single scheme is the
    one R5 names — in practice unreachable, because an eligible canvas always
    admits both of its two arrangements or neither, so the count is never 1; it is
    kept as an honest guard rather than a claim. The live degraded path is the
    second: schemes exist, but none of them clears R7 against the previous poster,
    so the most-different one ships and says so instead of pretending.
    """
    if scheme_count < 2:
        return True, "the effective palette yields fewer than two contrast-valid schemes"
    if reason == _NO_ALTERNATIVE_REASON:
        return True, (
            "no eligible scheme cleared the R7 dominance gate against the previous "
            "sibling; shipped the greatest-distance candidate available"
        )
    return False, ""


def select_palette_contract(
    plan: dict[str, Any],
    channel_config: dict[str, Any] | None = None,
    recent: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """The one resolved palette decision for a poster: roles, why, and proof.

    ``recent`` holds previously persisted contracts for sibling Shorts, most
    recent first. Callers with no history (standalone prompt inspection) get pure
    deterministic content-based selection (R17).
    """
    palette, schemes, rejected, reasons = _effective_palette(channel_config)
    poster_format = str(plan.get("poster_format") or "")
    fingerprint = _content_fingerprint(plan)
    roles, reason, avoided = _select_scheme(schemes, poster_format, fingerprint, recent)
    limited, limited_reason = _variation_limits(len(schemes), reason)
    return {
        "schema_version": POSTER_PALETTE_SCHEMA,
        "scheme_id": _scheme_id(roles),
        "roles": roles,
        "dominant_signature": dominant_signature(roles, poster_format),
        "poster_format": poster_format,
        "palette_fingerprint": palette_fingerprint(palette),
        "content_fingerprint": fingerprint,
        "eligible_scheme_count": len(schemes),
        "variation_limited": limited,
        "variation_limited_reason": limited_reason,
        "selection_reason": reason,
        "avoided_scheme_ids": avoided,
        "configured_palette_rejected": rejected,
        "rejection_reasons": reasons,
        "contrast_evidence": _contrast_evidence(roles, poster_format),
    }


def build_effective_palette(
    plan: dict[str, Any], channel_config: dict[str, Any] | None = None
) -> dict[str, str]:
    """Semantic role -> exact hex, all sourced from the channel style DNA.

    Kept as the standalone, history-free entry point (R17): same plan + same
    palette always yields the same mapping.
    """
    return select_palette_contract(plan, channel_config)["roles"]


def effective_palette_fingerprint(channel_config: dict[str, Any] | None = None) -> str:
    """Fingerprint of the palette schemes are actually built from — the CONFIGURED
    one, or the neutral fallback when the configured palette was rejected whole."""
    palette, _schemes, _rejected, _reasons = _effective_palette(channel_config)
    return palette_fingerprint(palette)


_REQUIRED_ROLES = frozenset(
    {
        "canvas",
        "body_text",
        "headline_1",
        "headline_2",
        "badge_fill",
        "badge_text",
        "positive",
        "positive_text",
        "negative",
        "negative_text",
        "divider_accent",
    }
)


def validate_palette_contract(
    contract: Any, expected_palette_fingerprint: str | None = None
) -> bool:
    """True when a persisted contract can still be trusted (R14).

    Everything is rechecked from the hexes themselves rather than believed: a
    sidecar written before a Style DNA edit, hand-tampered, or truncated must be
    replaced, never used to letter a poster nobody can read.
    """
    if not isinstance(contract, dict):
        return False
    if contract.get("schema_version") != POSTER_PALETTE_SCHEMA:
        return False
    roles = contract.get("roles")
    if not isinstance(roles, dict) or set(roles) != _REQUIRED_ROLES:
        return False
    if not all(is_valid_hex(value) for value in roles.values()):
        return False
    if (
        expected_palette_fingerprint is not None
        and contract.get("palette_fingerprint") != expected_palette_fingerprint
    ):
        return False
    poster_format = contract.get("poster_format")
    if not isinstance(poster_format, str):
        return False
    if contract.get("dominant_signature") != dominant_signature(roles, poster_format):
        return False
    evidence = contract.get("contrast_evidence")
    if evidence != _contrast_evidence(roles, poster_format):
        return False
    return all(ratio >= _minimum_for_pair(pair) for pair, ratio in evidence.items())


def _palette_provenance_line(contract: dict[str, Any]) -> str:
    """Scheme identity in the prompt itself, so an operator can diff two posters'
    dominant decisions from the logs alone without running Python (R16).

    Names no colour: it reports hex values already bound to roles above.
    """
    signature = "; ".join(f"{role}={value}" for role, value in contract["dominant_signature"].items())
    line = (
        "AUDIT NOTE — METADATA FOR THE HUMAN OPERATOR, NOT POSTER CONTENT: do not "
        "draw, letter, render or depict any part of this note anywhere on the "
        f"image. Palette scheme id {contract['scheme_id']}; dominant roles: "
        f"{signature}; selection: {contract['selection_reason']}; "
        f"variation_limited={str(contract['variation_limited']).lower()}"
    )
    if contract.get("variation_limited_reason"):
        line += f" ({contract['variation_limited_reason']})"
    if contract.get("configured_palette_rejected"):
        line += (
            "; configured_palette_rejected=true — the channel palette could not "
            "produce a legible scheme and the neutral fallback was used: "
            + " | ".join(contract.get("rejection_reasons") or [])
        )
    return line + "."


def _palette_contract_line(roles: dict[str, str]) -> str:
    """The single mandatory role->hex contract every block references (KTD4)."""
    listing = "; ".join(f"{role} = {value}" for role, value in roles.items())
    return (
        "PALETTE CONTRACT (MANDATORY): use these EXACT hex values for the DESIGN "
        f"layer — {listing}. The design layer means the canvas/background, all "
        "typography, badges, pills, cards and their tints, dividers, gridlines, "
        "flat decorative icons and the state marks (check / cross). Do NOT "
        "introduce any color outside this list for those elements, and do NOT "
        "fall back to your own default infographic color scheme — use only the "
        "hex values given above. EXEMPTION: the realistic food/object/topic "
        "photos keep their NATURAL, true-to-life colors; never tint, recolor or "
        "posterize them to the palette. Keep strong readable contrast: headline "
        "and body text must stay clearly legible on the canvas, and lettering on "
        "a filled badge must use the badge text color given above."
    )

_BASE = (
    "Design ONE dense vertical infographic POSTER (9:16, mobile) for a Spanish "
    "wellness audience. Render ALL the Spanish text below EXACTLY as written, spelled "
    "correctly with accents, large and legible on a phone. Use simple realistic food/"
    "object photos or clean flat icons per item. Keep generous margins; do not crop any "
    "text. Add NO other text, no captions beyond what is listed, and no watermark or logo "
    "EXCEPT the channel brand mark described below (if any) — that is the only permitted "
    "extra mark on the poster. "
    "STRICT TYPOGRAPHIC HIERARCHY: render each item's short label/sub-heading LARGE and "
    "extra-BOLD, and any explanation/note text clearly smaller and lighter beneath it — "
    "a phone viewer must understand the poster from the bold labels alone, without "
    "reading the small text. "
    "VISUAL CONSISTENCY: every item photo/icon must share ONE consistent rendering "
    "style across the whole poster — same lighting, same scale, each subject cleanly "
    "isolated within its own cell (no item drawn in a different art style from the "
    "rest). Prefix each small note/benefit line with a tiny matching mini-icon "
    "(e.g., a heart for a heart benefit) so notes read as designed rows."
)


def _labels(plan: dict[str, Any]) -> list[dict[str, Any]]:
    raw = plan.get("items")
    return [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []


def _message_match_line(plan: dict[str, Any]) -> str:
    """Topic-first visual contract (2026-07-12, mirrors long-form bug-532).

    The poster imagery must restate the topic by itself: derive the concrete
    objects the header/items actually name (same deterministic vocabulary the
    long-form thumbnail planner uses) and demand they appear as the hero/topic
    imagery, so a 45+ viewer understands the poster without reading small text.
    """
    from video_agent.thumbnail_planner import derive_topic_props

    header = " ".join(
        str(plan.get(k) or "").strip() for k in ("title", "hook_line")
    ).strip()
    body_text = " ".join(
        [str(plan.get("subtitle") or "").strip()]
        + [str(i.get("label") or "").strip() for i in _labels(plan)]
    ).strip()
    props = derive_topic_props(body_text, header)

    line = (
        "MESSAGE MATCH (MANDATORY): from the imagery and BOLD labels ALONE — "
        "without reading any small text — a Spanish adult aged 45+ scanning a "
        "phone feed must understand what this poster is about and what to do. "
        "Every item's photo/icon must depict that item's own words, never a "
        "generic wellness image."
    )
    if props:
        line += (
            " The hero/topic imagery must visibly show: "
            + "; ".join(props)
            + " — the exact objects the poster text names."
        )
    return line


def _format_block(plan: dict[str, Any], roles: dict[str, str]) -> str:
    fmt = str(plan.get("poster_format") or "")
    items = _labels(plan)
    labels = [str(i.get("label") or "").strip() for i in items]
    if fmt == "category_grid":
        return (
            "Layout: a grid of "
            f"{len(labels)} labelled cells separated by thin gridlines in the divider/"
            f"accent color ({roles['divider_accent']}), each with an icon/photo above "
            f"its label lettered in the body text color ({roles['body_text']}), an "
            "optional central hero image. Items: " + "; ".join(labels) + "."
        )
    if fmt == "numbered_tips":
        return (
            "Layout: a NUMBERED vertical list (1, 2, 3 …), each row an icon + a short "
            "tip. Render each number as a bold digit in the badge text color "
            f"({roles['badge_text']}) inside a solid CIRCULAR badge filled with the "
            f"badge fill color ({roles['badge_fill']}); all badges use that same "
            "fill. Numbered items in order: "
            + "; ".join(f"{n}. {t}" for n, t in enumerate(labels, 1)) + "."
        )
    if fmt == "warning_list":
        rows = [
            f"{n}. {str(i.get('label') or '').strip()}"
            + (f" — {str(i.get('note') or '').strip()}" if i.get("note") else "")
            for n, i in enumerate(items, 1)
        ]
        return (
            "Layout: a NUMBERED warning list, each row a food photo + a CROSS (X) mark "
            f"drawn in the negative/warning color ({roles['negative']}) + a short "
            "caution. Render each number as a bold digit in the negative text color "
            f"({roles['negative_text']}) inside a solid CIRCULAR badge filled with the "
            f"negative/warning color ({roles['negative']}); all badges use that same "
            "fill. Rows: " + "; ".join(rows) + "."
        )
    if fmt == "myth_vs_truth":
        rows = [
            f'Row {n}: MITO {n} card = "{str(i.get("label") or "").strip()}"; '
            f'VERDAD {n} card = "{str(i.get("note") or "").strip()}"'
            for n, i in enumerate(items, 1)
        ]
        return (
            "Layout: a TWO-COLUMN grid of rounded rectangle cards — a MITO column on "
            "the left and a VERDAD column on the right, each numbered pair aligned on "
            "the same row. Tint the MITO cards with a soft, low-opacity wash of the "
            f"negative/warning color ({roles['negative']}) and the VERDAD cards with a "
            f"soft, low-opacity wash of the positive color ({roles['positive']}), both "
            f"still readable under body text ({roles['body_text']}). Each Mito card "
            "starts with a small numbered circular badge and a ribbon tag reading "
            f"\"MITO n\" both filled with the negative/warning color ({roles['negative']}) "
            f"and lettered in the negative text color ({roles['negative_text']}), then a CROSS "
            f"(X) icon in the negative/warning color ({roles['negative']}) and the myth "
            "text. Each Verdad card starts with a small numbered circular badge and a "
            f"ribbon tag reading \"VERDAD n\" both filled with the positive color "
            f"({roles['positive']}) and lettered in the positive text color "
            f"({roles['positive_text']}), then a CHECK icon in the positive color "
            f"({roles['positive']}) and the truth text. One small relevant photo/icon per "
            "card. Rows: " + "; ".join(rows) + "."
        )
    if fmt == "timeline_routine":
        rows = [
            f"{str(i.get('time') or '').strip()} — {str(i.get('label') or '').strip()}"
            for i in items
        ]
        return (
            "Layout: a vertical DAY TIMELINE from top (morning) to bottom (night): a "
            f"connected line drawn in the divider/accent color ({roles['divider_accent']}) "
            f"with a dot per moment filled in the badge fill color ({roles['badge_fill']}), "
            "the clock time LARGE and bold beside each dot, an icon and the activity "
            "label next to it. Moments in order: " + "; ".join(rows) + "."
        )
    if fmt == "checklist_score":
        score_line = str(plan.get("score_line") or "").strip()
        return (
            "Layout: a SELF-CHECK list, each row an empty checkbox (☐) outlined in the "
            f"divider/accent color ({roles['divider_accent']}) + one short criterion. "
            "Rows: " + "; ".join(labels) + "."
            + (
                " At the bottom, a highlighted score band filled with the badge fill "
                f"color ({roles['badge_fill']}) and lettered in the badge text color "
                f'({roles["badge_text"]}): "{score_line}".'
                if score_line
                else ""
            )
        )
    if fmt == "comparison":
        groups_sorted = sorted({str(x.get("group") or "") for x in items})
        first_group = groups_sorted[0] if groups_sorted else ""
        left = [str(i.get("label") or "").strip() for i in items if str(i.get("group") or "") == first_group]
        right = [str(i.get("label") or "").strip() for i in items if str(i.get("label") or "").strip() not in left]
        return (
            "Layout: TWO columns side by side separated by a central divider drawn in "
            f"the divider/accent color ({roles['divider_accent']}), a CHECK mark in the "
            f"positive color ({roles['positive']}) over the recommended column and a "
            f"CROSS mark in the negative/warning color ({roles['negative']}) over the "
            f"other. Left column: {', '.join(left)}. Right column: {', '.join(right)}."
        )
    return "Layout: a clean labelled infographic. Items: " + "; ".join(labels) + "."


def _brand_identity_line(channel_config: dict[str, Any] | None) -> str:
    name = str(((channel_config or {}).get("channel") or {}).get("name") or "").strip()
    if not name:
        return ""
    return (
        f'\n\nBrand identity (creative direction): "{name}". Match a calm, '
        f"trustworthy, editorial wellness tone consistent with this identity. "
        f"Additionally, add ONE small rounded brand badge near the bottom of the "
        f'poster: a checkmark icon + the text "{name}" inside a thin bordered pill, '
        f"small and unobtrusive — this is the ONLY on-screen brand mark, do not add "
        f"any other banner, bar, or repeat of the channel name elsewhere. Any small "
        f"decorative accent near this badge should be thematically tied to the "
        f"poster's real topic (matching the header's decorative icons), never a "
        f"fixed generic motif repeated across every topic. Do NOT add any mascot "
        f"character, cartoon person, or speech bubble anywhere on the poster."
    )


def _header_style_line(subtitle: str, roles: dict[str, str]) -> str:
    line = (
        "HEADER STYLE: split the title across two bold lines with a color change "
        f"between them — first line in the headline 1 color ({roles['headline_1']}), "
        f"second line in the headline 2 color ({roles['headline_2']}) — for strong "
        "editorial-magazine impact. Both lines must stay clearly readable on the "
        f"canvas ({roles['canvas']})."
    )
    if subtitle:
        line += (
            " Render the subtitle as a small rounded PILL/badge shape filled with the "
            f"badge fill color ({roles['badge_fill']}) and bold lettering in the badge "
            f"text color ({roles['badge_text']}), centered just below the title."
        )
    line += (
        " Add two small decorative icons flanking a central circular topic icon just "
        "under the header — simple, thematically related to the topic (e.g., weather "
        "icons either side of a joint for a joint-pain-and-weather topic, a moon and "
        "clock for a sleep topic) — purely decorative, no extra words. Add a thin "
        "dotted horizontal line in the divider/accent color "
        f"({roles['divider_accent']}) separating the header from the content below."
    )
    return line


def build_poster_body(
    plan: dict[str, Any],
    channel_config: dict[str, Any] | None = None,
    palette_contract: dict[str, Any] | None = None,
) -> str:
    """The raw poster body WITHOUT the driver's dimension instruction.

    ``palette_contract`` is the already-resolved, already-persisted decision from
    the generation seam. Passing it in is what binds the sidecar, the sent body and
    the logged prompt to ONE scheme (R15); omitting it (standalone inspection)
    falls back to history-free deterministic selection (R17).
    """
    title = str(plan.get("title") or "").strip()
    subtitle = str(plan.get("subtitle") or "").strip()
    # ONE effective mapping per call feeds the contract, header and format block,
    # so the logged prompt and the sent body can never diverge (KTD4).
    contract = palette_contract or select_palette_contract(plan, channel_config)
    roles = contract["roles"]
    body = _BASE + "\n\n" + f'Big title at the top: "{title}".'
    if subtitle:
        body += f' Subtitle under it: "{subtitle}".'
    body += "\n\n" + _palette_contract_line(roles)
    body += "\n\n" + _header_style_line(subtitle, roles)
    body += "\n\n" + _message_match_line(plan)
    body += "\n\n" + _format_block(plan, roles)
    body += _brand_identity_line(channel_config)
    body += "\n\n" + _palette_provenance_line(contract)
    return body


def wrap_poster_body(body: str) -> str:
    """Wrap an ALREADY-BUILT body with the portrait dimension instruction.

    Callers that must log and send the same prompt build the body once and wrap
    that exact string, so the audit log can never show a different effective
    palette from what ``image_fn`` received (KTD4/R7)."""
    return build_image_gen_prompt(body, aspect_ratio="9:16")


def build_poster_prompt(plan: dict[str, Any], channel_config: dict[str, Any] | None = None) -> str:
    """Full prompt (body + portrait dimension instruction) — for logging/inspection."""
    return wrap_poster_body(build_poster_body(plan, channel_config))
