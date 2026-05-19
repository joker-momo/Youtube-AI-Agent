from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from video_agent.contracts import (
    ARTIFACT_REPORT,
    ARTIFACT_RENDER_PROPS,
    ARTIFACT_VISUAL_CONTACT_SHEET,
    ARTIFACT_VISUAL_REVIEW,
    ARTIFACT_VIDEO,
    EVENT_LOG,
    repo_root,
)
from video_agent.providers.mock import MockProvider
from video_agent.stages.assets import prepare_assets
from video_agent.stages.render import render_with_remotion
from video_agent.stages.scene import run_scene_stage
from video_agent.stages.script import run_script_stage
from video_agent.stages.thumbnail import create_thumbnail_and_seo
from video_agent.stages.visual_contact_sheet import create_visual_contact_sheet
from video_agent.utils.json_io import read_json, read_yaml, write_json
from video_agent.utils.logging import EventLogger
from video_agent.utils.paths import create_job_dir
from video_agent.utils.validation import validate_json


@dataclass
class PipelineOptions:
    channel_path: Path
    idea_path: Path
    jobs_dir: Path = Path("jobs")
    render: bool = True


@dataclass
class PipelineResult:
    job_id: str
    job_dir: Path
    video_path: Path | None
    thumbnail_path: Path
    seo_path: Path
    report_path: Path


def _load_style(channel_config: dict) -> dict:
    return read_json(repo_root() / channel_config["style_dna"]["path"])


def _scene_visual_issues(scene: dict) -> list[dict]:
    issues = []
    source = scene.get("source")
    provider = scene.get("provider")
    selection = scene.get("selection") or {}
    score = selection.get("score")
    asset_key = f"{provider}:{scene.get('provider_asset_id')}" if provider and scene.get("provider_asset_id") else None

    if source == "generated_placeholder":
        issues.append(
            {
                "type": "PLACEHOLDER_USED",
                "severity": "warning",
                "message": "Scene fell back to a generated placeholder image.",
            }
        )
    if source == "asset_library" and not provider:
        issues.append(
            {
                "type": "MISSING_PROVIDER",
                "severity": "warning",
                "message": "Asset library scene is missing provider metadata.",
            }
        )
    if score is not None and score < 40:
        issues.append(
            {
                "type": "LOW_SELECTION_SCORE",
                "severity": "warning",
                "message": f"Selected asset score is low: {score}.",
            }
        )
    if source == "asset_library" and not selection:
        issues.append(
            {
                "type": "MISSING_SELECTION_METADATA",
                "severity": "warning",
                "message": "Stock asset has no selection metadata.",
            }
        )
    if asset_key:
        scene["asset_key"] = asset_key
    return issues


def _add_visual_qa(review: dict) -> dict:
    seen_assets = {}
    issue_count = 0
    for scene in review["scenes"]:
        issues = _scene_visual_issues(scene)
        asset_key = scene.pop("asset_key", None)
        if asset_key:
            if asset_key in seen_assets:
                issues.append(
                    {
                        "type": "DUPLICATE_ASSET_IN_JOB",
                        "severity": "warning",
                        "message": f"Asset already used in {seen_assets[asset_key]}.",
                    }
                )
            else:
                seen_assets[asset_key] = scene["scene_id"]
        scene["qa"] = {"status": "WARN" if issues else "PASS", "issues": issues}
        issue_count += len(issues)
    review["qa"] = {"status": "WARN" if issue_count else "PASS", "issue_count": issue_count}
    return review


def _write_visual_review(job_dir: Path, job_id: str, assets: dict, scene_doc: dict) -> dict:
    scenes = []
    for scene_asset, scene in zip(assets["scenes"], scene_doc["scenes"]):
        scenes.append(
            {
                "scene_id": scene_asset["scene_id"],
                "on_screen_text": scene.get("on_screen_text"),
                "source": scene_asset.get("source"),
                "provider": scene_asset.get("provider"),
                "provider_asset_id": scene_asset.get("provider_asset_id"),
                "source_url": scene_asset.get("source_url"),
                "query": (scene_asset.get("asset_selection") or {}).get("query"),
                "selection": scene_asset.get("asset_selection"),
                "background": scene_asset.get("background"),
            }
        )
    summary = {
        "total_scenes": len(scenes),
        "by_source": {},
        "by_provider": {},
        "selection_scores": None,
        "searched_providers": {},
    }
    selection_scores = []
    for scene in scenes:
        summary["by_source"][scene["source"]] = summary["by_source"].get(scene["source"], 0) + 1
        if scene["provider"]:
            summary["by_provider"][scene["provider"]] = summary["by_provider"].get(scene["provider"], 0) + 1
        selection = scene.get("selection") or {}
        if isinstance(selection.get("score"), (int, float)):
            selection_scores.append(selection["score"])
        for provider in selection.get("searched_providers") or []:
            summary["searched_providers"][provider] = summary["searched_providers"].get(provider, 0) + 1
    if selection_scores:
        summary["selection_scores"] = {
            "min": min(selection_scores),
            "avg": round(sum(selection_scores) / len(selection_scores), 1),
            "max": max(selection_scores),
        }
    review = _add_visual_qa({"job_id": job_id, "summary": summary, "scenes": scenes})
    review["contact_sheet"] = ARTIFACT_VISUAL_CONTACT_SHEET
    write_json(job_dir / ARTIFACT_VISUAL_REVIEW, review)
    return review


def _write_report(
    job_dir: Path,
    job_id: str,
    channel_config: dict,
    idea: dict,
    render_enabled: bool,
    visual_review: dict,
) -> Path:
    visual_lines = [
        "",
        "## Visual Review",
        f"- Visual QA: {visual_review['qa']['status']}",
        f"- Visual contact sheet: {visual_review['contact_sheet']}",
    ]
    for scene in visual_review["scenes"]:
        provider = scene.get("provider") or "-"
        asset_id = scene.get("provider_asset_id") or "-"
        source = scene.get("source") or "-"
        issue_count = len(scene.get("qa", {}).get("issues", []))
        suffix = f" ({issue_count} issue)" if issue_count == 1 else f" ({issue_count} issues)" if issue_count else ""
        visual_lines.append(f"- {scene['scene_id']}: {source} {provider}/{asset_id}{suffix}")
    report_path = job_dir / ARTIFACT_REPORT
    report_path.write_text(
        "\n".join(
            [
                f"# Job Report: {job_id}",
                "",
                f"- Channel: {channel_config['channel']['name']}",
                f"- Topic: {idea['topic']}",
                f"- Render enabled: {render_enabled}",
                "- Outputs:",
                "  - script.json",
                "  - scenes.json",
                "  - assets_manifest.json",
                "  - render_props.json",
                "  - visual_review.json",
                "  - seo.json",
                "  - thumbnail.jpg",
                "  - video.mp4" if render_enabled else "  - video.mp4 skipped",
                *visual_lines,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return report_path


def run_pipeline(options: PipelineOptions) -> PipelineResult:
    root = repo_root()
    channel_config = read_yaml(options.channel_path)
    idea = read_json(options.idea_path)
    validate_json(channel_config, root / "schemas/channel-config.schema.json")
    validate_json(idea, root / "schemas/manual-idea.schema.json")

    job_dir = create_job_dir(options.jobs_dir, channel_config["channel"]["id"], idea["topic"])
    job_id = job_dir.name
    logger = EventLogger(job_dir / EVENT_LOG)
    provider = MockProvider()
    style = _load_style(channel_config)

    logger.log("JOB_STARTED", {"job_id": job_id, "channel_id": channel_config["channel"]["id"], "cost_usd": 0})
    script = run_script_stage(provider, channel_config, idea, job_id, job_dir)
    validate_json(script, root / "schemas/script.schema.json")
    logger.log("SCRIPTED", {"job_id": job_id, "cost_usd": 0})

    scene_doc = run_scene_stage(provider, channel_config, idea, script, job_id, job_dir)
    validate_json(scene_doc, root / "schemas/scenes.schema.json")
    logger.log("SCENED", {"job_id": job_id, "cost_usd": 0})

    assets = prepare_assets(
        job_dir,
        style,
        scene_doc,
        visual_config=channel_config.get("visuals"),
        channel_id=channel_config["channel"]["id"],
    )
    seo = create_thumbnail_and_seo(provider, channel_config, style, idea, job_dir)
    validate_json(seo, root / "schemas/seo.schema.json")
    logger.log("ASSETS_READY", {"job_id": job_id, "cost_usd": 0})

    render_props = {
        "channel": channel_config["channel"],
        "style": style,
        "render": channel_config["render"] | {"duration_sec": scene_doc["total_duration_sec"]},
        "scenes": scene_doc["scenes"],
        "audio": assets["audio"],
        "seo": seo,
    }
    write_json(job_dir / ARTIFACT_RENDER_PROPS, render_props)
    validate_json(render_props, root / "schemas/render-props.schema.json")
    visual_review = _write_visual_review(job_dir, job_id, assets, scene_doc)
    create_visual_contact_sheet(job_dir, visual_review)

    video_path = None
    if options.render:
        video_path = job_dir / ARTIFACT_VIDEO
        render_with_remotion(job_dir / ARTIFACT_RENDER_PROPS, video_path, job_dir / "thumbnail.jpg")
        logger.log("RENDERED", {"job_id": job_id, "video_path": str(video_path), "cost_usd": 0})

    report_path = _write_report(job_dir, job_id, channel_config, idea, options.render, visual_review)
    logger.log("JOB_COMPLETED", {"job_id": job_id, "cost_usd": 0})
    return PipelineResult(
        job_id=job_id,
        job_dir=job_dir,
        video_path=video_path if video_path and video_path.exists() else None,
        thumbnail_path=job_dir / "thumbnail.jpg",
        seo_path=job_dir / "seo.json",
        report_path=report_path,
    )
