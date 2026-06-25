"""Per-span continuous-clip acquisition orchestration (long-form, pure core).

For each ``continuous_clip`` visual span we want ONE Pexels clip that covers the
whole span's combined visual intent (so it can play continuously across the
member scenes instead of a per-scene cut), vetted by the same quality cascade the
per-scene path uses (semantic gate, SigLIP age-gate 45+, weak-match guard).

This module is the **pure orchestrator**: it builds the acquisition context per
span and drives the flow, but delegates the two heavy, environment-dependent
steps to injected callables so it stays testable without the live provider/models:

* ``candidate_fn`` — metadata-only span search, e.g.
  ``StockAssetService.get_visual_span_candidates`` (no download).
* ``select_and_download_fn(context, candidates) -> {"path", "duration_sec"} | None``
  — runs the quality cascade on the candidates, downloads + mirrors the winner
  render-ready, and returns its render-relative path. Returns ``None`` when no
  candidate passes the quality bar.

Output is a ``source_clips`` map (``scene_id -> {path, duration_sec}``) suitable
for :func:`video_agent.visual.compile_asset_schedule`. A span that yields no
passing clip is simply omitted → the schedule **fails closed** to per-scene
clips for that span (never an off-topic reuse). Independent of
``video_agent.shorts``.
"""

from __future__ import annotations

from typing import Any, Callable

__all__ = ["build_span_acquisition_context", "acquire_span_source_clips"]

CandidateFn = Callable[..., list[dict[str, Any]]]
SelectDownloadFn = Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any] | None]


def build_span_acquisition_context(
    span: dict[str, Any],
    scenes_by_id: dict[str, dict[str, Any]],
    *,
    locale: str = "es-ES",
) -> dict[str, Any]:
    """Build the metadata search context for one span from its member scenes."""
    members = [scenes_by_id[s] for s in (span.get("scene_ids") or []) if s in scenes_by_id]
    prompts = [
        str(m.get("visual_prompt") or m.get("on_screen_text") or m.get("caption") or "").strip()
        for m in members
    ]
    combined = " ".join(p for p in prompts if p)
    return {
        "span_id": str(span.get("id") or ""),
        "scene_ids": list(span.get("scene_ids") or []),
        "locale": locale,
        "visual_prompt": combined[:600],
        "visual_intent": str(span.get("visual_intent") or ""),
        "planned_duration_sec": sum(float(m.get("duration_sec") or 0.0) for m in members),
    }


def acquire_span_source_clips(
    visual_spans: dict[str, Any],
    scene_doc: dict[str, Any],
    *,
    candidate_fn: CandidateFn,
    select_and_download_fn: SelectDownloadFn,
    budget: Any,
    locale: str = "es-ES",
) -> dict[str, dict[str, Any]]:
    """Acquire one continuous clip per multi-scene span; return a ``source_clips`` map.

    Only multi-scene ``continuous_clip`` spans are acquired (a single-scene span
    already uses its own prepared clip; ``graphic_image`` spans are ChatGPT
    images). Spans with no passing candidate are omitted → schedule fails closed.
    """
    scenes_by_id = {
        str(s.get("id") or s.get("scene_id") or ""): s
        for s in (scene_doc or {}).get("scenes") or []
    }
    source_clips: dict[str, dict[str, Any]] = {}
    for span in (visual_spans or {}).get("spans") or []:
        if span.get("planned_mode") != "continuous_clip":
            continue
        scene_ids = [s for s in (span.get("scene_ids") or []) if s in scenes_by_id]
        if len(scene_ids) < 2:
            continue  # single scene → keep its own per-scene clip

        context = build_span_acquisition_context(span, scenes_by_id, locale=locale)
        candidates = candidate_fn(acquisition_context=context, budget=budget) or []
        if not candidates:
            continue
        chosen = select_and_download_fn(context, candidates)
        if not chosen or not chosen.get("path"):
            continue  # nothing passed the quality bar → fail closed to per-scene

        clip = {"path": str(chosen["path"]), "duration_sec": float(chosen.get("duration_sec") or 0.0)}
        for sid in scene_ids:
            source_clips[sid] = clip
    return source_clips
