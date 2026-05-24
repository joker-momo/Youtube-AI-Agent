from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable

MEDIA_SUFFIXES = {
    ".aac",
    ".flac",
    ".jpg",
    ".jpeg",
    ".m4a",
    ".mp3",
    ".mp4",
    ".ogg",
    ".png",
    ".wav",
    ".webp",
}


@dataclass(frozen=True)
class CleanupOptions:
    jobs_dir: Path
    apply: bool = False
    include_failed_media: bool = False
    include_success_media: bool = False
    include_orphan_media: bool = False
    include_shards: bool = False
    keep_successful: int = 1


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    size: int
    reason: str


@dataclass(frozen=True)
class CleanupResult:
    candidates: list[CleanupCandidate]
    bytes_reclaimable: int
    bytes_deleted: int


def _iter_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file():
            yield path


def _job_status(job_dir: Path) -> str:
    try:
        data = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    except Exception:
        return "unknown"
    stages = data.get("stages") or []
    if stages and all(str(s.get("status")) == "completed" for s in stages if isinstance(s, dict)):
        return "completed"
    if any(str(s.get("status")) == "failed" for s in stages if isinstance(s, dict)):
        return "failed"
    return str(data.get("status") or "unknown")


def _successful_jobs_to_keep(jobs_dir: Path, keep_count: int) -> set[Path]:
    if keep_count <= 0 or not jobs_dir.exists():
        return set()
    completed = [
        path for path in jobs_dir.iterdir()
        if path.is_dir() and _job_status(path) == "completed"
    ]
    completed.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return set(completed[:keep_count])


def _candidate(path: Path, reason: str) -> CleanupCandidate | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    return CleanupCandidate(path=path, size=size, reason=reason)


def collect_cleanup_candidates(options: CleanupOptions) -> list[CleanupCandidate]:
    jobs_dir = options.jobs_dir
    if not jobs_dir.exists():
        return []

    keep_successful = _successful_jobs_to_keep(jobs_dir, options.keep_successful)
    candidates: list[CleanupCandidate] = []

    for job_dir in sorted(path for path in jobs_dir.iterdir() if path.is_dir()):
        status = _job_status(job_dir)
        keep_job_media = job_dir in keep_successful

        for file_path in _iter_files(job_dir):
            rel_parts = file_path.relative_to(job_dir).parts
            suffix = file_path.suffix.lower()
            reason = None

            if file_path.name.endswith(".tmp") or suffix == ".tmp":
                reason = "temp_file"
            elif (
                options.include_shards
                and "scenes_batches" in rel_parts
                and (job_dir / "scenes.json").exists()
            ):
                reason = "stale_scene_shard"
            elif suffix in MEDIA_SUFFIXES:
                if status == "failed" and options.include_failed_media:
                    reason = "failed_job_media"
                elif status == "unknown" and options.include_orphan_media:
                    reason = "orphan_job_media"
                elif (
                    status == "completed"
                    and options.include_success_media
                    and not keep_job_media
                ):
                    reason = "old_successful_job_media"

            if reason:
                item = _candidate(file_path, reason)
                if item is not None:
                    candidates.append(item)

    return candidates


def cleanup_jobs(options: CleanupOptions) -> CleanupResult:
    candidates = collect_cleanup_candidates(options)
    reclaimable = sum(item.size for item in candidates)
    deleted = 0
    if options.apply:
        for item in candidates:
            try:
                item.path.unlink()
                deleted += item.size
            except FileNotFoundError:
                continue
    return CleanupResult(
        candidates=candidates,
        bytes_reclaimable=reclaimable,
        bytes_deleted=deleted,
    )


def _format_size(num_bytes: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely clean local job artifacts. Defaults to dry-run."
    )
    parser.add_argument("--jobs-dir", type=Path, default=Path("jobs"))
    parser.add_argument("--apply", action="store_true", help="Actually delete candidates.")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode.")
    parser.add_argument("--include-failed-media", action="store_true")
    parser.add_argument("--include-success-media", action="store_true")
    parser.add_argument("--include-orphan-media", action="store_true")
    parser.add_argument("--include-shards", action="store_true")
    parser.add_argument("--keep-successful", type=int, default=1)
    args = parser.parse_args()

    options = CleanupOptions(
        jobs_dir=args.jobs_dir,
        apply=bool(args.apply and not args.dry_run),
        include_failed_media=args.include_failed_media,
        include_success_media=args.include_success_media,
        include_orphan_media=args.include_orphan_media,
        include_shards=args.include_shards,
        keep_successful=args.keep_successful,
    )
    result = cleanup_jobs(options)
    mode = "APPLY" if options.apply else "DRY-RUN"
    print(f"Mode: {mode}")
    print(f"Jobs dir: {options.jobs_dir}")
    print(f"Candidates: {len(result.candidates)}")
    print(f"Reclaimable: {_format_size(result.bytes_reclaimable)}")
    print(f"Deleted: {_format_size(result.bytes_deleted)}")
    for item in result.candidates[:100]:
        rel = item.path
        try:
            rel = item.path.relative_to(options.jobs_dir)
        except ValueError:
            pass
        print(f"- {_format_size(item.size):>10} | {item.reason:<24} | {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
