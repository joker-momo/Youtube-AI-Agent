from __future__ import annotations

import json
from pathlib import Path

from video_agent.contracts import (
    ARTIFACT_PERSONA_EVAL,
    ARTIFACT_SCENES,
    ARTIFACT_SCRIPT,
    ARTIFACT_SEO,
    ARTIFACT_VIDEO,
    ARTIFACT_VISUAL_REVIEW,
)
from video_agent.orchestrator.job_state import load_job
from video_agent.orchestrator.stages._shared import (
    StageInputMissingError,
    _complete_stage,
    _resolve_artifact,
)
from video_agent.pipeline import OperatorRenderOptions
from video_agent.utils.json_io import read_json, read_yaml
from video_agent.utils.json_io import write_json as _write_json

__all__ = [
    "run_render_stage",
    "run_review_stage",
    "run_persona_eval_stage",
]


def _reset_render_progress(job_dir: Path) -> None:
    """Clear ``render_progress.json`` to a clean 'not started' state at render-stage
    start. Without this the dashboard reads the PREVIOUS run's terminal value (e.g.
    a leftover ``percent: 100``) during the long ``prepare_assets`` phase that runs
    BEFORE Remotion writes the first frame — so the progress bar would sit frozen at
    a stale 100% for ~20-30min. Reset → the bar shows 0% / 'Preparing' until real
    frame counts arrive."""
    clean = {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}
    for p in (job_dir / "json" / "render_progress.json", job_dir / "render_progress.json"):
        try:
            if p.parent.exists():
                _write_json(p, clean)
        except OSError:
            pass


def run_render_stage(job_dir: Path, channel_path: Path, *, notify_telegram: bool = True) -> Path:
    state = load_job(job_dir)
    if state.current_stage != "render":
        raise StageInputMissingError(
            f"Cannot run render stage from current_stage={state.current_stage!r}"
        )
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    _reset_render_progress(job_dir)
    try:
        # Late facade import: tests monkeypatch video_agent.orchestrator.stages.render_operator_job
        from video_agent.orchestrator import stages as stages_pkg
        stages_pkg.render_operator_job(
            OperatorRenderOptions(
                channel_path=channel_path,
                job_dir=job_dir,
                render=True,
                require_operator_qa=False,
                stop_request_path=job_dir / ".stop_requested",
                notify_telegram=notify_telegram,
            )
        )
    except (FileNotFoundError, ValueError) as exc:
        raise StageInputMissingError(str(exc)) from exc

    output_path = job_dir / "video.mp4"
    _complete_stage(job_dir, "render", output_path)
    return output_path


def run_review_stage(job_dir: Path) -> Path:
    state = load_job(job_dir)
    if state.current_stage != "review":
        raise StageInputMissingError(
            f"Cannot run review stage from current_stage={state.current_stage!r}"
        )

    try:
        # Late facade import: tests monkeypatch video_agent.orchestrator.stages.write_operator_review
        from video_agent.orchestrator import stages as stages_pkg
        output_path = stages_pkg.write_operator_review(job_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise StageInputMissingError(str(exc)) from exc

    _complete_stage(job_dir, "review", output_path)
    return output_path


def run_persona_eval_stage(job_dir: Path, channel_path: Path) -> Path:
    state = load_job(job_dir)
    if state.current_stage != "persona_eval":
        raise StageInputMissingError(
            f"Cannot run persona_eval stage from current_stage={state.current_stage!r}"
        )
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    script = read_json(_resolve_artifact(job_dir, ARTIFACT_SCRIPT))
    scenes = read_json(_resolve_artifact(job_dir, ARTIFACT_SCENES))
    seo = read_json(_resolve_artifact(job_dir, ARTIFACT_SEO))
    visual_review = read_json(_resolve_artifact(job_dir, ARTIFACT_VISUAL_REVIEW))
    whisper_ok = _resolve_artifact(job_dir, "json/whisper_timestamps.json").exists()
    video_ok = _resolve_artifact(job_dir, ARTIFACT_VIDEO).exists()

    cfg = read_yaml(channel_path)
    personas_cfg = cfg.get("personas") or []
    min_median = int((cfg.get("qa_rules") or {}).get("thresholds", {}).get("persona_min_median_score", 7))

    title = str(seo.get("title") or "")
    desc = str(seo.get("description") or "")
    tags = [str(t) for t in (seo.get("tags") or [])]
    scene_count = len(scenes.get("scenes") or [])
    visual_status = str((visual_review.get("qa") or {}).get("status") or "WARN").upper()
    script_text = json.dumps(script, ensure_ascii=False)
    bad_terms = ("cura", "milagro", "garantizado", "elimina", "secreto")
    bad_hits = sum(1 for t in bad_terms if t in script_text.lower() or t in desc.lower() or t in title.lower())

    def _clamp(v: float) -> int:
        return max(1, min(10, int(round(v))))

    def _metric_template() -> dict[str, int]:
        title_len = len(title)
        title_score = 8 if 42 <= title_len <= 88 else (6 if 28 <= title_len <= 100 else 4)
        hook_score = 8 if "?" in script_text[:450] else 6
        info_score = 8 if scene_count >= 35 and len(desc) >= 260 else (6 if scene_count >= 24 else 4)
        trust_score = _clamp(9 - bad_hits * 2)
        audio_score = 8 if whisper_ok else 5
        visual_score = 8 if visual_status == "PASS" else 6
        sub_score = 8 if ("suscr" in desc.lower() or "suscr" in script_text.lower()) else 6
        share_score = 8 if any("compart" in x.lower() for x in ([desc] + tags)) else 6
        if video_ok:
            visual_score = min(10, visual_score + 1)
        return {
            "thumbnail_click_intent": title_score,
            "hook_retention": hook_score,
            "informational_value": info_score,
            "trustworthiness": trust_score,
            "audio_quality": audio_score,
            "visual_clarity": visual_score,
            "subscribe_intent": sub_score,
            "share_intent": share_score,
        }

    persona_rows: list[dict] = []
    persona_totals: list[int] = []
    base = _metric_template()
    for p in personas_cfg:
        pid = str(p.get("id") or "persona")
        profile_rel = str(p.get("profile_path") or "")
        profile_txt = ""
        if profile_rel:
            try:
                # Late facade import: tests monkeypatch video_agent.orchestrator.stages.repo_root
                from video_agent.orchestrator import stages as stages_pkg
                profile_txt = (stages_pkg.repo_root() / profile_rel).read_text(encoding="utf-8")
            except OSError:
                profile_txt = ""
        metrics = dict(base)
        lc = profile_txt.lower()
        if "skeptic" in lc or "skeptical" in lc:
            metrics["trustworthiness"] = _clamp(metrics["trustworthiness"] - 1)
        if "audio-first" in lc:
            metrics["audio_quality"] = _clamp(metrics["audio_quality"] + 1)
            metrics["hook_retention"] = _clamp(metrics["hook_retention"] + 1)
        if "large readable text" in lc:
            metrics["visual_clarity"] = _clamp(metrics["visual_clarity"] - 1)
        overall = _clamp(sum(metrics.values()) / len(metrics))
        persona_rows.append(
            {
                "id": pid,
                "profile_path": profile_rel,
                "metrics": metrics,
                "overall_score": overall,
            }
        )
        persona_totals.append(overall)

    if persona_totals:
        sorted_scores = sorted(persona_totals)
        median_score = sorted_scores[len(sorted_scores) // 2]
    else:
        median_score = 0

    verdict = "PASS" if median_score >= min_median else "NEEDS_REWORK"
    payload = {
        "verdict": verdict,
        "median_score": median_score,
        "threshold": {"min_median_score": min_median},
        "personas": persona_rows,
        "signals": {
            "scene_count": scene_count,
            "visual_status": visual_status,
            "whisper_timestamps": whisper_ok,
            "video_exists": video_ok,
            "policy_risk_hits": bad_hits,
        },
    }
    output_path = job_dir / ARTIFACT_PERSONA_EVAL
    _write_json(output_path, payload)
    _complete_stage(job_dir, "persona_eval", output_path)
    return output_path
