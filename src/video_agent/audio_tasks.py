from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="video-agent-audio")
    subparsers = parser.add_subparsers(dest="command", required=True)

    whisper_parser = subparsers.add_parser(
        "whisper-timestamps",
        help="Run the heavy Whisper timestamp stage in an isolated process.",
    )
    whisper_parser.add_argument("--job-dir", required=True, type=Path)

    assets_parser = subparsers.add_parser(
        "prepare-assets",
        help="Run TTS-heavy asset preparation in an isolated process.",
    )
    assets_parser.add_argument("--job-dir", required=True, type=Path)
    assets_parser.add_argument("--channel-path", required=True, type=Path)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "whisper-timestamps":
        from video_agent.orchestrator.stages import _run_whisper_timestamps_stage_inline

        _run_whisper_timestamps_stage_inline(args.job_dir)
        return 0
    if args.command == "prepare-assets":
        from video_agent.pipeline import _load_style, _write_visual_review
        from video_agent.stages.assets import prepare_assets
        from video_agent.utils.json_io import read_json, read_yaml

        channel_config = read_yaml(args.channel_path)
        style = _load_style(channel_config)
        scene_doc = read_json(args.job_dir / "scenes.json")
        assets = prepare_assets(
            args.job_dir,
            style,
            scene_doc,
            visual_config=channel_config.get("visuals"),
            tts_config=(channel_config.get("tts") or {})
            | {"music": channel_config.get("music") or {}},
            channel_id=channel_config["channel"]["id"],
        )
        # Regenerate visual_review.json so the dashboard's Visual QA stays in sync.
        _write_visual_review(args.job_dir, args.job_dir.name, assets, scene_doc)
        return 0
    raise ValueError(f"Unknown audio command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
