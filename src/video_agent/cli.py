from __future__ import annotations

import argparse
import sys
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
from video_agent.storage.atomic import atomic_write_text
from video_agent.utils.incident import RunIncidentMonitor


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
    operator_promote_parser.add_argument("--channel", type=Path)

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

    auto_parser = subparsers.add_parser(
        "auto",
        help="End-to-end auto pipeline via the V3 FastAPI app (POST /run-all).",
    )
    auto_parser.add_argument("--idea", required=True, type=Path)
    auto_parser.add_argument(
        "--job-id",
        help="Job id to use. Defaults to auto-<unix-ts>.",
    )
    auto_parser.add_argument(
        "--channel-id",
        default="vida-plena-45",
        help="Channel id stored in job.json. Default vida-plena-45.",
    )
    auto_parser.add_argument(
        "--app-url",
        default="http://127.0.0.1:8000",
        help="V3 app base URL. Default http://127.0.0.1:8000.",
    )
    auto_parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="HTTP read timeout in seconds. Default 1800 (30 min).",
    )

    worker_parser = subparsers.add_parser(
        "worker",
        help="Run the background worker to poll and execute jobs from the queue.",
    )
    worker_parser.add_argument("--db-path", default=Path("jobs/queue.db"), type=Path)

    llm_history_parser = subparsers.add_parser(
        "shorts-llm-history",
        help="Export a Short's ChatGPT+Gemini prompt history (incl. fails) as Markdown.",
    )
    llm_history_parser.add_argument("--job-dir", required=True, type=Path, help="Long job dir under jobs/.")
    llm_history_parser.add_argument("--short-id", required=True, help="Short id (folder name under shorts/).")
    llm_history_parser.add_argument(
        "--output", type=Path,
        help="Write Markdown to this file. Default: print to stdout.",
    )
    llm_history_parser.add_argument(
        "--json", action="store_true",
        help="Emit raw JSON array instead of Markdown.",
    )

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


def _dispatch(args: argparse.Namespace) -> int:
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
        result = promote_operator_artifact(args.job_dir, args.artifact, args.raw_file, channel_path=args.channel)
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
            atomic_write_text(args.audit_path, markdown, encoding="utf-8")
            print(f"audit.md: {args.audit_path}")
        print(markdown, end="")
        return 0
    if args.command == "auto":
        return _run_auto(args)
    if args.command == "worker":
        from video_agent.orchestrator.worker import run_worker_loop
        run_worker_loop(args.db_path)
        return 0
    if args.command == "shorts-llm-history":
        import json as _json

        from video_agent.shorts import llm_history, paths

        hist_path = paths.short_json_dir(args.job_dir, args.short_id) / paths.SHORT_LLM_HISTORY_FILE
        history = llm_history.read_history(hist_path)
        if not history:
            print(f"No LLM history at {hist_path}", file=sys.stderr)
            return 1
        if args.json:
            out = _json.dumps(history, ensure_ascii=False, indent=2)
        else:
            out = llm_history.render_markdown(history, short_id=args.short_id)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(out, encoding="utf-8")
            print(f"Wrote {len(history)} calls -> {args.output}")
        else:
            print(out)
        return 0
    raise ValueError(f"Unknown command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    from video_agent.contracts import load_env
    load_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    jobs_root = getattr(args, "jobs_dir", Path("jobs"))
    monitor = RunIncidentMonitor(
        command=args.command,
        argv=list(argv) if argv is not None else list(sys.argv[1:]),
        jobs_root=Path(jobs_root),
    )
    monitor.start()
    try:
        code = _dispatch(args)
    except Exception as exc:
        incident_path = monitor.finish(ok=False, error=exc)
        print(f"[incident] Run failed. Incident log: {incident_path}")
        print(f"[incident] Heartbeat: {monitor.heartbeat_path}")
        raise
    monitor.finish(ok=(code == 0))
    return code


def _run_auto(args: argparse.Namespace) -> int:
    """Drive the V3 /run-all pipeline from the command line."""
    import json
    import time
    import urllib.request
    import urllib.error

    base = args.app_url.rstrip("/")
    idea_path = Path(args.idea)
    if not idea_path.exists():
        print(f"idea file not found: {idea_path}")
        return 2
    try:
        idea_payload = json.loads(idea_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"idea file is not valid JSON: {idea_path}: {exc}")
        return 2
    job_id = args.job_id or f"auto-{int(time.time())}"

    def _post(url: str, payload: dict | None, timeout: int) -> tuple[int, dict]:
        body = b""
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}")
            except Exception:
                detail = {"error": str(exc)}
            return exc.code, detail

    print(f"Job id: {job_id}")
    print(f"App: {base}")

    status, body = _post(
        f"{base}/jobs",
        {
            "job_id": job_id,
            "channel_id": args.channel_id,
            "idea_path": str(idea_path),
        },
        timeout=30,
    )
    if status != 201:
        print(f"Create job failed ({status}): {body}")
        return 1
    print("Job created.")

    status, body = _post(f"{base}/jobs/{job_id}/idea", idea_payload, timeout=30)
    if status != 201:
        print(f"Upload idea failed ({status}): {body}")
        return 1
    print("Idea uploaded. Starting /run-all (browser session may take minutes)...")

    start = time.time()
    status, body = _post(
        f"{base}/jobs/{job_id}/run-all?enforce_approvals=false",
        None,
        timeout=args.timeout,
    )
    elapsed = time.time() - start
    print(f"/run-all returned HTTP {status} in {elapsed:.1f}s")
    if status == 200:
        for entry in body.get("completed", []):
            print(f"  {entry['stage']}: {entry['output']}")
        print(f"Final current_stage: {body.get('state', {}).get('current_stage')}")
        return 0
    detail = body.get("detail", body)
    if isinstance(detail, dict):
        print(f"Stopped at: {detail.get('stopped_at')}")
        print(f"Error: {detail.get('error')}")
        for entry in detail.get("completed", []):
            print(f"  completed: {entry['stage']}: {entry['output']}")
    else:
        print(detail)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
