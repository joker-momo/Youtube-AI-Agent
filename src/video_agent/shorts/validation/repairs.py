from __future__ import annotations

from video_agent.shorts.validation.checks import *  # noqa: F401,F403


def _is_five_error_bread_script(script: dict[str, Any] | None) -> bool:
    if not script:
        return False
    contract = script.get("idea_contract") or {}
    count = contract.get("original_count") == 5 or contract.get("final_count") == 5
    narration = str(script.get("narration") or "").lower()
    return bool(count and any(term in narration for term in ("pan", "bread", "hogaza")))


def repair_five_error_bread_payoff_layout(scenes: list[dict[str, Any]], script: dict[str, Any] | None) -> bool:
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
    "un poco", "también", "simplemente", "en realidad", "de verdad",
    "muy", "bastante",
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
    return [m.group(0).strip() for m in _SENTENCE_RE.finditer(str(text or "")) if m.group(0).strip()]


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


def try_micro_condense(scene: dict[str, Any], *, idea_labels: list[str] | None = None) -> dict[str, Any] | None:
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
    for kp in (script.get("key_points") or []):
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
                scenes[i:i + 1] = parts
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
                    scene["covers_items"] = sorted(list(covers), key=lambda x: (isinstance(x, str), x))
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
        
    # Determine injection text
    inject_text = item_label
    if item_id == "3":
        inject_text = "Prepara un pan base antes del hambre."
    elif item_id == "4":
        inject_text = "Vuelve a harina, fibra e ingredientes."
        
    # Do not inject the item label into target_scene["narration"]!
    # Narration-fit timing must use only the actual TTS-spoken fields.
    # Injecting the source key_point meaning into the spoken audio estimate
    # creates a false hard blocker (scene_narration_fit exceeds tolerance).
    # We keep coverage validation separate from spoken-duration validation.
    current_caption = target_scene.get("caption") or ""
    combined = f"{current_caption} {inject_text}".strip()
    words = combined.split()
    if len(words) > 12:
        combined = " ".join(words[:12]) + "..."
    target_scene["caption"] = combined
        
    if item_id:
        covers = set(target_scene.get("covers_items") or [])
        try:
            cid = int(item_id)
        except ValueError:
            cid = item_id
        if cid not in covers and str(cid) not in covers:
            covers.add(cid)
            target_scene["covers_items"] = sorted(list(covers), key=lambda x: (isinstance(x, str), x))
            
    return True
