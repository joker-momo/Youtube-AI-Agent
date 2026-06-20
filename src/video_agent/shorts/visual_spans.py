"""Report-only visual-span grouping for Shorts (spec v3.2.3 §10, §11, §11A).

A *visual span* is a planning/grouping sidecar over one or more **contiguous**
editorial scenes that can share a single coherent visual (one continuous clip).
Scene remains the editorial/audio unit; the span never owns an authoritative
duration — it is derived from member scene boundaries.

This module is **pure** (no IO, no LLM). It:

1. reads optional planner hints (``visual_span_id`` / ``visual_span_intent``) off
   the normalized Short scene doc,
2. deterministically validates + repairs the grouping against the §11 invariants
   (and the §11A structured evidence-conflict policy), and
3. emits a schema-v1 ``visual_spans.json`` document with metrics + QA.

Repair strategy: a single forward pass re-derives spans from scene order. A span
only ever extends across contiguous scenes the planner asked to group, and splits
at the first illegal boundary. Output span IDs are always freshly assigned
``vs01, vs02, …`` in order, which deterministically resolves duplicate and
non-contiguous planner IDs (a reused ID becomes two distinct output spans).

In ``report_only`` mode every repair is recorded as a warning and the verdict
stays PASS. In ``enforced`` mode an unrepairable coverage failure yields an error
+ FAIL verdict (used by PR B; PR A only ever runs report-only).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA_VERSION = 1

# Structured-tag field names recognised by the §11A conflict policy. Planner may
# emit these flat sets directly; we additionally fold in the existing nested
# ``required_visual_evidence`` structure for back-compat.
_EVIDENCE_FIELDS = ("required_evidence_tags", "forbidden_evidence_tags")
_SUBJECT_FIELDS = ("required_subject_tags", "forbidden_subject_tags")
_ACTION_FIELDS = ("required_action_tags", "forbidden_action_tags")

# Explicit boolean mode constraints that hard-conflict when both appear in a span.
_MODE_CONFLICT_PAIRS = (
    ("graphic_only", "native_video_only"),
    ("must_show_product", "forbid_product"),
    ("must_show_food", "forbid_food"),
    ("must_show_exercise", "forbid_exercise"),
)

_DEFAULT_SPAN_CONFIG: dict[str, Any] = {
    "enabled": True,
    "mode": "report_only",
    "isolate_hook": True,
    "isolate_cta": True,
    "isolate_graphic_scenes": True,
    "max_scenes_per_span": 3,
    "max_span_sec": 10.0,
}


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def resolve_visual_span_config(channel_config: dict[str, Any]) -> dict[str, Any]:
    """Merge ``shorts.visual_timeline`` config over the §29 defaults.

    Returns the flat span-planning knobs this module needs plus ``mode`` /
    ``enabled``. Unknown / missing keys fall back to defaults; ``mode`` is clamped
    to ``{disabled, report_only, enforced}`` (default ``report_only``).
    """
    cfg = dict(_DEFAULT_SPAN_CONFIG)
    vt = ((channel_config or {}).get("shorts") or {}).get("visual_timeline") or {}
    if "enabled" in vt:
        cfg["enabled"] = bool(vt["enabled"])
    mode = str(vt.get("mode") or cfg["mode"]).strip().lower()
    cfg["mode"] = mode if mode in {"disabled", "report_only", "enforced"} else "report_only"
    planning = vt.get("span_planning") or {}
    for key in ("isolate_hook", "isolate_cta", "isolate_graphic_scenes"):
        if key in planning:
            cfg[key] = bool(planning[key])
    if "max_scenes_per_span" in planning:
        try:
            cfg["max_scenes_per_span"] = max(1, int(planning["max_scenes_per_span"]))
        except (TypeError, ValueError):
            pass
    if "max_span_sec" in planning:
        try:
            cfg["max_span_sec"] = float(planning["max_span_sec"])
        except (TypeError, ValueError):
            pass
    return cfg


# --------------------------------------------------------------------------- #
# Scene classification helpers
# --------------------------------------------------------------------------- #
def _scene_id(scene: dict[str, Any], index: int) -> str:
    return str(scene.get("id") or scene.get("scene_id") or f"s{index + 1}")


def _is_graphic_scene(scene: dict[str, Any]) -> bool:
    layout = str(scene.get("layout") or "").strip().lower()
    if layout.startswith("graphic_"):
        return True
    return str(scene.get("visual_type") or "").strip().lower() == "graphic"


def _is_hook(scene: dict[str, Any]) -> bool:
    return str(scene.get("layout") or "").strip().lower() == "short_hook"


def _is_cta(scene: dict[str, Any]) -> bool:
    return str(scene.get("layout") or "").strip().lower() == "short_cta"


def _scene_duration(scene: dict[str, Any]) -> float:
    try:
        return float(scene.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _planner_span_id(scene: dict[str, Any]) -> str | None:
    raw = scene.get("visual_span_id")
    text = str(raw).strip() if raw is not None else ""
    return text or None


def _planner_intent(scene: dict[str, Any]) -> str:
    return str(scene.get("visual_span_intent") or "").strip()


def _str_tags(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value.strip().lower()} if value.strip() else set()
    if isinstance(value, (list, tuple, set)):
        return {str(v).strip().lower() for v in value if str(v).strip()}
    return set()


# --------------------------------------------------------------------------- #
# §11A — structured evidence-conflict detection
# --------------------------------------------------------------------------- #
def _collect_tag_pair(
    scenes: list[dict[str, Any]], required_field: str, forbidden_field: str
) -> tuple[set[str], set[str]]:
    required: set[str] = set()
    forbidden: set[str] = set()
    for scene in scenes:
        required |= _str_tags(scene.get(required_field))
        forbidden |= _str_tags(scene.get(forbidden_field))
        # Back-compat: fold existing nested required_visual_evidence structure.
        rve = scene.get("required_visual_evidence") or {}
        if isinstance(rve, dict):
            if required_field.startswith("required_subject"):
                required |= _str_tags(rve.get("required_objects"))
                forbidden |= _str_tags(rve.get("forbidden_objects"))
            elif required_field.startswith("required_action"):
                required |= _str_tags(rve.get("required_actions"))
                forbidden |= _str_tags(rve.get("forbidden_actions"))
    return required, forbidden


def detect_structured_span_conflicts(
    member_scenes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Detect §11A *hard* structural conflicts inside a proposed span.

    A hard conflict exists only from structured fields: when the union of a
    required tag set intersects the union of its forbidden counterpart, or when
    two explicit mode constraints (e.g. ``graphic_only`` + ``native_video_only``)
    coexist. Natural-language contradiction is **never** treated as a hard
    conflict here — that is warning-only territory and is left to callers.

    Returns ``{"hard_conflicts": [...], "unresolved_semantic_warnings": [...]}``.
    """
    hard_conflicts: list[dict[str, Any]] = []
    for label, (req_field, forb_field) in {
        "evidence": _EVIDENCE_FIELDS,
        "subject": _SUBJECT_FIELDS,
        "action": _ACTION_FIELDS,
    }.items():
        required, forbidden = _collect_tag_pair(member_scenes, req_field, forb_field)
        overlap = sorted(required & forbidden)
        if overlap:
            hard_conflicts.append(
                {"type": f"{label}_tag_intersection", "tags": overlap}
            )

    flags = {
        name: any(bool(scene.get(name)) for scene in member_scenes)
        for pair in _MODE_CONFLICT_PAIRS
        for name in pair
    }
    for a, b in _MODE_CONFLICT_PAIRS:
        if flags.get(a) and flags.get(b):
            hard_conflicts.append({"type": "mode_constraint", "constraint": [a, b]})

    return {"hard_conflicts": hard_conflicts, "unresolved_semantic_warnings": []}


# --------------------------------------------------------------------------- #
# Forward-pass repair engine
# --------------------------------------------------------------------------- #
def _scene_hint_map(
    scenes: list[dict[str, Any]],
    visual_spans: dict[str, Any] | None,
) -> dict[str, tuple[str | None, str]]:
    """Map ``scene_id -> (planner_span_id, planner_intent)``.

    Source priority: an explicit ``visual_spans`` doc (first-claim-wins resolves
    overlap; later claims are dropped), else per-scene ``visual_span_id`` hints.
    """
    order = [_scene_id(s, i) for i, s in enumerate(scenes)]
    valid_ids = set(order)
    hints: dict[str, tuple[str | None, str]] = {}
    if visual_spans and visual_spans.get("spans"):
        claimed: set[str] = set()
        for span in visual_spans["spans"]:
            span_id = str(span.get("id") or "").strip() or None
            intent = str(span.get("visual_intent") or span.get("visual_span_intent") or "").strip()
            for sid in span.get("scene_ids") or []:
                sid = str(sid)
                if sid not in valid_ids or sid in claimed:
                    continue  # missing ref or overlap → drop (repaired)
                claimed.add(sid)
                hints[sid] = (span_id, intent)
        # Uncovered scenes fall through to per-scene hints / implicit below.
    for idx, scene in enumerate(scenes):
        sid = _scene_id(scene, idx)
        if sid not in hints:
            hints[sid] = (_planner_span_id(scene), _planner_intent(scene))
    return hints


def _classify_span_reason(members: list[dict[str, Any]], source: str) -> tuple[str, str]:
    """Return ``(planned_mode, planning_reason)`` for a finalized span."""
    if all(_is_graphic_scene(s) for s in members):
        return "graphic_led", "graphic isolated"
    if len(members) == 1:
        only = members[0]
        if _is_hook(only):
            return "continuous_clip", "hook isolated"
        if _is_cta(only):
            return "continuous_clip", "cta isolated"
        return "continuous_clip", "single scene"
    reason = "same coherent action and environment" if source == "scene_planner" else "grouped contiguous scenes"
    return "continuous_clip", reason


def validate_and_repair_visual_spans(
    short_scenes: dict[str, Any],
    visual_spans: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Validate + deterministically repair a proposed span grouping (§11).

    ``config`` is the resolved span config (:func:`resolve_visual_span_config`).
    Returns the canonical schema-v1 ``visual_spans`` document (without
    ``generation_mode`` / ``input_hash``, which :func:`build_visual_spans` adds).
    """
    scenes = list((short_scenes or {}).get("scenes") or [])
    cfg = {**_DEFAULT_SPAN_CONFIG, **(config or {})}
    enforced = str(cfg.get("mode")) == "enforced"
    max_scenes = int(cfg["max_scenes_per_span"])
    max_span_sec = float(cfg["max_span_sec"])

    hints = _scene_hint_map(scenes, visual_spans)

    spans: list[dict[str, Any]] = []
    global_warnings: list[str] = []
    invalid_boundary_count = 0

    cur: list[dict[str, Any]] = []
    cur_idx: list[int] = []
    cur_seed_planner: str | None = None
    cur_intent = ""
    cur_repaired = False
    cur_reasons: list[str] = []

    def _flush() -> None:
        nonlocal cur, cur_idx, cur_seed_planner, cur_intent, cur_repaired, cur_reasons
        if not cur:
            return
        source = "scene_planner" if cur_seed_planner else "implicit"
        planned_mode, reason = _classify_span_reason(cur, source)
        span_id = f"vs{len(spans) + 1:02d}"
        spans.append(
            {
                "id": span_id,
                "scene_ids": [_scene_id(s, cur_idx[k]) for k, s in enumerate(cur)],
                "start_scene_index": cur_idx[0],
                "end_scene_index": cur_idx[-1],
                "visual_intent": cur_intent,
                "planned_mode": planned_mode,
                "planning_reason": reason,
                "source": source,
                "repaired": cur_repaired,
                "warnings": list(cur_reasons),
            }
        )
        cur = []
        cur_idx = []
        cur_seed_planner = None
        cur_intent = ""
        cur_repaired = False
        cur_reasons = []

    for idx, scene in enumerate(scenes):
        sid = _scene_id(scene, idx)
        planner_id, intent = hints.get(sid, (None, ""))

        if not cur:
            cur = [scene]
            cur_idx = [idx]
            cur_seed_planner = planner_id
            cur_intent = intent
            cur_reasons = []
            cur_repaired = False
            continue

        # Decide whether `scene` may extend the current span.
        block_reason: str | None = None
        if planner_id is None or cur_seed_planner is None or planner_id != cur_seed_planner:
            block_reason = None  # natural boundary (planner didn't group these)
            # Not a repair — just close the span and start fresh.
            extend = False
        else:
            extend = True
            # Same planner id → must still satisfy every invariant, else it's a
            # forced split (a repair).
            if _is_graphic_scene(scene) != _is_graphic_scene(cur[0]):
                block_reason = f"split_graphic_boundary:{sid}"
            elif cfg["isolate_graphic_scenes"] and _is_graphic_scene(scene):
                block_reason = f"split_graphic_isolation:{sid}"
            elif cfg["isolate_hook"] and (_is_hook(scene) or any(_is_hook(m) for m in cur)):
                block_reason = f"split_hook_isolation:{sid}"
            elif cfg["isolate_cta"] and (_is_cta(scene) or any(_is_cta(m) for m in cur)):
                block_reason = f"split_cta_isolation:{sid}"
            elif len(cur) >= max_scenes:
                block_reason = f"split_max_scenes:{sid}"
            elif sum(_scene_duration(m) for m in cur) + _scene_duration(scene) > max_span_sec + 1e-9:
                block_reason = f"split_max_span_sec:{sid}"
            else:
                conflict = detect_structured_span_conflicts(cur + [scene])
                if conflict["hard_conflicts"]:
                    block_reason = f"split_structured_conflict:{sid}"
            if block_reason:
                extend = False

        if extend:
            cur.append(scene)
            cur_idx.append(idx)
            if not cur_intent and intent:
                cur_intent = intent
        else:
            if block_reason:
                invalid_boundary_count += 1
                cur_repaired = True
                cur_reasons.append(block_reason)
                global_warnings.append(block_reason)
            _flush()
            cur = [scene]
            cur_idx = [idx]
            cur_seed_planner = planner_id
            cur_intent = intent
            cur_reasons = []
            cur_repaired = False

    _flush()

    # ----- metrics ------------------------------------------------------- #
    scene_count = len(scenes)
    span_count = len(spans)
    graphic_span_count = sum(1 for s in spans if s["planned_mode"] == "graphic_led")
    repaired_span_count = sum(1 for s in spans if s.get("repaired"))
    max_scenes_in_span = max((len(s["scene_ids"]) for s in spans), default=0)
    metrics = {
        "scene_count": scene_count,
        "visual_span_count": span_count,
        "average_scenes_per_span": round(scene_count / span_count, 3) if span_count else 0,
        "maximum_scenes_per_span": max_scenes_in_span,
        "estimated_asset_call_reduction": scene_count - span_count,
        "graphic_span_count": graphic_span_count,
        "repaired_span_count": repaired_span_count,
        "invalid_boundary_count": invalid_boundary_count,
    }

    # ----- QA ------------------------------------------------------------ #
    errors: list[str] = []
    covered = [sid for s in spans for sid in s["scene_ids"]]
    expected = [_scene_id(s, i) for i, s in enumerate(scenes)]
    if covered != expected:
        errors.append("incomplete_or_reordered_coverage")
    verdict = "PASS"
    if errors and enforced:
        verdict = "FAIL"
    qa = {"verdict": verdict, "errors": errors, "warnings": list(global_warnings)}

    # Strip the internal ``repaired`` flag from the public span objects but keep
    # it reflected in metrics/warnings.
    public_spans = [{k: v for k, v in s.items() if k != "repaired"} for s in spans]

    return {
        "schema_version": SCHEMA_VERSION,
        "spans": public_spans,
        "metrics": metrics,
        "qa": qa,
    }


# --------------------------------------------------------------------------- #
# Build (public entry)
# --------------------------------------------------------------------------- #
def compute_span_input_hash(
    short_scenes: dict[str, Any], config: dict[str, Any]
) -> str:
    """Stable hash for cache reuse (§40.1).

    Includes ordered scene IDs, planner span IDs/intents, layouts, graphic flags,
    isolation + max-span config, and schema version. Duration changes alone do not
    invalidate grouping unless they cross ``max_span_sec`` (that surfaces via the
    repaired grouping, not the hash).
    """
    scenes = list((short_scenes or {}).get("scenes") or [])
    payload = {
        "schema_version": SCHEMA_VERSION,
        "scenes": [
            {
                "id": _scene_id(s, i),
                "planner_span_id": _planner_span_id(s),
                "planner_intent": _planner_intent(s),
                "layout": str(s.get("layout") or ""),
                "is_graphic": _is_graphic_scene(s),
            }
            for i, s in enumerate(scenes)
        ],
        "config": {
            k: config.get(k)
            for k in (
                "isolate_hook",
                "isolate_cta",
                "isolate_graphic_scenes",
                "max_scenes_per_span",
                "max_span_sec",
            )
        },
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def build_visual_spans(
    short_scenes: dict[str, Any],
    channel_config: dict[str, Any],
    *,
    mode: str | None = None,
    short_id: str | None = None,
) -> dict[str, Any]:
    """Build + validate the visual-span document from a finalized scene plan.

    No LLM call: planner hints are read off the scenes. ``mode`` overrides the
    config mode when provided (defaults to the resolved config mode). The result
    is the canonical schema-v1 document including ``generation_mode``,
    ``input_hash``, ``metrics`` and ``qa``.
    """
    config = resolve_visual_span_config(channel_config)
    effective_mode = (mode or config["mode"]).strip().lower()
    if effective_mode not in {"disabled", "report_only", "enforced"}:
        effective_mode = "report_only"
    config = {**config, "mode": effective_mode}

    # Per-scene planner hints feed the repair engine directly (an empty spans doc
    # makes _scene_hint_map fall back to each scene's visual_span_id).
    result = validate_and_repair_visual_spans(short_scenes, {"spans": []}, config)

    result = {
        "schema_version": SCHEMA_VERSION,
        "short_id": short_id or (short_scenes or {}).get("short_id") or "",
        "generation_mode": effective_mode,
        "input_hash": compute_span_input_hash(short_scenes, config),
        **result,
    }
    return result


def assign_span_ids_to_scenes(
    short_scenes: dict[str, Any], visual_spans: dict[str, Any]
) -> None:
    """Attach repaired ``visual_span_id`` onto each scene in place (compat field).

    Does not invent ``visual_span_intent`` — only the repaired span ID, so the
    scene doc reflects the authoritative grouping. Scene durations are untouched.
    """
    by_scene: dict[str, str] = {}
    for span in visual_spans.get("spans") or []:
        for sid in span.get("scene_ids") or []:
            by_scene[str(sid)] = str(span.get("id"))
    for idx, scene in enumerate(list((short_scenes or {}).get("scenes") or [])):
        sid = _scene_id(scene, idx)
        if sid in by_scene:
            scene["visual_span_id"] = by_scene[sid]
