"""Hard eligibility checks for candidate Pexels assets (spec §15)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .quality import strong_negative_match


@dataclass
class EligibilityResult:
    eligible: bool
    hard_reject_reason: str | None = None
    can_escape_hatch: bool = False


def normalize_provider_id(provider: str | None, visual_dna: dict[str, Any] | None = None) -> str:
    """Map provider IDs (e.g. ``pexels_video``) to canonical IDs via visual_dna aliases."""
    raw = str(provider or "").strip()
    if not raw:
        return ""
    aliases = ((visual_dna or {}).get("source_policy", {}) or {}).get("provider_aliases", {}) or {}
    return aliases.get(raw, raw)


def is_pexels_provider(provider: str | None, visual_dna: dict[str, Any] | None = None) -> bool:
    """Allow only providers that normalize into source_policy.external_stock_providers.

    Backward-compatible: if no visual_dna is supplied, falls back to the legacy
    prefix check so existing call-sites keep working.
    """
    if not provider:
        return False
    if visual_dna is None:
        return str(provider).lower().startswith("pexels")
    normalized = normalize_provider_id(provider, visual_dna)
    allowed = set(
        (visual_dna.get("source_policy", {}) or {}).get("external_stock_providers")
        or ["pexels"]
    )
    return normalized in allowed


def graphic_card_fallback_available(
    scene: dict[str, Any],
    visual_config: dict[str, Any],
    renderer_caps: dict[str, Any],
) -> bool:
    """True iff a renderable graphic card can replace the scene's stock asset."""
    from .graphic_cards import graphic_card_action  # local import avoids cycle

    action = graphic_card_action(visual_config, renderer_caps)
    if action != "render":
        return False

    supported = set(
        ((visual_config or {}).get("graphic_cards", {}) or {}).get("supported_card_types")
        or []
    )
    requested = ((scene.get("graphic_card") or {}) if isinstance(scene, dict) else {}).get("type")
    if requested:
        return requested in supported
    return bool(supported & {"checklist", "timeline", "habit_matrix"})


def is_duplicate_in_current_job(provider_asset_id: str | None, job_state: dict[str, Any]) -> bool:
    if not provider_asset_id:
        return False
    used = job_state.get("used_provider_asset_ids") or set()
    return str(provider_asset_id) in used


def is_resolution_too_low(candidate: dict[str, Any], min_width: int = 1280, min_height: int = 720) -> bool:
    width = int(candidate.get("width") or 0)
    height = int(candidate.get("height") or 0)
    if not width or not height:
        return False
    return width < min_width or height < min_height


def rollout_mode_is_enforce(visual_config: dict[str, Any]) -> bool:
    diversity = (visual_config or {}).get("diversity", {}) or {}
    return str(diversity.get("rollout_mode", "report_only")).lower() == "enforce"


def candidate_eligibility(
    candidate: dict[str, Any],
    scene: dict[str, Any],
    job_state: dict[str, Any],
    visual_config: dict[str, Any],
    visual_dna: dict[str, Any],
    candidate_text: str | None = None,
) -> EligibilityResult:
    if not is_pexels_provider(str(candidate.get("provider") or ""), visual_dna):
        return EligibilityResult(False, "non_pexels_provider")

    diversity = (visual_config or {}).get("diversity", {}) or {}
    if is_duplicate_in_current_job(candidate.get("provider_asset_id"), job_state):
        if diversity.get("duplicate_asset_escape_hatch") == "warn_if_no_alternatives":
            return EligibilityResult(False, "duplicate_asset_current_job", can_escape_hatch=True)
        return EligibilityResult(False, "duplicate_asset_current_job")

    text_for_negatives = candidate_text or " ".join([
        str(candidate.get("source_url") or ""),
        str(candidate.get("photographer") or ""),
        " ".join(candidate.get("tags") or []),
        str(candidate.get("attribution") or ""),
    ])
    scene_text_value = str(scene.get("narration_text") or scene.get("on_screen_text") or "")
    if strong_negative_match(text_for_negatives, scene_text_value, visual_dna):
        return EligibilityResult(False, "strong_negative_pattern")

    if candidate.get("invalid_local_file"):
        return EligibilityResult(False, "invalid_cache_file")

    if is_resolution_too_low(candidate) and rollout_mode_is_enforce(visual_config):
        return EligibilityResult(False, "resolution_too_low", can_escape_hatch=True)

    return EligibilityResult(True)
