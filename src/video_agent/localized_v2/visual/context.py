from __future__ import annotations

from dataclasses import dataclass


class VisualLocalizationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VisualContext:
    locale: str
    topic: str
    people_context: str
    market_context: str | None
    evidence: tuple[str, ...]
    avoid: tuple[str, ...]


def _contains_avoided(text: str, avoid: tuple[str, ...]) -> str | None:
    folded = text.casefold()
    return next((phrase for phrase in avoid if phrase.casefold() in folded), None)


def build_visual_context(
    topic: str,
    locale_pack: dict,
    *,
    market_relevant: bool,
    evidence: tuple[str, ...] = (),
) -> VisualContext:
    if not topic.strip():
        raise VisualLocalizationError("visual topic cannot be empty")
    if market_relevant and not any(item.strip() for item in evidence):
        raise VisualLocalizationError("market context requires explicit topic evidence")
    visuals = locale_pack["visuals"]
    avoid = tuple(str(item) for item in visuals["avoid"])
    context = VisualContext(
        locale=str(locale_pack["locale"]),
        topic=topic.strip(),
        people_context=str(visuals["peopleContext"]).strip(),
        market_context=str(locale_pack["market"]).strip() if market_relevant else None,
        evidence=tuple(item.strip() for item in evidence if item.strip()),
        avoid=avoid,
    )
    validate_visual_context(context)
    return context


def validate_visual_context(context: VisualContext) -> None:
    combined = " ".join(
        part
        for part in (
            context.topic,
            context.people_context,
            context.market_context or "",
            *context.evidence,
        )
        if part
    )
    forbidden = _contains_avoided(combined, context.avoid)
    if forbidden:
        raise VisualLocalizationError(
            f"visual context contains prohibited stereotype guidance: {forbidden}"
        )
