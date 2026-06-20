"""PR E sequence-level QA for selected visual beats."""

from __future__ import annotations

from collections import Counter
from typing import Any

from video_agent.shorts.visual_acquisition import CONTRACT_REVISION, stable_hash

SCHEMA_VERSION = 1


def _selected_plan(span: dict[str, Any]) -> dict[str, Any] | None:
    plan = span.get("selected_plan")
    return plan if isinstance(plan, dict) else None


def _beat_requires_media_track(beat: dict[str, Any]) -> bool:
    return str(beat.get("type") or "") != "graphic"


def build_visual_sequence_qa(
    *,
    short_id: str,
    visual_beat_plan: dict[str, Any],
) -> dict[str, Any]:
    """Build canonical ``visual_sequence_qa.json``.

    Sequence QA counts selected beats/tracks rather than raw scenes, preserving
    the renderer boundary that compiled schedule remains the only render input.
    """
    errors: list[str] = []
    warnings: list[str] = []
    distribution: Counter[str] = Counter()
    beat_count = 0
    track_count = 0
    graphic_beat_count = 0
    cut_count_changes = 0
    span_reports: list[dict[str, Any]] = []

    for span in visual_beat_plan.get("spans") or []:
        span_id = str(span.get("visual_span_id") or "")
        selected = _selected_plan(span)
        span_errors: list[str] = []
        span_warnings: list[str] = list((span.get("qa") or {}).get("warnings") or [])
        if not selected:
            span_errors.append("missing_selected_plan")
            errors.append(f"missing_selected_plan:{span_id}")
            span_reports.append(
                {
                    "visual_span_id": span_id,
                    "selected_mode": None,
                    "beat_count": 0,
                    "track_count": 0,
                    "graphic_beat_count": 0,
                    "qa": {"verdict": "FAIL", "errors": span_errors, "warnings": span_warnings},
                }
            )
            continue

        mode = str(selected.get("mode") or "")
        beats = list(selected.get("beats") or [])
        distribution[mode] += 1
        span_beat_count = len(beats)
        span_track_count = sum(1 for beat in beats if _beat_requires_media_track(beat))
        span_graphic_count = sum(1 for beat in beats if str(beat.get("type") or "") == "graphic")
        beat_count += span_beat_count
        track_count += span_track_count
        graphic_beat_count += span_graphic_count
        cut_count_changes += max(0, span_beat_count - 1)

        for beat in beats:
            if beat.get("inside_scene_boundary") is True and not beat.get("timing_anchor_ref"):
                span_errors.append(f"inside_scene_boundary_without_anchor:{beat.get('beat_id')}")
            if beat.get("boundary_reason") == "sentence_punctuation":
                span_errors.append(f"punctuation_boundary:{beat.get('beat_id')}")
        span_verdict = (span.get("qa") or {}).get("verdict") or "PASS"
        if span_errors:
            errors.extend(f"{span_id}:{err}" for err in span_errors)
            span_verdict = "FAIL"
        elif span_verdict == "CAPABILITY_REDUCED":
            warnings.append(f"capability_reduced:{span_id}")
        span_reports.append(
            {
                "visual_span_id": span_id,
                "selected_mode": mode,
                "selection_reason": span.get("selection_reason"),
                "beat_count": span_beat_count,
                "track_count": span_track_count,
                "graphic_beat_count": span_graphic_count,
                "qa": {
                    "verdict": span_verdict,
                    "errors": span_errors,
                    "warnings": span_warnings,
                },
            }
        )

    span_verdicts = [(span.get("qa") or {}).get("verdict") for span in span_reports]
    overall = (
        "FAIL"
        if errors or any(v == "FAIL" for v in span_verdicts)
        else (
            "CAPABILITY_REDUCED"
            if any(v == "CAPABILITY_REDUCED" for v in span_verdicts)
            else "PASS"
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "contract_revision": CONTRACT_REVISION,
        "short_id": short_id,
        "input_hash": stable_hash(visual_beat_plan),
        "created_by_stage": "visual_sequence_qa",
        "summary": {
            "span_count": len(span_reports),
            "beat_count": beat_count,
            "track_count": track_count,
            "graphic_beat_count": graphic_beat_count,
            "cut_count_changes": cut_count_changes,
            "plan_distribution": dict(sorted(distribution.items())),
        },
        "spans": span_reports,
        "qa": {"verdict": overall, "errors": errors, "warnings": warnings},
    }
