"""Glue layer between the rest of the pipeline and the visual diversity modules.

Designed for minimally-invasive wiring into `prepare_assets`. The rollback
switch (`visuals.diversity.enabled = false`) makes every entry point a no-op
so the legacy `StockAssetService` selection path keeps working unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .api_budget import ApiBudget, is_backoff_active
from .graphic_cards import detect_renderer_caps
from .helpers import resolve_video_topic
from .loader import classify_video_length, load_visual_dna
from .placeholder_baseline import resolve_placeholder_baseline
from .planner import plan_report_only_graphic_cards, plan_scenes
from .report import build_report, write_report


@dataclass
class VisualDiversityRun:
    enabled: bool
    rollout_mode: str
    plans: list[dict[str, Any]] = field(default_factory=list)
    visual_dna: dict[str, Any] = field(default_factory=dict)
    visual_config: dict[str, Any] = field(default_factory=dict)
    renderer_caps: dict[str, Any] = field(default_factory=dict)
    selections: list[dict[str, Any]] = field(default_factory=list)
    placeholder_count: int = 0
    api_budget: ApiBudget | None = None
    baseline_placeholder_ratio: float | None = None
    reuse_warnings: list[Any] = field(default_factory=list)
    negative_pattern_warnings: list[Any] = field(default_factory=list)
    backoff_active: bool = False
    graphic_card_plans: list[dict[str, Any]] = field(default_factory=list)
    dry_run_proposed_changes: list[dict[str, Any]] = field(default_factory=list)


def diversity_enabled(visual_config: dict[str, Any] | None) -> bool:
    if not visual_config:
        return False
    diversity = visual_config.get("diversity") or {}
    return bool(diversity.get("enabled", False))


def rollout_mode(visual_config: dict[str, Any] | None) -> str:
    if not visual_config:
        return "report_only"
    diversity = visual_config.get("diversity") or {}
    return str(diversity.get("rollout_mode", "report_only"))


def prepare_visual_diversity(
    *,
    scene_doc: dict[str, Any],
    visual_config: dict[str, Any] | None,
    channel_id: str,
    job_id: str,
    repo_root: Path,
    job_metadata: dict[str, Any] | None = None,
    outputs_root: Path | None = None,
) -> VisualDiversityRun:
    """Plan scenes and prepare per-job state. Safe to call when disabled."""
    if not diversity_enabled(visual_config):
        return VisualDiversityRun(enabled=False, rollout_mode="report_only")

    visual_dna = load_visual_dna({"visuals": visual_config or {}}, channel_id, repo_root=repo_root)
    renderer_caps = detect_renderer_caps(visual_config)
    topic = resolve_video_topic(scene_doc, job_metadata)

    plans = plan_scenes(
        scene_doc.get("scenes", []),
        channel_id=channel_id,
        job_id=job_id,
        topic=topic,
        visual_dna=visual_dna,
        renderer_caps=renderer_caps,
        visual_config=visual_config,
    )
    card_plans = plan_report_only_graphic_cards(
        scene_doc.get("scenes", []),
        visual_dna,
        visual_config or {},
        renderer_caps,
    )
    # Attach plan to scenes so downstream selectors can read them via .get().
    plan_by_id = {p["scene_id"]: p for p in plans}
    current_rollout_mode = rollout_mode(visual_config)
    if current_rollout_mode != "report_only":
        for scene in scene_doc.get("scenes", []):
            plan = plan_by_id.get(scene.get("id"))
            if not plan:
                continue
            scene.setdefault("visual_bucket", plan["visual_bucket"])
            scene.setdefault("shot_type", plan["shot_type"])
            scene.setdefault("scene_role", plan["role"])

    dry_run_proposed_changes: list[dict[str, Any]] = []
    if current_rollout_mode == "report_only":
        for entry in card_plans:
            dry_run_proposed_changes.append({
                "scene_id": entry.get("scene_id"),
                "change_type": "graphic_card_plan",
                "suggested_card_type": entry.get("suggested_card_type"),
                "reason": f"{entry.get('role', 'scene')} role with graphic-card trigger",
            })

    api_budget = ApiBudget(
        max_per_video=int((visual_config or {}).get("max_api_requests_per_video", 80) or 80)
    )

    backoff_active = False
    backoff_path = (
        Path(repo_root) / "caches" / "pexels_api_backoff.json" if repo_root else None
    )
    if backoff_path is not None and is_backoff_active(backoff_path):
        backoff_active = True
        api_budget.record_429()

    baseline = None
    if outputs_root is not None:
        baseline = resolve_placeholder_baseline(channel_id, job_id, outputs_root)

    return VisualDiversityRun(
        enabled=True,
        rollout_mode=current_rollout_mode,
        plans=plans,
        visual_dna=visual_dna,
        visual_config=dict(visual_config or {}),
        renderer_caps=renderer_caps,
        api_budget=api_budget,
        baseline_placeholder_ratio=baseline,
        backoff_active=backoff_active,
        graphic_card_plans=card_plans,
        dry_run_proposed_changes=dry_run_proposed_changes,
    )


def record_scene_selection(
    run: VisualDiversityRun,
    *,
    scene: dict[str, Any],
    selected_asset: dict[str, Any] | None,
    is_placeholder: bool = False,
) -> None:
    if not run.enabled:
        return
    if is_placeholder or selected_asset is None:
        run.placeholder_count += 1
        return
    run.selections.append({
        "scene_id": scene.get("id"),
        "visual_bucket": scene.get("visual_bucket"),
        "shot_type": scene.get("shot_type"),
        "creator_key": selected_asset.get("creator_key"),
        "locale_feel": selected_asset.get("locale_feel"),
        "original_query": selected_asset.get("original_query"),
        "provider_tags_json": selected_asset.get("provider_tags_json"),
        "provider": selected_asset.get("provider"),
        "provider_asset_id": selected_asset.get("provider_asset_id"),
    })


def finalize_visual_diversity_report(
    run: VisualDiversityRun,
    *,
    job_id: str,
    channel_id: str,
    outputs_dir: Path,
    graphic_cards_planned: int = 0,
    graphic_cards_rendered: int = 0,
) -> Path | None:
    """Emit the JSON + Markdown report. Returns the JSON path."""
    if not run.enabled:
        return None

    api_stats = run.api_budget.stats(
        hourly_budget_enforced=False,
        hourly_budget_warning="No batch-level API budget manager is available.",
    ) if run.api_budget else {}

    dry_run_rel_path: str | None = None
    if run.rollout_mode == "report_only":
        dry_run_rel_path = f"outputs/{job_id}/visual-diversity-dry-run.json"

    report = build_report(
        job_id=job_id,
        channel_id=channel_id,
        rollout_mode=run.rollout_mode,
        plans=run.plans,
        selections=run.selections,
        visual_dna=run.visual_dna,
        api_budget_stats=api_stats,
        placeholder_count=run.placeholder_count,
        baseline_placeholder_ratio=run.baseline_placeholder_ratio,
        reuse_warnings=run.reuse_warnings,
        negative_pattern_warnings=run.negative_pattern_warnings,
        graphic_cards_supported=bool(run.renderer_caps.get("graphic_cards")),
        graphic_cards_rendered=graphic_cards_rendered,
        graphic_cards_planned=graphic_cards_planned or len(run.graphic_card_plans),
        graphic_cards_planned_scene_ids=[
            entry["scene_id"] for entry in run.graphic_card_plans if entry.get("scene_id")
        ],
        dry_run_selection_changes_count=len(run.dry_run_proposed_changes),
        dry_run_selection_changes_path=dry_run_rel_path,
    )
    json_path, _md_path = write_report(report, outputs_dir)
    if run.rollout_mode == "report_only":
        dry_run_payload = {
            "job_id": job_id,
            "channel_id": channel_id,
            "mode": "report_only",
            "production_assets_changed": False,
            "proposed_changes": run.dry_run_proposed_changes,
        }
        (Path(outputs_dir) / "visual-diversity-dry-run.json").write_text(
            json.dumps(dry_run_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return json_path


def video_length_profile(run: VisualDiversityRun) -> str:
    if not run.enabled:
        return "unknown"
    return classify_video_length(len(run.plans), run.visual_dna)
