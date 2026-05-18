from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from video_agent.pipeline import PipelineOptions, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="video-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run the local MVP pipeline.")
    run_parser.add_argument("--channel", required=True, type=Path)
    run_parser.add_argument("--idea", required=True, type=Path)
    run_parser.add_argument("--jobs-dir", default=Path("jobs"), type=Path)
    run_parser.add_argument("--no-render", action="store_true", help="Generate artifacts but skip Remotion render.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        result = run_pipeline(
            PipelineOptions(
                channel_path=args.channel,
                idea_path=args.idea,
                jobs_dir=args.jobs_dir,
                render=not args.no_render,
            )
        )
        print(f"Job completed: {result.job_dir}")
        print(f"thumbnail.jpg: {result.thumbnail_path}")
        print(f"seo.json: {result.seo_path}")
        print(f"report.md: {result.report_path}")
        print(f"video.mp4: {result.video_path if result.video_path else 'skipped'}")
        return 0
    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
