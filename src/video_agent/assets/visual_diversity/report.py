"""Visual diversity report (JSON + Markdown) per spec §26–§27."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .loader import classify_video_length


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def visual_diversity_score(report: dict[str, Any]) -> float:
    """Compute diversity score from a populated report dict (spec §27)."""
    scene_count = max(1, int(report.get("scene_count") or 0))
    video_length_profile = str(report.get("video_length_profile") or "long")
    distinct_buckets = len(report.get("bucket_distribution", {}) or {})
    distinct_shots = len(report.get("shot_type_distribution", {}) or {})

    # Measure creator diversity against total scenes, not only scenes with
    # known creator metadata, so placeholders / graphics / unknown creators
    # cannot make the metric look artificially better.
    creator_dist = dict(report.get("creator_distribution") or {})
    known_creator_total = sum(int(value) for value in creator_dist.values())
    unknown_creator_count = max(0, scene_count - known_creator_total)
    if unknown_creator_count:
        creator_dist["unknown"] = int(creator_dist.get("unknown", 0)) + unknown_creator_count

    max_creator_ratio = (
        max(int(value) for value in creator_dist.values()) / scene_count
        if creator_dist
        else 0.0
    )
    reused_recent_assets = len(report.get("reuse_warnings", []) or [])
    locale_count = int(report.get("spain_or_mediterranean_scene_count", 0) or 0)
    rendered_cards = int(report.get("graphic_cards_rendered", 0) or 0)

    # v5.6 requires `graphic_cards_target` in fresh reports. Older reports may
    # not have it; use a safe compatibility fallback so short videos are not
    # penalized for omitted card targets.
    if "graphic_cards_target" in report:
        target_cards = int(report.get("graphic_cards_target") or 0)
    else:
        target_cards = 0 if video_length_profile == "short" else 4

    bucket_denominator = min(max(scene_count, 6), 8)
    shot_denominator = min(max(scene_count, 4), 6)

    distinct_bucket_ratio = min(1.0, distinct_buckets / bucket_denominator)
    distinct_shot_type_ratio = min(1.0, distinct_shots / shot_denominator)
    creator_diversity = 1.0 - min(1.0, max_creator_ratio)
    low_reuse = 1.0 - min(1.0, reused_recent_assets / scene_count)
    locale_fit = min(1.0, locale_count / scene_count)
    card_score = 1.0 if target_cards <= 0 else min(1.0, rendered_cards / target_cards)

    return round(
        distinct_bucket_ratio * 0.25
        + distinct_shot_type_ratio * 0.20
        + creator_diversity * 0.15
        + low_reuse * 0.20
        + locale_fit * 0.10
        + card_score * 0.10,
        4,
    )


def build_report(
    *,
    job_id: str,
    channel_id: str,
    rollout_mode: str,
    plans: list[dict[str, Any]],
    selections: list[dict[str, Any]] | None = None,
    visual_dna: dict[str, Any] | None = None,
    api_budget_stats: dict[str, Any] | None = None,
    placeholder_count: int = 0,
    baseline_placeholder_ratio: float | None = None,
    reuse_warnings: list[Any] | None = None,
    negative_pattern_warnings: list[Any] | None = None,
    qa_verdict: str = "pass_with_report",
    graphic_cards_supported: bool = False,
    graphic_cards_rendered: int = 0,
    graphic_cards_planned: int = 0,
    graphic_cards_planned_scene_ids: list[str] | None = None,
    dry_run_selection_changes_count: int = 0,
    dry_run_selection_changes_path: str | None = None,
    baseline_visual_diversity_score: float | None = None,
) -> dict[str, Any]:
    """Assemble the canonical report dict."""
    selections = selections or []
    visual_dna = visual_dna or {}
    scene_count = len(plans)

    length_profile = classify_video_length(scene_count, visual_dna)

    bucket_distribution = Counter(p.get("visual_bucket", "unknown") for p in plans)
    shot_distribution = Counter(p.get("shot_type", "unknown") for p in plans)
    creator_distribution = Counter(
        sel.get("creator_key") or "unknown" for sel in selections
    ) if selections else Counter()

    placeholder_ratio = round(placeholder_count / scene_count, 4) if scene_count else 0.0

    locale_count = sum(
        1
        for sel in selections
        if (sel.get("locale_feel") or "").lower() in {"spain", "mediterranean", "european"}
    )

    semantic_present = {
        "original_query": all(bool(sel.get("original_query")) for sel in selections) if selections else False,
        "provider_tags_json": all(bool(sel.get("provider_tags_json")) for sel in selections) if selections else False,
    }

    report: dict[str, Any] = {
        "job_id": job_id,
        "channel_id": channel_id,
        "provider_policy": "pexels_only",
        "scene_count": scene_count,
        "video_length_profile": length_profile,
        "rollout_mode": rollout_mode,
        "assets_selected": len(selections),
        "graphic_cards_rendered": graphic_cards_rendered,
        "graphic_cards_planned": graphic_cards_planned,
        "graphic_cards_supported": graphic_cards_supported,
        "graphic_cards_target": int(
            (visual_dna.get("video_length_profiles", {}).get(length_profile, {}) or {})
            .get("min_local_graphic_cards", 0) or 0
        ),
        "graphic_cards_planned_scene_ids": list(graphic_cards_planned_scene_ids or []),
        "dry_run_selection_changes_count": int(dry_run_selection_changes_count or 0),
        "dry_run_selection_changes_path": dry_run_selection_changes_path,
        "bucket_distribution": dict(bucket_distribution),
        "shot_type_distribution": dict(shot_distribution),
        "creator_distribution": dict(creator_distribution),
        "api_budget": api_budget_stats or {},
        "placeholder_count": placeholder_count,
        "placeholder_ratio": placeholder_ratio,
        "baseline_placeholder_ratio": baseline_placeholder_ratio,
        "spain_or_mediterranean_scene_count": locale_count,
        "reuse_warnings": reuse_warnings or [],
        "negative_pattern_warnings": negative_pattern_warnings or [],
        "semantic_metadata_present": semantic_present,
        "qa_verdict": qa_verdict,
    }
    report["visual_diversity_score"] = visual_diversity_score(report)
    report["baseline_visual_diversity_score"] = baseline_visual_diversity_score
    if baseline_visual_diversity_score is not None:
        report["delta_vs_baseline"] = round(
            report["visual_diversity_score"] - baseline_visual_diversity_score, 4
        )
    else:
        report["delta_vs_baseline"] = None
    return report


def _markdown_dict(title: str, data: dict[str, Any]) -> str:
    if not data:
        return f"### {title}\n\n_empty_\n"
    lines = [f"### {title}", ""]
    for key, value in sorted(data.items()):
        lines.append(f"- **{key}**: {value}")
    return "\n".join(lines) + "\n"


def render_markdown(report: dict[str, Any]) -> str:
    parts = [
        f"# Visual Diversity Report — {report.get('job_id')}",
        "",
        f"- channel: `{report.get('channel_id')}`",
        f"- provider_policy: `{report.get('provider_policy')}`",
        f"- scene_count: {report.get('scene_count')}",
        f"- video_length_profile: {report.get('video_length_profile')}",
        f"- rollout_mode: {report.get('rollout_mode')}",
        f"- qa_verdict: **{report.get('qa_verdict')}**",
        f"- visual_diversity_score: {report.get('visual_diversity_score')}",
        f"- delta_vs_baseline: {report.get('delta_vs_baseline')}",
        f"- placeholder_ratio: {report.get('placeholder_ratio')}",
        f"- baseline_placeholder_ratio: {report.get('baseline_placeholder_ratio')}",
        "",
        _markdown_dict("Bucket distribution", report.get("bucket_distribution", {})),
        _markdown_dict("Shot-type distribution", report.get("shot_type_distribution", {})),
        _markdown_dict("Creator distribution", report.get("creator_distribution", {})),
        _markdown_dict("API budget", report.get("api_budget", {})),
    ]
    warnings = report.get("reuse_warnings") or []
    neg = report.get("negative_pattern_warnings") or []
    if warnings:
        parts.append("### Reuse warnings\n")
        parts.extend(f"- {w}" for w in warnings)
        parts.append("")
    if neg:
        parts.append("### Negative pattern warnings\n")
        parts.extend(f"- {w}" for w in neg)
        parts.append("")
    return "\n".join(parts) + "\n"


def write_report(report: dict[str, Any], outputs_dir: Path) -> tuple[Path, Path]:
    outputs_dir = Path(outputs_dir)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    json_path = outputs_dir / "visual-diversity-report.json"
    md_path = outputs_dir / "visual-diversity-report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
