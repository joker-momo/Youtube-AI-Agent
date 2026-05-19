from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from video_agent.batch import format_audit_markdown, build_audit_row, write_batch_audit
from video_agent.operator import (
    build_operator_next,
    build_operator_status,
    promote_operator_artifact,
    promote_operator_qa,
    write_operator_prompts,
    write_operator_review,
)
from video_agent.pipeline import OperatorRenderOptions, PipelineOptions, render_operator_job, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="video-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run the local MVP pipeline.")
    run_parser.add_argument("--channel", required=True, type=Path)
    run_parser.add_argument("--idea", required=True, type=Path)
    run_parser.add_argument("--jobs-dir", default=Path("jobs"), type=Path)
    run_parser.add_argument("--no-render", action="store_true", help="Generate artifacts but skip Remotion render.")
    _add_tts_override_args(run_parser)

    operator_render_parser = subparsers.add_parser(
        "operator-render",
        help="Render a job directory that already contains operator-approved script.json, scenes.json, and seo.json.",
    )
    operator_render_parser.add_argument("--channel", required=True, type=Path)
    operator_render_parser.add_argument("--job-dir", required=True, type=Path)
    operator_render_parser.add_argument("--no-render", action="store_true", help="Prepare assets but skip Remotion render.")
    operator_render_parser.add_argument(
        "--skip-operator-qa",
        action="store_true",
        help="Render without requiring promoted Gemini QA JSON files for script, scenes, and SEO.",
    )
    _add_tts_override_args(operator_render_parser)

    operator_prompts_parser = subparsers.add_parser(
        "operator-prompts",
        help="Write ChatGPT/Gemini prompt files for the semi-automated content workflow.",
    )
    operator_prompts_parser.add_argument("--channel", required=True, type=Path)
    operator_prompts_parser.add_argument("--idea", required=True, type=Path)
    operator_prompts_parser.add_argument("--job-dir", required=True, type=Path)
    operator_prompts_parser.add_argument("--stage", choices=["all", "script", "scenes", "seo"], default="all")

    operator_promote_parser = subparsers.add_parser(
        "operator-promote",
        help="Extract, validate, and promote a raw ChatGPT JSON response into a job artifact.",
    )
    operator_promote_parser.add_argument("--job-dir", required=True, type=Path)
    operator_promote_parser.add_argument("--artifact", choices=["script", "scenes", "seo"], required=True)
    operator_promote_parser.add_argument("--raw-file", required=True, type=Path)

    operator_promote_qa_parser = subparsers.add_parser(
        "operator-promote-qa",
        help="Extract, normalize, and promote a raw Gemini QA response for an operator artifact.",
    )
    operator_promote_qa_parser.add_argument("--job-dir", required=True, type=Path)
    operator_promote_qa_parser.add_argument("--artifact", choices=["script", "scenes", "seo"], required=True)
    operator_promote_qa_parser.add_argument("--raw-file", required=True, type=Path)

    operator_review_parser = subparsers.add_parser(
        "operator-review",
        help="Write a static HTML review page for an operator job.",
    )
    operator_review_parser.add_argument("--job-dir", required=True, type=Path)
    operator_review_parser.add_argument("--output", type=Path)

    operator_status_parser = subparsers.add_parser(
        "operator-status",
        help="Print the current artifact and QA state for an operator job.",
    )
    operator_status_parser.add_argument("--job-dir", required=True, type=Path)

    operator_next_parser = subparsers.add_parser(
        "operator-next",
        help="Create any needed prompt and print the next command for a single operator job.",
    )
    operator_next_parser.add_argument("--channel", required=True, type=Path)
    operator_next_parser.add_argument("--idea", required=True, type=Path)
    operator_next_parser.add_argument("--job-dir", required=True, type=Path)

    batch_parser = subparsers.add_parser("batch", help="Run multiple ideas and write a visual QA audit.")
    batch_parser.add_argument("--channel", required=True, type=Path)
    batch_parser.add_argument("--idea", action="append", required=True, type=Path)
    batch_parser.add_argument("--jobs-dir", default=Path("jobs"), type=Path)
    batch_parser.add_argument("--audit-path", type=Path)
    batch_parser.add_argument("--no-render", action="store_true", help="Generate artifacts but skip Remotion render.")
    _add_tts_override_args(batch_parser)

    audit_parser = subparsers.add_parser("audit", help="Write a visual QA audit for existing job directories.")
    audit_parser.add_argument("--job", action="append", required=True, type=Path)
    audit_parser.add_argument("--audit-path", type=Path)
    return parser


def _add_tts_override_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tts-provider", choices=["mock-local", "kokoro"])
    parser.add_argument("--tts-voice-id")
    parser.add_argument("--tts-lang-code")
    parser.add_argument("--tts-speed", type=float)


def _tts_override_from_args(args) -> dict | None:
    override = {}
    if args.tts_provider:
        override["provider"] = args.tts_provider
    if args.tts_voice_id:
        override["voice_id"] = args.tts_voice_id
    if args.tts_lang_code:
        override["lang_code"] = args.tts_lang_code
    if args.tts_speed is not None:
        override["speed"] = args.tts_speed
    return override or None


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
                tts_override=_tts_override_from_args(args),
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
                    tts_override=_tts_override_from_args(args),
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
    if args.command == "operator-render":
        result = render_operator_job(
            OperatorRenderOptions(
                channel_path=args.channel,
                job_dir=args.job_dir,
                render=not args.no_render,
                require_operator_qa=not args.skip_operator_qa,
                tts_override=_tts_override_from_args(args),
            )
        )
        _print_run_result(result)
        return 0
    if args.command == "operator-prompts":
        result = write_operator_prompts(
            channel_path=args.channel,
            idea_path=args.idea,
            job_dir=args.job_dir,
            stage=args.stage,
        )
        print(f"Operator prompts written: {len(result.paths)}")
        for path in result.paths:
            print(path)
        return 0
    if args.command == "operator-promote":
        result = promote_operator_artifact(args.job_dir, args.artifact, args.raw_file)
        print(f"Promoted {result.artifact}: {result.output_path}")
        return 0
    if args.command == "operator-promote-qa":
        result = promote_operator_qa(args.job_dir, args.artifact, args.raw_file)
        print(f"Promoted {result.artifact} QA: {result.output_path}")
        return 0
    if args.command == "operator-review":
        output_path = write_operator_review(args.job_dir, args.output)
        print(f"operator_review.html: {output_path}")
        return 0
    if args.command == "operator-status":
        status = build_operator_status(args.job_dir)
        print(f"Job: {status['job_dir']}")
        print(f"Overall: {status['overall']}")
        for artifact, artifact_status in status["artifacts"].items():
            print(f"{artifact}: artifact={artifact_status['artifact']} qa={artifact_status['qa']}")
        print(f"Next: {status['next_step']}")
        return 0
    if args.command == "operator-next":
        result = build_operator_next(args.channel, args.idea, args.job_dir)
        print(f"Step: {result.step}")
        print(result.message)
        for path in result.prompt_paths:
            print(f"Prompt: {path}")
        for command in result.commands:
            print(f"Command: {command}")
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
