from pathlib import Path
import re
from dataclasses import dataclass, field, asdict


@dataclass
class ScenePipelineState:
    current_scenes_version: int = 0
    latest_scene_validation_ok: bool = False
    latest_scene_validation_version: int | None = None
    latest_scene_qa_ok: bool = False
    latest_scene_qa_version: int | None = None
    latest_audio_tail_ok: bool = False
    latest_audio_tail_version: int | None = None
    latest_seo_ok: bool = False

@dataclass
class RetryIssue:
    id: str
    stage: str
    attempt: int
    scene_id: str | None
    type: str
    severity: str
    detail: str
    required_change: str
    status: str  # active | resolved | suppressed | stale
    first_seen_attempt: int
    last_seen_attempt: int
    repeat_count: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class RetryMemory:
    stage: str
    active_issues: dict[str, RetryIssue] = field(default_factory=dict)
    resolved_issues: dict[str, RetryIssue] = field(default_factory=dict)
    suppressed_issues: dict[str, RetryIssue] = field(default_factory=dict)
    do_not_regress: list[str] = field(default_factory=list)
    hard_invariants: list[str] = field(default_factory=list)

def make_stable_issue_id(stage: str, scene_id: str | None, issue_type: str, detail_or_change: str) -> str:
    scene_str = str(scene_id or "global").lower().strip()
    type_str = str(issue_type or "unknown").lower().strip()
    
    clean_text = detail_or_change.lower()
    clean_text = re.sub(r'\b\d+(?:\.\d+)?\b', 'n', clean_text)
    clean_text = re.sub(r'\bs\d+\b', 'scene_id', clean_text)
    clean_text = re.sub(r'[^\w\s]', '', clean_text)
    words = clean_text.split()
    normalized_rc = "_".join(words)
    return f"{stage}:{scene_str}:{type_str}:{normalized_rc}"

def add_or_update_issue(memory: RetryMemory, issue: RetryIssue) -> None:
    if issue.id in memory.active_issues:
        existing = memory.active_issues[issue.id]
        existing.last_seen_attempt = issue.attempt
        existing.repeat_count += 1
        existing.detail = issue.detail
        existing.required_change = issue.required_change
    else:
        memory.active_issues[issue.id] = issue

def make_do_not_regress_line(issue: RetryIssue) -> str:
    if issue.stage == "scene_validation" and issue.type == "duration":
        return f"- Keep {issue.scene_id or 'CTA'} duration within layout caps."
    return f"- Do not reintroduce: {issue.required_change or issue.detail}."

def resolve_issue_by_id(memory: RetryMemory, issue_id: str) -> None:
    if issue_id in memory.active_issues:
        issue = memory.active_issues[issue_id]
        issue.status = "resolved"
        memory.resolved_issues[issue_id] = issue
        del memory.active_issues[issue_id]
        memory.do_not_regress.append(make_do_not_regress_line(issue))

def suppress_issue_by_id(memory: RetryMemory, issue_id: str) -> None:
    if issue_id in memory.active_issues:
        issue = memory.active_issues[issue_id]
        issue.status = "suppressed"
        memory.suppressed_issues[issue_id] = issue
        del memory.active_issues[issue_id]

def generate_cumulative_feedback(memory: RetryMemory, attempt_number: int, candidate_summary: str = "") -> str:
    active_lines = []
    for idx, (issue_id, issue) in enumerate(memory.active_issues.items(), 1):
        stage_str = str(issue.stage).upper().replace("_", "-")
        type_str = str(issue.type).upper().replace("_", "-")
        desc = issue.required_change or issue.detail
        if issue.detail and issue.detail not in desc:
            desc = f"{desc} - {issue.detail}"
        active_lines.append(f"{idx}. [{stage_str}][{issue.scene_id or 'global'}][{type_str}] {desc}")
    
    active_issues_str = "\n".join(active_lines) if active_lines else "None. All previously identified issues are resolved/addressed."
    do_not_regress_str = "\n".join(memory.do_not_regress) if memory.do_not_regress else "None."
    hard_invariants_str = "\n".join(memory.hard_invariants) if memory.hard_invariants else "None."
    
    suppressed_lines = [
        f"- {issue.required_change or issue.detail}" 
        for issue in memory.suppressed_issues.values()
    ]
    suppressed_str = "\n".join(suppressed_lines) if suppressed_lines else "None."
    
    return f"""RETRY FEEDBACK — CUMULATIVE

This is retry attempt {attempt_number}.
You must satisfy ALL active requirements below.
Do not only fix the newest issue.
Do not reintroduce resolved issues.

ACTIVE ISSUES TO FIX NOW:
{active_issues_str}

DO NOT REGRESS:
{do_not_regress_str}

HARD INVARIANTS:
{hard_invariants_str}

SUPPRESSED / STALE ISSUES:
{suppressed_str}

LATEST CANDIDATE SUMMARY:
{candidate_summary or "None."}

OUTPUT REQUIREMENTS:
- Return a full corrected JSON object.
- Do not return partial patches.
- Do not remove source-supported idea items.
- Do not change the approved script meaning."""

def log_final_gate_status(state: ScenePipelineState, allowed: bool, reason: str = "") -> None:
    import json
    log_doc = {
        "final_gate": {
            "current_scenes_version": state.current_scenes_version,
            "scene_validation_ok": state.latest_scene_validation_ok,
            "scene_validation_version": state.latest_scene_validation_version,
            "scene_qa_ok": state.latest_scene_qa_ok,
            "scene_qa_version": state.latest_scene_qa_version,
            "allowed_to_continue": allowed,
        }
    }
    if not allowed:
        log_doc["final_gate"]["reason"] = reason
    print(f"FINAL GATE STATUS: {json.dumps(log_doc)}")

def assert_latest_scenes_ready(state: ScenePipelineState) -> None:
    try:
        if not state.latest_scene_validation_ok:
            raise RuntimeError("Cannot proceed: latest scenes have not passed deterministic scene_validation.")

        if state.latest_scene_validation_version != state.current_scenes_version:
            raise RuntimeError("Cannot proceed: scene_validation result is stale.")

        if not state.latest_scene_qa_ok:
            raise RuntimeError("Cannot proceed: latest scenes have not passed Gemini scene QA.")

        if state.latest_scene_qa_version != state.current_scenes_version:
            raise RuntimeError("Cannot proceed: scene QA result is stale.")
            
        log_final_gate_status(state, allowed=True)
    except Exception as exc:
        log_final_gate_status(state, allowed=False, reason=str(exc))
        raise

def save_retry_memory(memory: RetryMemory, filepath: Path) -> None:
    from video_agent.storage.atomic import atomic_write_json
    doc = {
        "stage": memory.stage,
        "active_issues": {k: v.to_dict() for k, v in memory.active_issues.items()},
        "resolved_issues": {k: v.to_dict() for k, v in memory.resolved_issues.items()},
        "suppressed_issues": {k: v.to_dict() for k, v in memory.suppressed_issues.items()},
        "do_not_regress": memory.do_not_regress,
        "hard_invariants": memory.hard_invariants,
    }
    atomic_write_json(filepath, doc)

def load_retry_memory(filepath: Path) -> RetryMemory | None:
    import json
    if not filepath.exists():
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        memory = RetryMemory(
            stage=data["stage"],
            do_not_regress=data.get("do_not_regress") or [],
            hard_invariants=data.get("hard_invariants") or [],
        )
        for field_name in ("active_issues", "resolved_issues", "suppressed_issues"):
            tgt = getattr(memory, field_name)
            for k, v in (data.get(field_name) or {}).items():
                tgt[k] = RetryIssue(**v)
        return memory
    except Exception:
        return None




