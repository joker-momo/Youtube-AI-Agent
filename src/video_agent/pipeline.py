from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from video_agent.contracts import (
    ARTIFACT_REPORT,
    ARTIFACT_RENDER_PROPS,
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


def _write_report(job_dir: Path, job_id: str, channel_config: dict, idea: dict, render_enabled: bool) -> Path:
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
                "  - seo.json",
                "  - thumbnail.jpg",
                "  - video.mp4" if render_enabled else "  - video.mp4 skipped",
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

    assets = prepare_assets(job_dir, style, scene_doc)
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

    video_path = None
    if options.render:
        video_path = job_dir / ARTIFACT_VIDEO
        render_with_remotion(job_dir / ARTIFACT_RENDER_PROPS, video_path, job_dir / "thumbnail.jpg")
        logger.log("RENDERED", {"job_id": job_id, "video_path": str(video_path), "cost_usd": 0})

    report_path = _write_report(job_dir, job_id, channel_config, idea, options.render)
    logger.log("JOB_COMPLETED", {"job_id": job_id, "cost_usd": 0})
    return PipelineResult(
        job_id=job_id,
        job_dir=job_dir,
        video_path=video_path if video_path and video_path.exists() else None,
        thumbnail_path=job_dir / "thumbnail.jpg",
        seo_path=job_dir / "seo.json",
        report_path=report_path,
    )
