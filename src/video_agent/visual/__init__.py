"""Long-form visual-planning core (independent of ``video_agent.shorts``).

This package owns the long-form **visual span** layer: grouping contiguous
editorial scenes that can share a single continuous Pexels background clip
(layer 1 of the 3-layer long-form composition). It is written from scratch for
long-form and must NOT import from ``video_agent.shorts`` — the shorts modules
are used only as a design reference.

Phase 1 scope: pure span grouping + report. No IO, no LLM, no rendering.
"""

from __future__ import annotations

from video_agent.visual.config import (
    DEFAULT_SPAN_CONFIG,
    resolve_visual_span_config,
)
from video_agent.visual.spans import (
    SCHEMA_VERSION,
    assign_span_ids_to_scenes,
    build_visual_spans,
    compute_span_input_hash,
    validate_and_repair_visual_spans,
    verify_span_invariants,
)

__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_SPAN_CONFIG",
    "resolve_visual_span_config",
    "build_visual_spans",
    "validate_and_repair_visual_spans",
    "verify_span_invariants",
    "compute_span_input_hash",
    "assign_span_ids_to_scenes",
]
