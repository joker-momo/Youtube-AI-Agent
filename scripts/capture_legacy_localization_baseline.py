#!/usr/bin/env python3
"""Capture a read-only, deterministic baseline of one completed legacy job."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

TERMINAL_QUEUE_STATUSES = frozenset({"completed", "failed", "cancelled"})
COPIED_ARTIFACTS = (
    "job.json",
    "json/script.json",
    "json/scenes.json",
    "json/seo.json",
    "json/render_props.json",
    "json/review.json",
    "operator/gemini/script_qa.json",
    "operator/gemini/scenes_qa.json",
    "operator/gemini/seo_qa.json",
)
HASHED_PROMPTS = (
    "operator/chatgpt/script_prompt.md",
    "operator/chatgpt/scenes_prompt.md",
    "operator/chatgpt/seo_prompt.md",
)
PROTECTED_CONFIG_ROOT = "configs/vida-plena-45"


class BaselineCaptureError(RuntimeError):
    """Raised when the legacy baseline cannot be captured safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _queue_snapshot(queue_path: Path, job_id: str) -> dict[str, Any]:
    uri = f"{queue_path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT job_id, status, created_at, started_at, completed_at
                FROM job_queue
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise BaselineCaptureError(f"cannot read queue snapshot: {exc}") from exc
    if row is None:
        raise BaselineCaptureError(f"job {job_id!r} is absent from the queue")
    return dict(row)


def _assert_terminal(job: dict[str, Any], queue: dict[str, Any]) -> None:
    if queue["status"] not in TERMINAL_QUEUE_STATUSES:
        raise BaselineCaptureError(
            f"job {queue['job_id']!r} is not terminal: queue status={queue['status']!r}"
        )
    incomplete = [
        stage["name"]
        for stage in job.get("stages", [])
        if stage.get("status") != "completed"
    ]
    if queue["status"] == "completed" and incomplete:
        raise BaselineCaptureError(
            f"job {queue['job_id']!r} is not terminal: incomplete stages={incomplete}"
        )


def _tracked_files(repo_root: Path, baseline_ref: str) -> dict[str, str]:
    changed = _run_git(
        repo_root,
        "diff",
        "--name-only",
        "--diff-filter=MDRTUXB",
        baseline_ref,
        "--",
    ).splitlines()
    if changed:
        raise BaselineCaptureError(
            "pre-existing tracked files differ from baseline ref: " + ", ".join(changed)
        )

    paths = _run_git(repo_root, "ls-tree", "-r", "--name-only", baseline_ref).splitlines()
    return {path: _sha256(repo_root / path) for path in paths}


def _json_structure(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_structure(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        signatures = {_stable_json(_json_structure(item)) for item in value}
        return {
            "type": "array",
            "item_structures": [json.loads(item) for item in sorted(signatures)],
        }
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _probe_media(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration:"
                "stream=index,codec_type,codec_name,width,height,sample_rate,channels"
            ),
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def capture_baseline(
    *,
    repo_root: Path,
    job_dir: Path,
    queue_path: Path,
    output_dir: Path,
    baseline_ref: str,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    job_dir = job_dir.resolve()
    queue_path = queue_path.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise BaselineCaptureError(f"output directory already exists: {output_dir}")

    job_path = job_dir / "job.json"
    if not job_path.is_file():
        raise BaselineCaptureError(f"missing job snapshot: {job_path}")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    queue = _queue_snapshot(queue_path, str(job["job_id"]))
    _assert_terminal(job, queue)

    tracked_files = _tracked_files(repo_root, baseline_ref)
    protected_configs = {
        path: digest
        for path, digest in tracked_files.items()
        if path == PROTECTED_CONFIG_ROOT or path.startswith(f"{PROTECTED_CONFIG_ROOT}/")
    }
    prompt_hashes = {}
    for relative_path in HASHED_PROMPTS:
        source = job_dir / relative_path
        if source.is_file():
            prompt_hashes[relative_path] = _sha256(source)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=str(output_dir.parent))
    )
    try:
        artifact_manifest: dict[str, dict[str, Any]] = {}
        for relative_path in COPIED_ARTIFACTS:
            source = job_dir / relative_path
            if not source.is_file():
                raise BaselineCaptureError(f"missing required artifact: {source}")
            destination = staging / "artifacts" / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            artifact_manifest[relative_path] = {
                "sha256": _sha256(destination),
                "size_bytes": destination.stat().st_size,
            }

        render_props = json.loads(
            (staging / "artifacts" / "json" / "render_props.json").read_text(
                encoding="utf-8"
            )
        )
        video_path = job_dir / "outputs" / "video.mp4"
        if not video_path.is_file():
            raise BaselineCaptureError(f"missing completed video: {video_path}")
        media = _probe_media(video_path)
        manifest = {
            "schema_version": "localized-v2-legacy-baseline/v1",
            "baseline_ref": baseline_ref,
            "source_job_id": job["job_id"],
            "source_channel_id": job["channel_id"],
            "queue_snapshot": queue,
            "stage_sequence": [stage["name"] for stage in job["stages"]],
            "prompt_hashes": prompt_hashes,
            "artifacts": artifact_manifest,
            "render_props_structure": _json_structure(render_props),
            "final_video": {
                "sha256": _sha256(video_path),
                "size_bytes": video_path.stat().st_size,
                "probe": media,
            },
            "protected_config_hashes": protected_configs,
            "tracked_files": tracked_files,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--queue-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--baseline-ref", default="HEAD")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        manifest = capture_baseline(
            repo_root=args.repo_root,
            job_dir=args.job_dir,
            queue_path=args.queue_db,
            output_dir=args.output_dir,
            baseline_ref=args.baseline_ref,
        )
    except (BaselineCaptureError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"baseline capture failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"captured {manifest['source_job_id']} at {args.output_dir}",
        file=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
