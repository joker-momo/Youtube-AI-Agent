from __future__ import annotations

from pathlib import Path

from video_agent.contracts import ARTIFACT_SCRIPT
from video_agent.operator import (
    _chatgpt_script_prompt,
    promote_operator_artifact,
)
from video_agent.orchestrator.job_state import load_job
from video_agent.orchestrator.stages._shared import (
    SCRIPT_PROMPT_PATH,
    SCRIPT_RAW_PATH,
    StageInputMissingError,
    _complete_stage,
    _resolve_artifact,
    _resolve_idea_path,
    dag_mode,
)
from video_agent.storage.atomic import atomic_write_text
from video_agent.utils.json_io import read_json, read_yaml
from video_agent.utils.json_io import write_json as _write_json

__all__ = [
    "run_script_stage",
    "promote_script_stage",
    "auto_script_stage",
    "_enforce_script_length_qa",
]


def _narration_word_count(script_payload: dict) -> int:
    """Spoken-word count of a promoted script's narration (whitespace tokens)."""
    return len(str(script_payload.get("narration") or "").split())


def _enforce_script_length_qa(
    job_dir: Path,
    qa_output: Path,
    qa_payload: dict,
    *,
    channel_path: Path | None = None,
) -> None:
    """Deterministically enforce the configured MINIMUM narration length.

    Gemini's opinion on length is advisory and has drifted (bug-495 shipped a
    stale hardcoded 2900-4350 range that failed correctly-sized scripts). The
    floor is now enforced HERE from the same channel-config value the generator
    and the QA briefing derive (``duration_sec_min × pace_wpm``), so a script
    below the floor is ALWAYS reworked regardless of a lenient model — and a
    script that MEETS the floor is left alone, never rejected for being "too
    short" by a drifting judge. There is no upper bound: longer is fine.
    """
    import json

    script_path = _resolve_artifact(job_dir, ARTIFACT_SCRIPT)
    if not script_path.exists():
        return
    try:
        script_payload = json.loads(script_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    if channel_path is None:
        try:
            state = load_job(job_dir)
        except Exception:
            return
        from video_agent.orchestrator import stages as stages_pkg
        channel_config_path = stages_pkg.repo_root() / "configs" / state.channel_id / "channel.yaml"
    else:
        channel_config_path = channel_path
    channel_config = read_yaml(channel_config_path) if Path(channel_config_path).exists() else {}

    from video_agent.orchestrator.briefing import _script_length_floor
    floor = _script_length_floor(channel_config)
    word_floor = floor["script_word_floor"]
    words = _narration_word_count(script_payload)
    if words >= word_floor:
        return  # meets the configured minimum — length is satisfied deterministically

    issue = (
        f"Narration is {words} words, below the configured minimum of {word_floor} "
        f"words (~{floor['floor_min']}+ min at {floor['pace_wpm']} wpm). Expand with "
        "more concrete examples, steps or micro-stories; there is no upper bound."
    )
    required = (
        f"Rewrite narration to AT LEAST {word_floor} words (~{floor['floor_min']}+ min "
        f"at {floor['pace_wpm']} wpm). Do not pad with filler — add real value. No maximum."
    )

    updated = dict(qa_payload)
    updated["verdict"] = "NEEDS_REWORK"
    youtube_policy = dict(updated.get("youtube_policy") or {})
    youtube_policy.setdefault("compliant", True)
    youtube_policy.setdefault("risk_level", "none")
    youtube_policy.setdefault("violations", [])
    updated["youtube_policy"] = youtube_policy
    scores = dict(updated.get("scores") or {})
    try:
        current_depth = int(scores.get("depth") or scores.get("clarity") or 5)
    except (TypeError, ValueError):
        current_depth = 5
    scores["depth"] = min(current_depth, 3)
    updated["scores"] = scores
    issues = list(updated.get("issues") or [])
    if issue not in issues:
        issues.append(issue)
    updated["issues"] = issues
    changes = list(updated.get("required_changes") or [])
    if required not in changes:
        changes.append(required)
    updated["required_changes"] = changes
    _write_json(qa_output, updated)


def run_script_stage(job_dir: Path, channel_path: Path) -> Path:
    """Produce the ChatGPT script prompt for the job.

    Reads ``job_dir/idea.json`` and the channel YAML, renders the prompt
    via the existing v2 helper, writes ``operator/chatgpt/script_prompt.md``,
    marks the ``script`` stage completed, and emits a ``STAGE_COMPLETED``
    event so consumers of ``events.jsonl`` see the same shape as v2.
    """
    idea_path = _resolve_idea_path(job_dir)
    if not idea_path.exists():
        raise StageInputMissingError(f"Missing {idea_path}")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    state = load_job(job_dir)
    if not dag_mode() and state.current_stage != "script":
        raise StageInputMissingError(
            f"Cannot run script stage from current_stage={state.current_stage!r}"
        )

    idea = read_json(idea_path)
    channel_config = read_yaml(channel_path)
    prompt_text = _chatgpt_script_prompt(channel_config, idea)

    output_path = job_dir / SCRIPT_PROMPT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path, prompt_text, encoding="utf-8")

    _complete_stage(job_dir, "script", output_path)

    return output_path


def promote_script_stage(job_dir: Path, channel_path: Path, raw_response: str) -> Path:
    state = load_job(job_dir)
    if not dag_mode() and state.current_stage != "script_promote":
        raise StageInputMissingError(
            f"Cannot run script_promote stage from current_stage={state.current_stage!r}"
        )
    if not raw_response.strip():
        raise StageInputMissingError("Missing raw ChatGPT script response.")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    raw_path = job_dir / SCRIPT_RAW_PATH
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(raw_path, raw_response, encoding="utf-8")

    try:
        result = promote_operator_artifact(
            job_dir,
            "script",
            raw_path,
            channel_path=channel_path,
        )
    except ValueError as exc:
        raise StageInputMissingError(str(exc)) from exc

    _complete_stage(job_dir, "script_promote", result.output_path)
    return result.output_path


async def auto_script_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn,
) -> Path:
    from video_agent.orchestrator import stages as stages_pkg
    return await stages_pkg._auto_run_then_promote(
        job_dir=job_dir,
        channel_path=channel_path,
        prompt_path=job_dir / SCRIPT_PROMPT_PATH,
        runner=stages_pkg.run_script_stage,
        promoter=stages_pkg.promote_script_stage,
        session_fn=session_fn,
        run_stage_name="script",
        promote_stage_name="script_promote",
        briefing_stage_name="script",
    )
