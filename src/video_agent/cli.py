from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from video_agent.batch import format_audit_markdown, build_audit_row, write_batch_audit
from video_agent.pipeline import PipelineOptions, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="video-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run the local MVP pipeline.")
    run_parser.add_argument("--channel", required=True, type=Path)
    run_parser.add_argument("--idea", required=True, type=Path)
    run_parser.add_argument("--jobs-dir", default=Path("jobs"), type=Path)
    run_parser.add_argument("--no-render", action="store_true", help="Generate artifacts but skip Remotion render.")

    batch_parser = subparsers.add_parser("batch", help="Run multiple ideas and write a visual QA audit.")
    batch_parser.add_argument("--channel", required=True, type=Path)
    batch_parser.add_argument("--idea", action="append", required=True, type=Path)
    batch_parser.add_argument("--jobs-dir", default=Path("jobs"), type=Path)
    batch_parser.add_argument("--audit-path", type=Path)
    batch_parser.add_argument("--no-render", action="store_true", help="Generate artifacts but skip Remotion render.")

    audit_parser = subparsers.add_parser("audit", help="Write a visual QA audit for existing job directories.")
    audit_parser.add_argument("--job", action="append", required=True, type=Path)
    audit_parser.add_argument("--audit-path", type=Path)
    return parser


def _print_run_result(result) -> None:
    print(f"Job completed: {result.job_dir}")
    print(f"thumbnail.jpg: {result.thumbnail_path}")
    print(f"seo.json: {result.seo_path}")
    print(f"report.md: {result.report_path}")
    print(f"video.mp4: {result.video_path if result.video_path else 'skipped'}")


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
        _print_run_result(result)
        return 0
    if args.command == "batch":
        results = []
        for idea_path in args.idea:
            result = run_pipeline(
                PipelineOptions(
                    channel_path=args.channel,
                    idea_path=idea_path,
                    jobs_dir=args.jobs_dir,
                    render=not args.no_render,
                )
            )
            results.append(result)
            _print_run_result(result)
        audit_path = args.audit_path or args.jobs_dir / "latest_batch_audit.md"
        markdown = write_batch_audit([result.job_dir for result in results], audit_path)
        print(f"Batch completed: {len(results)} jobs")
        print(f"audit.md: {audit_path}")
        print(markdown, end="")
        return 0
    if args.command == "audit":
        rows = [build_audit_row(job_dir) for job_dir in args.job]
        markdown = format_audit_markdown(rows)
        if args.audit_path:
            args.audit_path.parent.mkdir(parents=True, exist_ok=True)
            args.audit_path.write_text(markdown, encoding="utf-8")
            print(f"audit.md: {args.audit_path}")
        print(markdown, end="")
        return 0
    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
