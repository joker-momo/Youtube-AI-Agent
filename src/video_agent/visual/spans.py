"""Long-form visual-span grouping (independent core, pure).

A *visual span* groups one or more **contiguous** long-form editorial scenes that
can share a single continuous Pexels background clip (layer 1 of the 3-layer
long-form composition). The scene stays the editorial/audio unit; a span never
owns an authoritative duration — it is derived from member scene boundaries.

Design reference (NOT imported): ``video_agent.shorts.visual_spans``. This module
is rewritten for long-form semantics:

* Long-form layouts are ``hook | subtitle | checklist | warning | quote | cta``.
* Phase 1 (Gate 0, 2026-06-24) grouping rule: only contiguous ``subtitle`` scenes
  may merge; ``hook`` and graphic layouts (``checklist``/``warning``/``quote``/
  ``cta``) are each isolated to their own single-scene span.
* Graphic scenes (incl. ``cta`` per MASTER-PLAN G1) will be rendered as
  ChatGPT-generated images (not Pexels), so their span is tagged ``graphic_image``
  and never carries a background clip. ``hook`` keeps its continuous Pexels clip.
* No evidence/tag conflict machinery (long-form scenes carry no such tags); it
  can be added later if the scene schema grows those fields.

The module is **pure**: no IO, no LLM. Output is a schema-v1 ``visual_spans``
document with ``spans``, ``metrics`` and ``qa``. In ``report_only`` mode the
verdict is always PASS; ``enforced`` mode can FAIL on a coverage error.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from video_agent.visual.config import (
    DEFAULT_SPAN_CONFIG,
    resolve_visual_span_config,
)

SCHEMA_VERSION = 1

# Long-form graphic layouts → rendered as ChatGPT images, always isolated.
# MASTER-PLAN G1 (canonical, 2026-06-24): checklist/warning/quote/cta all move
# to ChatGPT images. ``hook`` joined them 2026-06-29 (user request): a designed,
# attention-grabbing gen-image hook reads better + avoids the weak-stock-match
# QA block that a generic hook visual_prompt kept tripping.
GRAPHIC_LAYOUTS = frozenset({"hook", "checklist", "warning", "quote", "cta"})


# --------------------------------------------------------------------------- #
# Scene classification helpers
# --------------------------------------------------------------------------- #
def _scene_id(scene: dict[str, Any], index: int) -> str:
    return str(scene.get("id") or scene.get("scene_id") or f"scene-{index + 1:02d}")


def _layout(scene: dict[str, Any]) -> str:
    return str(scene.get("layout") or "").strip().lower()


def _is_hook(scene: dict[str, Any]) -> bool:
    return _layout(scene) == "hook"


def _is_cta(scene: dict[str, Any]) -> bool:
    return _layout(scene) == "cta"


def _is_graphic(scene: dict[str, Any]) -> bool:
    return _layout(scene) in GRAPHIC_LAYOUTS


def _is_groupable(scene: dict[str, Any], cfg: dict[str, Any]) -> bool:
    return _layout(scene) in set(cfg.get("groupable_layouts") or ())


def _is_isolated(scene: dict[str, Any], cfg: dict[str, Any]) -> bool:
    """A scene that must always be its own single-scene span."""
    if cfg.get("isolate_hook") and _is_hook(scene):
        return True
    if cfg.get("isolate_cta") and _is_cta(scene):
        return True
    if cfg.get("isolate_graphic_scenes") and _is_graphic(scene):
        return True
    return False


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


def _classify_span(members: list[dict[str, Any]]) -> tuple[str, str]:
    """Return ``(planned_mode, planning_reason)`` for a finalized span."""
    if all(_is_graphic(s) for s in members):
        return "graphic_image", "graphic isolated (ChatGPT image)"
    if len(members) == 1:
        # Note: cta is in GRAPHIC_LAYOUTS, so a lone cta is already classified as
        # graphic_image above; only hook stays a single continuous Pexels clip.
        if _is_hook(members[0]):
            return "continuous_clip", "hook isolated"
        return "continuous_clip", "single scene"
    return "continuous_clip", "grouped contiguous subtitle scenes"


# --------------------------------------------------------------------------- #
# Invariant self-audit (regression guard — gives ``enforced`` real teeth)
# --------------------------------------------------------------------------- #
def verify_span_invariants(
    spans: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> list[str]:
    """Re-check a finalized span list against the Phase-1 grouping invariants.

    Defensive guard: it audits the *output* independently of how it was produced,
    so a future regression in the grouping engine surfaces as a QA error instead
    of a silently malformed schedule. Returns a list of error tokens (empty ==
    clean). Wired into the QA ``errors`` list; in ``enforced`` mode any token
    flips the verdict to FAIL.
    """
    cfg = {**DEFAULT_SPAN_CONFIG, **(cfg or {})}
    max_scenes = int(cfg["max_scenes_per_span"])
    max_span_sec = float(cfg["max_span_sec"])
    by_id = {_scene_id(s, i): s for i, s in enumerate(scenes)}
    index_of = {_scene_id(s, i): i for i, s in enumerate(scenes)}

    errors: list[str] = []
    for span in spans:
        sid_list = list(span.get("scene_ids") or [])
        members = [by_id[s] for s in sid_list if s in by_id]
        label = span.get("id") or "?"

        if len(sid_list) > max_scenes:
            errors.append(f"invariant_max_scenes:{label}")
        if len(members) > 1 and (
            sum(_scene_duration(m) for m in members) > max_span_sec + 1e-9
        ):
            errors.append(f"invariant_max_span_sec:{label}")

        idxs = [index_of[s] for s in sid_list if s in index_of]
        if idxs and idxs != list(range(idxs[0], idxs[0] + len(idxs))):
            errors.append(f"invariant_non_contiguous:{label}")

        if len(members) > 1:
            if any(_is_graphic(m) for m in members):
                errors.append(f"invariant_graphic_isolation:{label}")
            if any(_is_hook(m) for m in members):
                errors.append(f"invariant_hook_isolation:{label}")
            if not all(_is_groupable(m, cfg) for m in members):
                errors.append(f"invariant_non_groupable_merge:{label}")
    return errors


# --------------------------------------------------------------------------- #
# Forward-pass grouping engine
# --------------------------------------------------------------------------- #
def validate_and_repair_visual_spans(
    scene_doc: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Group long-form scenes into visual spans via a single forward pass.

    Greedy implicit grouping: contiguous *groupable* scenes (default: ``subtitle``)
    that share a planner span id (or have none) merge until an isolation rule, a
    layout change, ``max_scenes_per_span`` or ``max_span_sec`` stops them. Output
    span ids are freshly assigned ``vs01, vs02, …`` in scene order.

    ``config`` is the resolved span config (:func:`resolve_visual_span_config`).
    Returns the schema-v1 ``visual_spans`` body (``build_visual_spans`` adds the
    ``generation_mode`` / ``input_hash`` envelope).
    """
    scenes = list((scene_doc or {}).get("scenes") or [])
    cfg = {**DEFAULT_SPAN_CONFIG, **(config or {})}
    enforced = str(cfg.get("mode")) == "enforced"
    max_scenes = int(cfg["max_scenes_per_span"])
    max_span_sec = float(cfg["max_span_sec"])

    spans: list[dict[str, Any]] = []
    global_warnings: list[str] = []
    cap_split_count = 0
    repaired_split_count = 0

    cur: list[dict[str, Any]] = []
    cur_idx: list[int] = []
    cur_seed_pid: str | None = None
    cur_intent = ""
    cur_repaired = False
    cur_reasons: list[str] = []

    def _flush() -> None:
        nonlocal cur, cur_idx, cur_seed_pid, cur_intent, cur_repaired, cur_reasons
        if not cur:
            return
        planned_mode, reason = _classify_span(cur)
        spans.append(
            {
                "id": f"vs{len(spans) + 1:02d}",
                "scene_ids": [_scene_id(s, cur_idx[k]) for k, s in enumerate(cur)],
                "start_scene_index": cur_idx[0],
                "end_scene_index": cur_idx[-1],
                "visual_intent": cur_intent,
                "planned_mode": planned_mode,
                "planning_reason": reason,
                "source": "scene_planner" if cur_seed_pid else "implicit",
                "repaired": cur_repaired,
                "warnings": list(cur_reasons),
            }
        )
        cur = []
        cur_idx = []
        cur_seed_pid = None
        cur_intent = ""
        cur_repaired = False
        cur_reasons = []

    for idx, scene in enumerate(scenes):
        sid = _scene_id(scene, idx)
        pid = _planner_span_id(scene)
        intent = _planner_intent(scene)

        if not cur:
            cur = [scene]
            cur_idx = [idx]
            cur_seed_pid = pid
            cur_intent = intent
            continue

        seed = cur[0]
        extend = True
        block_reason: str | None = None
        repair = False

        if _is_isolated(scene, cfg) or _is_isolated(seed, cfg):
            extend = False  # natural isolation boundary (no repair)
        elif not (_is_groupable(scene, cfg) and _is_groupable(seed, cfg)):
            extend = False  # layout not groupable → natural boundary
        elif cur_seed_pid is not None and pid is not None and pid != cur_seed_pid:
            extend = False  # planner put them in different spans → natural boundary
        elif len(cur) >= max_scenes:
            extend = False
            block_reason = f"split_max_scenes:{sid}"
            # Repair only if a planner hint explicitly asked to keep them together.
            repair = cur_seed_pid is not None and pid == cur_seed_pid
        elif sum(_scene_duration(m) for m in cur) + _scene_duration(scene) > max_span_sec + 1e-9:
            extend = False
            block_reason = f"split_max_span_sec:{sid}"
            repair = cur_seed_pid is not None and pid == cur_seed_pid

        if extend:
            cur.append(scene)
            cur_idx.append(idx)
            if not cur_intent and intent:
                cur_intent = intent
            continue

        # Close current span; the blocking scene seeds the next one.
        if block_reason:
            cap_split_count += 1
            global_warnings.append(block_reason)
            if repair:
                repaired_split_count += 1
                cur_repaired = True
                cur_reasons.append(block_reason)
        _flush()
        cur = [scene]
        cur_idx = [idx]
        cur_seed_pid = pid
        cur_intent = intent

    _flush()

    # ----- metrics ------------------------------------------------------- #
    scene_count = len(scenes)
    span_count = len(spans)
    continuous_spans = [s for s in spans if s["planned_mode"] == "continuous_clip"]
    graphic_span_count = sum(1 for s in spans if s["planned_mode"] == "graphic_image")
    non_graphic_scene_count = sum(1 for s in scenes if not _is_graphic(s))
    metrics = {
        "scene_count": scene_count,
        "visual_span_count": span_count,
        "average_scenes_per_span": round(scene_count / span_count, 3) if span_count else 0,
        "maximum_scenes_per_span": max((len(s["scene_ids"]) for s in spans), default=0),
        "continuous_clip_span_count": len(continuous_spans),
        "graphic_span_count": graphic_span_count,
        # How many fewer Pexels background clips vs the 1-clip-per-scene baseline.
        "estimated_background_clip_reduction": max(
            0, non_graphic_scene_count - len(continuous_spans)
        ),
        "cap_split_count": cap_split_count,
        "repaired_span_count": repaired_split_count,
    }

    # ----- QA ------------------------------------------------------------ #
    errors: list[str] = []
    covered = [sid for s in spans for sid in s["scene_ids"]]
    expected = [_scene_id(s, i) for i, s in enumerate(scenes)]
    if covered != expected:
        errors.append("incomplete_or_reordered_coverage")
    errors.extend(verify_span_invariants(spans, scenes, cfg))
    verdict = "FAIL" if (errors and enforced) else "PASS"
    qa = {"verdict": verdict, "errors": errors, "warnings": list(global_warnings)}

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
    scene_doc: dict[str, Any], config: dict[str, Any]
) -> str:
    """Stable hash for cache reuse.

    Includes ordered scene ids, planner span ids/intents, layouts, and the
    grouping config. Duration changes alone do not invalidate grouping unless
    they cross ``max_span_sec`` (which surfaces via the grouping itself).
    """
    scenes = list((scene_doc or {}).get("scenes") or [])
    payload = {
        "schema_version": SCHEMA_VERSION,
        "scenes": [
            {
                "id": _scene_id(s, i),
                "planner_span_id": _planner_span_id(s),
                "planner_intent": _planner_intent(s),
                "layout": _layout(s),
            }
            for i, s in enumerate(scenes)
        ],
        "config": {
            k: config.get(k)
            for k in (
                "isolate_hook",
                "isolate_cta",
                "isolate_graphic_scenes",
                "groupable_layouts",
                "max_scenes_per_span",
                "max_span_sec",
            )
        },
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=list).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def build_visual_spans(
    scene_doc: dict[str, Any],
    channel_config: dict[str, Any],
    *,
    mode: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Build + validate the long-form visual-span document from a scene plan.

    No LLM call: planner hints (``visual_span_id``) are read off the scenes; when
    absent, contiguous groupable scenes are auto-grouped. ``mode`` overrides the
    resolved config mode when provided. Returns the canonical schema-v1 document
    including ``generation_mode``, ``input_hash``, ``metrics`` and ``qa``.
    """
    config = resolve_visual_span_config(channel_config)
    effective_mode = (mode or config["mode"]).strip().lower()
    if effective_mode not in {"disabled", "report_only", "enforced"}:
        effective_mode = "report_only"
    config = {**config, "mode": effective_mode}

    body = validate_and_repair_visual_spans(scene_doc, config)
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id or (scene_doc or {}).get("job_id") or "",
        "generation_mode": effective_mode,
        "input_hash": compute_span_input_hash(scene_doc, config),
        **body,
    }


def assign_span_ids_to_scenes(
    scene_doc: dict[str, Any], visual_spans: dict[str, Any]
) -> None:
    """Attach the resolved ``visual_span_id`` onto each scene in place.

    Reflects the authoritative grouping back onto the scene doc (compat field).
    Scene durations and all other fields are untouched.
    """
    by_scene: dict[str, str] = {}
    for span in visual_spans.get("spans") or []:
        for sid in span.get("scene_ids") or []:
            by_scene[str(sid)] = str(span.get("id"))
    for idx, scene in enumerate(list((scene_doc or {}).get("scenes") or [])):
        sid = _scene_id(scene, idx)
        if sid in by_scene:
            scene["visual_span_id"] = by_scene[sid]
