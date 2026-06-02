from __future__ import annotations

from pathlib import Path

# Per-stage input + output file plumbing for the dashboard. Each stage
# entry lists the artifact relative paths (input + output) the operator
# wants to see, so the UI can show "what went into" and "what came out
# of" each step without per-stage hardcoding in JS.
STAGE_ARTIFACTS = {
    "idea_research": {
        "input": ["idea.json"],
        "output": ["research.json"],
    },
    "script": {
        "input": ["idea.json"],
        "output": ["operator/chatgpt/script_prompt.md"],
    },
    "script_promote": {
        "input": ["operator/chatgpt/script.raw.txt"],
        "output": ["script.json"],
    },
    "script_qa": {
        "input": ["script.json"],
        "output": ["operator/gemini/script_qa.json"],
    },
    "scenes": {
        "input": ["script.json"],
        "output": ["operator/chatgpt/scenes_prompt.md"],
    },
    "scenes_promote": {
        "input": ["operator/chatgpt/scenes.raw.txt"],
        "output": ["scenes.json"],
    },
    "scenes_qa": {
        "input": ["scenes.json"],
        "output": ["operator/gemini/scenes_qa.json"],
    },
    "seo": {
        "input": ["scenes.json"],
        "output": ["operator/chatgpt/seo_prompt.md"],
    },
    "seo_promote": {
        "input": ["operator/chatgpt/seo.raw.txt"],
        "output": ["seo.json"],
    },
    "seo_qa": {
        "input": ["seo.json"],
        "output": ["operator/gemini/seo_qa.json"],
    },
    "thumbnail_image": {
        "input": ["seo.json"],
        "output": [
            "assets/thumbnail_bg.png",
            "seo.json",
            "thumbnail.jpg",
            "thumbnail_1.jpg",
            "thumbnail_2.jpg",
            "thumbnail_3.jpg",
        ],
    },
    "assets_chatgpt": {
        "input": ["scenes.json"],
        "output": ["scenes.json"],
    },
    "whisper_timestamps": {
        "input": ["assets/narration.wav"],
        "output": ["whisper_timestamps.json"],
    },
    "render": {
        "input": ["script.json", "scenes.json", "seo.json"],
        "output": [
            "render_props.json",
            "video.mp4",
            "thumbnail.jpg",
            "thumbnail_1.jpg",
            "thumbnail_2.jpg",
            "thumbnail_3.jpg",
            "visual_review.json",
            "report.md",
        ],
    },
    "review": {
        "input": ["video.mp4"],
        "output": ["operator_review.html"],
    },
    "persona_eval": {
        "input": ["video.mp4", "script.json", "scenes.json", "seo.json"],
        "output": ["persona_eval.json"],
    },
}

# Empirical seconds per stage when long-form 20-30 min config is in
# play. Used for an ETA when the stage hasn't run yet. Render scales
# with the target_duration_sec of the idea so we compute it from
# scenes.json or idea.json instead of a constant.
STAGE_ETA_SECONDS = {
    "idea_research": 60,
    "script": 60,
    "script_promote": 60,
    "script_qa": 90,
    "scenes": 90,
    "scenes_promote": 60,
    "scenes_qa": 120,
    "seo": 60,
    "seo_promote": 30,
    "seo_qa": 90,
    "thumbnail_image": 120,
    "assets_chatgpt": 420,
    "whisper_timestamps": 30,
    "render": 600,  # overridden when target_duration_sec known
    "review": 5,
    "persona_eval": 8,
}


def resolve_inside(job_dir: Path, rel: str) -> Path | None:
    """Return ``job_dir / rel`` if it stays inside ``job_dir``, else None.

    Defends the artifact endpoint against ``..`` path traversal.
    """
    try:
        candidate = (job_dir / rel).resolve()
        if candidate.is_relative_to(job_dir.resolve()):
            return candidate
    except Exception:
        pass
    return None


def isoformat_to_epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def stage_duration_seconds(stage: dict) -> float | None:
    start = isoformat_to_epoch(stage.get("started_at"))
    end = isoformat_to_epoch(stage.get("completed_at"))
    if start is None or end is None:
        return None
    return max(0.0, end - start)


def effective_stage_status(
    stage: dict,
    current_stage: str | None,
    *,
    stop_requested: bool = False,
    current_stage_active: bool = True,
) -> str:
    status = str(stage.get("status") or "pending")
    if stop_requested:
        return status
    if current_stage_active and status == "pending" and stage.get("name") == current_stage:
        return "in_progress"
    return status


def job_has_in_progress_stage(payload: dict) -> bool:
    for raw_stage in payload.get("stages", []):
        if str(raw_stage.get("status") or "pending") == "in_progress":
            return True
    return False
