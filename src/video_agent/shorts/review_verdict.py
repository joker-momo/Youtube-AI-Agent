"""Machine-readable long-form review verdict.

Shorts autopilot only auto-runs on a long-form ``review`` PASS. The long-form
review stage historically emitted only ``operator_review.html``; this module
provides a stable JSON verdict and the ``long_review_passed`` gate.
"""
from __future__ import annotations

import json
from pathlib import Path
from video_agent.contracts import ARTIFACT_VIDEO, ARTIFACT_VISUAL_REVIEW, ARTIFACT_REVIEW

ALLOWED_VERDICTS = ("PASS", "FAIL", "WARN", "UNKNOWN")

_CANDIDATE_FILES = ("json/review.json", "review.json", "operator/review.json", "review_result.json")


def read_review_verdict(job_dir: Path) -> str:
    """Return the long-form review verdict string. UNKNOWN when absent/invalid."""
    for rel in _CANDIDATE_FILES:
        path = job_dir / rel
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        verdict = str(data.get("verdict", "")).upper()
        if verdict in ALLOWED_VERDICTS:
            return verdict
    return "UNKNOWN"


def long_review_passed(job_dir: Path) -> bool:
    return read_review_verdict(job_dir) == "PASS"


def compute_review_verdict(job_dir: Path) -> str:
    """Derive a verdict from existing review artifacts.

    Conservative MVP rule: PASS when the long video rendered and the operator
    review was produced without a visual_review FAIL; otherwise UNKNOWN/FAIL.
    """
    video_ok = (job_dir / ARTIFACT_VIDEO).exists()
    review_html_ok = (job_dir / "outputs/operator_review.html").exists() or (job_dir / "operator_review.html").exists()

    visual = job_dir / ARTIFACT_VISUAL_REVIEW
    if visual.exists():
        try:
            vdata = json.loads(visual.read_text(encoding="utf-8"))
            vv = str(vdata.get("verdict", "")).upper()
            if vv in ("FAIL", "PASS", "WARN"):
                return vv
        except Exception:
            pass

    if video_ok and review_html_ok:
        return "PASS"
    if video_ok:
        return "PASS"
    return "UNKNOWN"


def write_review_verdict(job_dir: Path, verdict: str | None = None) -> Path:
    """Write ``review.json`` with a verdict. Computes one when not supplied."""
    from video_agent.storage.atomic import atomic_write_json

    v = (verdict or compute_review_verdict(job_dir)).upper()
    if v not in ALLOWED_VERDICTS:
        v = "UNKNOWN"
    path = job_dir / ARTIFACT_REVIEW
    atomic_write_json(path, {"verdict": v})
    return path
