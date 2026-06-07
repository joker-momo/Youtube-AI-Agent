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
    clean_text = " ".join(clean_text.split())
    
    words = clean_text.split()
    normalized_rc = "_".join(words)
    return f"{stage}:{scene_str}:{type_str}:{normalized_rc}"
