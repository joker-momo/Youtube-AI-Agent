from __future__ import annotations

from typing import Any

from video_agent.shorts.validation.checks import *  # noqa: F401,F403
from video_agent.shorts.validation._helpers import _joined_scene_text, _scene_id
from video_agent.shorts.validation.graphic_checks import (
    _missing_graphic_candidate,
    graphic_repair_targets,
    is_explicit_graphic_led,
)


def _is_five_error_bread_script(script: dict[str, Any] | None) -> bool:
    if not script:
        return False
    contract = script.get("idea_contract") or {}
    count = contract.get("original_count") == 5 or contract.get("final_count") == 5
    narration = str(script.get("narration") or "").lower()
    return bool(count and any(term in narration for term in ("pan", "bread", "hogaza")))


def repair_five_error_bread_payoff_layout(
    scenes: list[dict[str, Any]], script: dict[str, Any] | None
) -> bool:
    """Normalize the fixed 5-error bread payoff card before spending a regen."""
    if not _is_five_error_bread_script(script) or len(scenes) < 2:
        return False

    payoff = scenes[-2]
    changed = payoff.get("layout") != "graphic_checklist"

    if payoff.get("on_screen_text") != "MEJOR ASÍ":
        payoff["on_screen_text"] = "MEJOR ASÍ"
        changed = True

    canonical_payload = {
        "title": "MEJOR ASÍ",
        "items": ["Porción visible", "Plato pequeño", "Comida completa"],
    }
    if payoff.get("layout_payload") != canonical_payload:
        payoff["layout_payload"] = canonical_payload
        changed = True

    try:
        duration = float(payoff.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    repaired_duration = min(5.0, max(4.2, duration or 4.6))
    if duration != repaired_duration:
        payoff["duration_sec"] = repaired_duration
        changed = True

    if payoff.get("layout") != "graphic_checklist":
        payoff["layout"] = "graphic_checklist"
        payoff["visual_type"] = "graphic"
        changed = True
    elif payoff.get("visual_type") != "graphic":
        payoff["visual_type"] = "graphic"
        changed = True

    if not payoff.get("caption"):
        payoff["caption"] = "Porción visible"
        changed = True
    if not payoff.get("visual_prompt"):
        payoff["visual_prompt"] = "Graphic checklist card for a better bread portion routine"
        changed = True
    return changed


def repair_missing_graphic_checklist_scene(
    scenes: list[dict[str, Any]], script: dict[str, Any] | None
) -> bool:
    """Promote one compact structured scene to a ChatGPT-backed graphic.

    Scene generation can correctly identify a compact proof/comparison/portion
    beat but leave it as a ``short_*`` lifestyle scene. If the Short is
    checklist/explainer-like and still has room under the two-graphic cap,
    convert the best existing structured scene before spending another LLM retry.
    """
    if not script:
        return False
    graphic_count = sum(
        1 for scene in scenes if str(scene.get("layout") or "").startswith("graphic_")
    )
    if graphic_count >= MAX_GRAPHIC_SCENES_PER_SHORT:
        return False
    def _title(scene: dict[str, Any]) -> str:
        title = str(scene.get("on_screen_text") or "").strip() or str(
            (scene.get("layout_payload") or {}).get("title") if isinstance(scene.get("layout_payload"), dict) else ""
        ).strip()
        return title[:48] or "GUÍA VISUAL"

    def _graphic_payload(scene: dict[str, Any], layout: str) -> dict[str, Any]:
        text = _joined_scene_text(scene).lower()
        title = _title(scene)
        existing = scene.get("layout_payload")
        items = list((existing or {}).get("items") or []) if isinstance(existing, dict) else []
        items = [str(item).strip() for item in items if str(item).strip()]
        if layout == "graphic_comparison":
            left_text = "1 rebanada" if ("rebanada" in text or "1 o 2" in text) else "Opción A"
            right_text = "2 rebanadas" if ("rebanada" in text or "1 o 2" in text) else "Opción B"
            return {
                "title": title,
                "left": {"heading": "MENOS", "text": left_text, "badge": "Empieza aquí"},
                "right": {"heading": "MÁS", "text": right_text, "badge": "Según tu plato"},
                "footer": "Ajusta al contexto.",
                "variant": "warm_olive",
                "visual_tone": "focus",
                "background_mode": "video_blur",
                "surface_style": "soft_card",
            }
        if layout == "graphic_plate_ratio":
            return {
                "title": title,
                "segments": [
                    {"label": "Pan", "value": 25},
                    {"label": "Resto del plato", "value": 75},
                ],
                "footer": "Mira el conjunto.",
                "variant": "warm_olive",
                "visual_tone": "focus",
                "background_mode": "video_blur",
                "surface_style": "plate_focus",
            }
        if layout == "graphic_label_callout":
            return {
                "title": title,
                "productLabel": "Etiqueta",
                "callouts": [
                    {"label": "Ingrediente", "value": "primero"},
                    {"label": "Fibra", "value": "compara"},
                ],
                "variant": "warm_olive",
                "visual_tone": "focus",
                "background_mode": "video_blur",
                "surface_style": "soft_card",
            }
        return {
            "title": title,
            "items": items[:5] or ["Mira el contexto", "Decide antes", "Ajusta al plato"],
            "variant": "warm_olive",
            "visual_tone": "focus",
            "background_mode": "video_blur",
            "surface_style": "soft_card",
        }

    def _target_layout(scene: dict[str, Any]) -> str:
        text = _joined_scene_text(scene).lower()
        if any(term in text for term in ("1 o 2", "una o dos", "opción a", "opcion a", "opción b", "opcion b", " vs ")):
            return "graphic_comparison"
        if sum(1 for term in ("porción", "porcion", "palma", "plato", "hidrato", "rebanada") if term in text) >= 2:
            return "graphic_plate_ratio"
        if sum(1 for term in ("etiqueta", "fibra", "azúcar", "azucar", "ingrediente", "por 100 g") if term in text) >= 2:
            return "graphic_label_callout"
        return "graphic_checklist"

    for scene in scenes:
        if str(scene.get("layout") or "") not in {"short_checklist", "short_tip"}:
            continue
        payload = scene.get("layout_payload")
        items = list((payload or {}).get("items") or []) if isinstance(payload, dict) else []
        has_items = len([item for item in items if str(item).strip()]) >= 2
        if not has_items and not _missing_graphic_candidate(scene):
            continue
        layout = "graphic_checklist" if has_items else _target_layout(scene)
        scene["layout"] = layout
        scene["layout_payload"] = _graphic_payload(scene, layout)
        scene["visual_type"] = "graphic"
        scene["asset_strategy"] = "ai_image_preferred"
        return True
    return False


def _demote_graphic_to_short_tip(scene: dict[str, Any]) -> None:
    """Turn one over-cap graphic scene into a realistic short_tip scene in place.

    Keeps narration / duration / id / on_screen_text; strips the graphic-only
    layout + payload + visual_type so it renders as a realistic supermarket/kitchen
    beat instead of a card. Builds a fallback visual_prompt from the graphic's
    title/items when the scene has none.
    """
    title = str(scene.get("on_screen_text") or "").strip()
    payload = scene.get("layout_payload")
    if isinstance(payload, dict):
        if not title:
            title = str(payload.get("title") or "").strip()
        items = [str(i).strip() for i in (payload.get("items") or []) if str(i).strip()]
    else:
        items = []
    scene["layout"] = "short_tip"
    scene.pop("layout_payload", None)
    # Detection keys off layout startswith graphic_ OR visual_type == "graphic".
    if str(scene.get("visual_type") or "").strip().lower() == "graphic":
        scene.pop("visual_type", None)
    scene["asset_strategy"] = "stock_ok"
    if not str(scene.get("visual_prompt") or "").strip():
        focus = title or (items[0] if items else "")
        base = "Realistic Spanish supermarket/kitchen scene, vertical 9:16, warm natural light"
        scene["visual_prompt"] = f"{base}, showing {focus}".strip().rstrip(",") if focus else base


def repair_excess_graphic_scenes(
    scenes: list[dict[str, Any]], script: dict[str, Any] | None
) -> bool:
    """Demote the lowest-value graphics when a normal Short is over the cap.

    A normal Short allows at most ``MAX_GRAPHIC_SCENES_PER_SHORT`` graphics. The
    LLM frequently emits more for checklist/explainer ideas; without an explicit
    graphic-led request that is a repairable error. Convert the lowest-value
    excess graphics into realistic short_tip scenes deterministically — before
    spending another LLM retry that often re-emits the same over-cap layout and
    loops straight to a hard blocker. ``graphic_repair_targets`` decides which
    graphics to keep vs. convert.
    """
    graphic_count = sum(
        1 for s in scenes if str(s.get("layout") or "").startswith("graphic_")
    )
    if graphic_count <= MAX_GRAPHIC_SCENES_PER_SHORT:
        return False
    if is_explicit_graphic_led(script):
        return False
    _keep_ids, convert_ids = graphic_repair_targets(scenes)
    convert = set(convert_ids)
    if not convert:
        return False
    changed = False
    for index, scene in enumerate(scenes):
        if not str(scene.get("layout") or "").startswith("graphic_"):
            continue
        if _scene_id(scene, index) not in convert:
            continue
        _demote_graphic_to_short_tip(scene)
        changed = True
    return changed


def repair_scene_duration_if_possible(scene: dict[str, Any]) -> str:
    layout = scene.get("layout") or ""
    narration = scene.get("narration") or ""
    est = estimate_spanish_narration_sec(narration, 2.25)
    required = round(est + 0.3, 1)

    cap = GLOBAL_SCENE_MAX_SEC
    target = LAYOUT_DURATION_TARGETS.get(layout)
    if target:
        cap = target[2]

    try:
        dur = float(scene.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0

    if layout == "short_cta" and dur > 2.8 and count_spoken_words(narration) <= 5:
        scene["duration_sec"] = 2.8
        return "auto_shortened_cta"

    # Specific mechanical repair for payoff/checklist scenes
    if layout in {"short_checklist", "graphic_checklist"} and dur > 5.0:
        if required <= 4.5:
            scene["duration_sec"] = 4.5
            return "auto_shortened"
        elif est <= 5.0:
            scene["duration_sec"] = 5.0
            return "auto_shortened"

    # Clamp an over-long scene down to its layout hard cap when the narration
    # comfortably fits within the cap (audio-fit safe). Prevents a single
    # payoff/checklist scene at e.g. 7.4s from looping scene validation when
    # the rest of the candidate is valid.
    if dur > cap and est <= cap:
        scene["duration_sec"] = cap
        return "auto_shortened"

    if required <= cap and dur < required:
        scene["duration_sec"] = required
        return "auto_extended"

    if required > cap:
        return "must_split_or_compress"

    return "ok"


# ---------------------------------------------------------------------------
# Deterministic scene_narration_fit repair (QA storm fix v2.2)
#
# Repair an over-long scene narration mechanically — extend within the layout
# cap, split at existing sentence boundaries, or conservatively micro-condense —
# BEFORE burning an LLM scene regeneration. Every repair preserves source
# fidelity, idea count, covers_items, and source_scene_ids.
# ---------------------------------------------------------------------------

SCENE_FIT_TOLERANCE = 0.3

# Whitelisted, meaning-safe edits for micro-condense. Nothing here removes an
# idea item, a count, a claim, or safety/CTA wording.
_MICRO_CONDENSE_FILLER = (
    "un poco",
    "también",
    "simplemente",
    "en realidad",
    "de verdad",
    "muy",
    "bastante",
)
_MICRO_CONDENSE_INTROS = ("Y un truco más:", "Recuerda:", "Fíjate:")

_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]+|\S[^.!?]*$")


def scene_hard_cap(layout: str) -> float:
    target = LAYOUT_DURATION_TARGETS.get(str(layout or ""))
    return float(target[2]) if target else GLOBAL_SCENE_MAX_SEC


def estimate_fits(narration: str, duration: float, tol: float = SCENE_FIT_TOLERANCE) -> bool:
    try:
        dur = float(duration)
    except (TypeError, ValueError):
        dur = 0.0
    return estimate_spanish_narration_sec(str(narration or "")) <= dur + tol


def split_narration_sentences(text: str) -> list[str]:
    return [
        m.group(0).strip() for m in _SENTENCE_RE.finditer(str(text or "")) if m.group(0).strip()
    ]


def _fit_duration(narration: str, cap: float) -> float:
    est = estimate_spanish_narration_sec(narration)
    return round(min(cap, max(est + SCENE_FIT_TOLERANCE, 1.5)), 1)


# Canonical camera motions used to differentiate the second half of a split
# footage scene from the first. Ordered by preference; the picker returns the
# first token not already present in the original motion description.
_SECONDARY_MOTIONS = ("crop_shift", "push_in", "object_reveal", "text_pop")


def _alternate_motion(original: str) -> str:
    low = (original or "").lower()
    for cand in _SECONDARY_MOTIONS:
        if cand not in low:
            return cand
    return "crop_shift"


def try_mechanical_split(scene: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Split a scene at an existing sentence boundary into two scenes that each
    fit the layout cap. Returns ``None`` if no clean split makes every segment
    fit — never invents or rewords narration."""
    narration = str(scene.get("narration") or "")
    sentences = split_narration_sentences(narration)
    if len(sentences) < 2:
        return None
    layout = str(scene.get("layout") or "")
    # A GRAPHIC scene that carries a concrete visual_prompt cannot be split: both
    # halves inherit the SAME rendered graphic card, producing two redundant
    # adjacent graphics (and double-counting against the graphic cap). Inventing a
    # distinct graphic for the second half is forbidden — defer those to
    # micro-condense / LLM regeneration instead.
    #
    # Real-footage short_* layouts CAN split even with a visual_prompt: both halves
    # share the same b-roll location, which is acceptable scene continuation (a
    # common pattern in real Shorts). We hand the second half a DISTINCT camera
    # motion below so the two beats don't read as a static slideshow. This closes
    # the recurring scene_narration_fit hard blocker where a 2-sentence footage
    # scene overflowed its cap but could not be repaired (bug-301/303/306 family).
    if layout in SUPPORTED_GRAPHIC_LAYOUTS and str(scene.get("visual_prompt") or "").strip():
        return None
    cap = scene_hard_cap(layout)
    for i in range(1, len(sentences)):
        left = " ".join(sentences[:i]).strip()
        right = " ".join(sentences[i:]).strip()
        if estimate_fits(left, cap) and estimate_fits(right, cap):
            base_id = str(scene.get("id") or "")
            first = dict(scene)
            second = dict(scene)
            first["id"] = f"{base_id}a" if base_id else base_id
            second["id"] = f"{base_id}b" if base_id else base_id
            first["narration"] = left
            second["narration"] = right
            first["duration_sec"] = _fit_duration(left, cap)
            second["duration_sec"] = _fit_duration(right, cap)
            # Do not invent on-screen/caption text for the second segment.
            second["on_screen_text"] = ""
            second["caption"] = ""
            second.pop("layout_payload", None)
            # When both halves share the same b-roll visual_prompt, give the
            # second beat a distinct camera motion so the pair does not read as a
            # static slideshow. Pure motion change — invents no visual content.
            if str(scene.get("visual_prompt") or "").strip():
                second["motion"] = _alternate_motion(str(scene.get("motion") or ""))
            # Preserve coverage + provenance on both halves.
            covers = list(scene.get("covers_items") or [])
            first["covers_items"] = list(covers)
            second["covers_items"] = list(covers)
            src = list(scene.get("source_scene_ids") or [])
            first["source_scene_ids"] = list(src)
            second["source_scene_ids"] = list(src)
            return [first, second]
    return None


def try_micro_condense(
    scene: dict[str, Any], *, idea_labels: list[str] | None = None
) -> dict[str, Any] | None:
    """Conservatively shrink narration using only whitelisted filler/intro
    removal. Returns a repaired scene copy that fits the layout cap, or ``None``
    if it cannot fit without touching protected meaning."""
    original = str(scene.get("narration") or "")
    if not original.strip():
        return None
    layout = str(scene.get("layout") or "")
    cap = scene_hard_cap(layout)
    labels = [str(l).lower() for l in (idea_labels or []) if str(l).strip()]

    text = original
    for intro in _MICRO_CONDENSE_INTROS:
        text = re.sub(rf"\b{re.escape(intro)}\s*", "", text, flags=re.IGNORECASE)
    for filler in _MICRO_CONDENSE_FILLER:
        text = re.sub(rf"\b{re.escape(filler)}\b", "", text, flags=re.IGNORECASE)
    # Collapse whitespace and stray spaces before punctuation introduced above.
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip()

    if not estimate_fits(text, cap):
        return None
    # Reject if any protected idea label was lost.
    for label in labels:
        if label in original.lower() and label not in text.lower():
            return None
    repaired = dict(scene)
    repaired["narration"] = text
    repaired["duration_sec"] = _fit_duration(text, cap)
    return repaired


def _idea_labels_from_script(script: dict[str, Any] | None) -> list[str]:
    script = script or {}
    labels: list[str] = []
    for kp in script.get("key_points") or []:
        if isinstance(kp, dict):
            point = str(kp.get("point") or kp.get("label") or "")
        else:
            point = str(kp or "")
        labels.extend(w for w in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{4,}", point))
    return labels


def deterministic_scene_fit_repair(
    scenes: list[dict[str, Any]],
    script: dict[str, Any] | None = None,
    *,
    regen_fn: Any = None,
    max_count: int = 12,
) -> dict[str, Any]:
    """Repair scene_narration_fit overflows mechanically before any LLM
    regeneration. Tries extend -> split -> micro-condense per scene; only if all
    fail does it call ``regen_fn`` (once)."""
    idea_labels = _idea_labels_from_script(script)
    modes: list[str] = []
    logs: list[dict[str, Any]] = []
    regen_needed = False

    i = 0
    while i < len(scenes):
        scene = scenes[i]
        narration = str(scene.get("narration") or "")
        layout = str(scene.get("layout") or "")
        try:
            dur = float(scene.get("duration_sec") or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
        if not narration.strip() or estimate_fits(narration, dur):
            i += 1
            continue

        cap = scene_hard_cap(layout)
        est = estimate_spanish_narration_sec(narration)
        log: dict[str, Any] = {
            "scene_id": scene.get("id"),
            "layout": layout,
            "duration_sec": dur,
            "hard_cap_sec": cap,
            "spoken_text_used_for_estimate": narration,
            "word_count": count_spoken_words(narration),
            "estimated_spoken_sec": round(est, 2),
            "overflow_sec": round(est - dur, 2),
            "repair_mode_attempted": "none",
        }

        # A. Extend within layout hard cap.
        if estimate_fits(narration, cap):
            scene["duration_sec"] = _fit_duration(narration, cap)
            log["repair_mode_attempted"] = "extend"
            modes.append("extend")
            logs.append(log)
            i += 1
            continue

        # B. Mechanical split — only if it stays within scene/graphic caps.
        is_graphic = layout in SUPPORTED_GRAPHIC_LAYOUTS
        graphic_ok = (not is_graphic) or (
            count_graphic_scenes(scenes) + 1 <= MAX_GRAPHIC_SCENES_PER_SHORT
        )
        if len(scenes) < max_count and graphic_ok:
            parts = try_mechanical_split(scene)
            if parts:
                scenes[i : i + 1] = parts
                log["repair_mode_attempted"] = "split"
                modes.append("split")
                logs.append(log)
                i += len(parts)
                continue

        # C. Conservative micro-condense.
        condensed = try_micro_condense(scene, idea_labels=idea_labels)
        if condensed is not None:
            scene["narration"] = condensed["narration"]
            scene["duration_sec"] = condensed["duration_sec"]
            log["repair_mode_attempted"] = "micro_condense"
            modes.append("micro_condense")
            logs.append(log)
            i += 1
            continue

        # D. Unfixable mechanically — defer to LLM regeneration.
        log["repair_mode_attempted"] = "regen_required"
        modes.append("regen_required")
        logs.append(log)
        regen_needed = True
        i += 1

    total = round(sum(float(s.get("duration_sec") or 0.0) for s in scenes), 1)

    regen_called = False
    if regen_needed and regen_fn is not None:
        regen_fn(scenes, script)
        regen_called = True

    return {
        "scenes": scenes,
        "modes": modes,
        "logs": logs,
        "total_duration_sec": total,
        "regen_called": regen_called,
        "regen_required": regen_needed,
    }


def count_graphic_scenes(scenes: list[dict[str, Any]]) -> int:
    return sum(1 for s in scenes if str(s.get("layout") or "") in SUPPORTED_GRAPHIC_LAYOUTS)


_NUMBERED_OST_RE = __import__("re").compile(r"^\s*\d+\s*[.):\-]\s*")


def strip_numbered_on_screen_text(scenes: list[dict]) -> bool:
    """Remove leading list numbering ("1. ", "2) ", "3: ") from on_screen_text.

    Shorts list/mistake formats forbid the generic "N. [text]" overlay (it breaks
    the polished visual rhythm); the LLM keeps emitting it and bounded regen does
    not reliably fix it. Deterministically strip the numeric prefix so the blocker
    never reaches Gemini QA — uppercase label content is preserved verbatim.
    """
    changed = False
    for scene in scenes or []:
        ost = scene.get("on_screen_text")
        if not isinstance(ost, str):
            continue
        stripped = _NUMBERED_OST_RE.sub("", ost)
        if stripped != ost:
            scene["on_screen_text"] = stripped.strip()
            changed = True
    return changed


def repair_weak_hook_motion(scenes: list[dict]) -> bool:
    if not scenes:
        return False
    first_scene = scenes[0]
    first_motion = str(first_scene.get("motion") or "").strip()
    if first_motion not in {"push_in", "object_reveal", "face_cut", "text_pop", "crop_shift"}:
        first_scene["motion"] = "push_in"
        if not first_scene.get("pattern_interrupt"):
            first_scene["pattern_interrupt"] = "text_pop at 0.5s"
        return True
    return False


def repair_visual_only_unreadable(scenes: list[dict], required_item: Any) -> bool:
    if not scenes or not required_item:
        return False

    item_id = ""
    item_label = ""

    if isinstance(required_item, dict):
        item_id = str(required_item.get("item_id") or required_item.get("id") or "")
        item_label = str(required_item.get("label") or required_item.get("point") or "")
    elif isinstance(required_item, str):
        try:
            import json

            parsed = json.loads(required_item)
            if isinstance(parsed, dict):
                item_id = str(parsed.get("id", ""))
                item_label = str(parsed.get("label") or parsed.get("point") or "")
            else:
                item_label = required_item
        except Exception:
            item_label = required_item
    else:
        item_label = str(required_item)

    if not item_id and item_label:
        import re

        m = re.match(r"^(\d+)", item_label)
        if m:
            item_id = m.group(1)

    if not item_label:
        item_label = item_id

    if not item_label and not item_id:
        return False

    # Check if already covered properly
    for scene in scenes:
        narration = str(scene.get("narration") or "").lower()
        caption = str(scene.get("caption") or "").lower()
        if item_label.lower() in narration or item_label.lower() in caption:
            # Enforce covers_items
            if item_id:
                covers = set(scene.get("covers_items") or [])
                try:
                    cid = int(item_id)
                except ValueError:
                    cid = item_id
                if cid not in covers and str(cid) not in covers:
                    covers.add(cid)
                    scene["covers_items"] = sorted(
                        list(covers), key=lambda x: (isinstance(x, str), x)
                    )
                    return True
            return False

    # Locate covering scene
    target_scene = None
    for scene in scenes:
        covers = set(scene.get("covers_items") or [])
        try:
            cid = int(item_id)
        except ValueError:
            cid = item_id
        if item_id and (cid in covers or str(item_id) in covers):
            target_scene = scene
            break
        ost = str(scene.get("on_screen_text") or "").lower()
        payload_str = str(scene.get("layout_payload") or "").lower()
        if item_label.lower() in ost or item_label.lower() in payload_str:
            target_scene = scene
            break

    if not target_scene:
        target_scene = scenes[1] if len(scenes) > 1 else scenes[0]

    # Inject the actual item label so coverage validation can detect this scene
    # as a readable caption (mode="caption"), which clears the visual_only check.
    # NEVER hardcode per-item text: a string that does not contain the item's
    # label words makes this function return True while validation keeps failing
    # the same item -> false hard blocker on the next attempt.
    inject_text = item_label

    # Do not inject the item label into target_scene["narration"]!
    # Narration-fit timing must use only the actual TTS-spoken fields.
    # Injecting the source key_point meaning into the spoken audio estimate
    # creates a false hard blocker (scene_narration_fit exceeds tolerance).
    # We keep coverage validation separate from spoken-duration validation.
    current_caption = str(target_scene.get("caption") or "")
    # Strip stale wrong-topic text left by older buggy runs.
    current_caption = current_caption.replace("Vuelve a harina, fibra e ingredientes.", "").strip()
    # Label leads so its words always survive the 12-word readability cap;
    # otherwise a long existing caption could truncate the label away and the
    # caption-coverage check would still fail.
    inject_words = inject_text.split()
    if len(inject_words) > 12:
        combined = " ".join(inject_words[:12]) + "..."
    else:
        remaining = 12 - len(inject_words)
        tail = " ".join(current_caption.split()[:remaining]).strip()
        combined = f"{inject_text} {tail}".strip() if tail else inject_text
    target_scene["caption"] = combined

    if item_id:
        covers = set(target_scene.get("covers_items") or [])
        try:
            cid = int(item_id)
        except ValueError:
            cid = item_id
        if cid not in covers and str(cid) not in covers:
            covers.add(cid)
            target_scene["covers_items"] = sorted(
                list(covers), key=lambda x: (isinstance(x, str), x)
            )

    return True
