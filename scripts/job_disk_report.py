from __future__ import annotations

import argparse
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


def _format_size(num_bytes: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file():
            yield path


def build_report(jobs_dir: Path, *, top: int = 20) -> str:
    if not jobs_dir.exists():
        return f"jobs dir not found: {jobs_dir}\n"

    job_rows: list[tuple[int, int, Path]] = []
    media_rows: list[tuple[int, Path]] = []
    total_bytes = 0
    media_bytes = 0

    for job_dir in sorted(p for p in jobs_dir.iterdir() if p.is_dir()):
        job_total = 0
        job_media = 0
        for file_path in _iter_files(job_dir):
            try:
                size = file_path.stat().st_size
            except OSError:
                continue
            job_total += size
            total_bytes += size
            if file_path.suffix.lower() in MEDIA_SUFFIXES:
                job_media += size
                media_bytes += size
                media_rows.append((size, file_path))
        job_rows.append((job_total, job_media, job_dir))

    lines = [
        f"Jobs dir: {jobs_dir}",
        f"Total job data: {_format_size(total_bytes)}",
        f"Total media artifacts: {_format_size(media_bytes)}",
        "",
        f"Top {top} jobs by size:",
    ]
    for total, media, job_dir in sorted(job_rows, reverse=True)[:top]:
        lines.append(
            f"- {_format_size(total):>10} total | {_format_size(media):>10} media | {job_dir.name}"
        )

    lines.extend(["", f"Top {top} media files:"])
    for size, file_path in sorted(media_rows, reverse=True)[:top]:
        rel = file_path.relative_to(jobs_dir)
        lines.append(f"- {_format_size(size):>10} | {rel}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report disk usage for jobs without deleting anything."
    )
    parser.add_argument("--jobs-dir", type=Path, default=Path("jobs"))
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()
    print(build_report(args.jobs_dir, top=args.top), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
