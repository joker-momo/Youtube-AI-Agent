from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from video_agent.contracts import ARTIFACT_VISUAL_REVIEW
from video_agent.utils.json_io import read_json, write_json


@dataclass
class AuditRow:
    job_dir: Path
    topic: str
    video_status: str
    visual_status: str
    contact_sheet: str
    source_mix: str
    provider_mix: str
    issue_count: int


def build_audit_row(job_dir: Path) -> AuditRow:
    visual_review = read_json(job_dir / ARTIFACT_VISUAL_REVIEW)
    render_props = read_json(job_dir / "render_props.json")
    video_path = job_dir / "video.mp4"
    video_status = f"{video_path.stat().st_size / 1024 / 1024:.1f} MB" if video_path.exists() else "skipped"
    source_mix = ", ".join(f"{count} {source}" for source, count in visual_review["summary"]["by_source"].items())
    provider_mix = ", ".join(f"{count} {provider}" for provider, count in visual_review["summary"]["by_provider"].items())
    return AuditRow(
        job_dir=job_dir,
        topic=render_props.get("seo", {}).get("title") or render_props.get("channel", {}).get("name", "-"),
        video_status=video_status,
        visual_status=visual_review["qa"]["status"],
        contact_sheet=str(job_dir / visual_review.get("contact_sheet", "visual_contact_sheet.jpg")),
        source_mix=source_mix,
        provider_mix=provider_mix or "-",
        issue_count=visual_review["qa"]["issue_count"],
    )


def format_audit_markdown(rows: list[AuditRow]) -> str:
    lines = [
        "# Visual Batch Audit",
        "",
        "| Job | Topic | Video | Visual QA | Contact sheet | Source mix | Provider mix |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for row in rows:
        visual_status = row.visual_status
        if row.issue_count:
            visual_status = f"{visual_status} ({row.issue_count} issues)"
        lines.append(
            f"| `{row.job_dir.name}` | {row.topic} | {row.video_status} | {visual_status} | `{row.contact_sheet}` | {row.source_mix} | {row.provider_mix} |"
        )
    return "\n".join(lines) + "\n"


def write_batch_audit(job_dirs: list[Path], audit_path: Path) -> str:
    rows = [build_audit_row(job_dir) for job_dir in job_dirs]
    markdown = format_audit_markdown(rows)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(markdown, encoding="utf-8")
    write_json(audit_path.with_suffix(".json"), {"jobs": [row.__dict__ | {"job_dir": str(row.job_dir)} for row in rows]})
    return markdown
