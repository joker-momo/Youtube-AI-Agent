from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from video_agent.contracts import repo_root
from video_agent.orchestrator import JobAlreadyExistsError, create_job
from video_agent.orchestrator.browser_client import BrowserClient, BrowserClientError
from video_agent.orchestrator.idea_expander import IdeaExpansionError, expand_title_to_idea
from video_agent.orchestrator.idea_generator import load_published_videos
from video_agent.orchestrator.queue import JobQueue
from video_agent.storage.atomic import atomic_write_json
from video_agent.utils.json_io import read_json, read_yaml
from video_agent.utils.paths import slugify
from video_agent.utils.validation import validate_json

IDEA_FILE = "idea.json"
_SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ALLOWED_TITLE_POLICIES = {"block", "rewrite_angle", "warn_only"}
_ALLOWED_IDEA_POLICIES = {"block", "warn_only"}
_BANNED_HEALTH_CLAIMS = ("cura", "curar", "garantiza", "elimina", "milagro")


@dataclass
class DuplicateVerdict:
    verdict: str
    closest_existing_title: str = ""
    overlap_reason: str = ""
    similarity: float = 0.0
    policy_action: str = "allowed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").lower())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = re.sub(r"[^a-z0-9\s]+", " ", ascii_value)
    return re.sub(r"\s+", " ", ascii_value).strip()


def _tokens(value: str) -> set[str]:
    stopwords = {
        "como", "para", "cuando", "donde", "sobre", "entre", "desde",
        "despues", "antes", "anos", "vida", "plena", "mejor", "rutina",
        "sencilla", "realista", "ayuda", "ayudan", "puede", "pueden",
    }
    return {t for t in _normalize_text(value).split() if len(t) >= 4 and t not in stopwords}


def _jaccard(left: str, right: str) -> float:
    a = _tokens(left)
    b = _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _idea_text(idea: dict) -> str:
    points = " ".join(str(p) for p in idea.get("key_points") or [])
    return " ".join(
        str(idea.get(k) or "")
        for k in ("title_seed", "topic", "angle", "target_keyword", "viewer_pain", "thumbnail_hook")
    ) + " " + points


def _same_keyword(idea: dict, existing: dict) -> bool:
    left = _normalize_text(idea.get("target_keyword") or idea.get("title_seed") or "")
    right = _normalize_text(existing.get("target_keyword") or existing.get("title") or "")
    return bool(left and right and (left == right or left in right or right in left))


def check_content_duplicate(*, idea: dict, existing_videos: list[dict]) -> DuplicateVerdict:
    title = str(idea.get("title_seed") or idea.get("topic") or "").strip()
    norm_title = _normalize_text(title)
    best = DuplicateVerdict(verdict="UNIQUE")
    for existing in existing_videos:
        existing_title = str(existing.get("title") or existing.get("topic") or "").strip()
        if not existing_title:
            continue
        if norm_title and norm_title == _normalize_text(existing_title):
            return DuplicateVerdict(
                verdict="DUPLICATE",
                closest_existing_title=existing_title,
                overlap_reason="Exact normalized title match.",
                similarity=1.0,
                policy_action="blocked",
            )
        similarity = max(
            _jaccard(title, existing_title),
            _jaccard(_idea_text(idea), " ".join(str(existing.get(k) or "") for k in ("title", "topic", "angle", "target_keyword"))),
        )
        same_kw = _same_keyword(idea, existing)
        if similarity > best.similarity:
            best = DuplicateVerdict(
                verdict="RISK" if same_kw and similarity >= 0.45 else "UNIQUE",
                closest_existing_title=existing_title,
                overlap_reason="Same or similar target keyword with high token overlap." if same_kw else "",
                similarity=round(similarity, 3),
                policy_action="blocked" if same_kw and similarity >= 0.72 else "allowed",
            )
        if same_kw and similarity >= 0.72:
            return DuplicateVerdict(
                verdict="DUPLICATE",
                closest_existing_title=existing_title,
                overlap_reason="Same target keyword and high token overlap.",
                similarity=round(similarity, 3),
                policy_action="blocked",
            )
    return best


def resolve_channel_config(channel_id: str) -> tuple[Path, dict]:
    path = repo_root() / "configs" / channel_id / "channel.yaml"
    if path.exists():
        return path, read_yaml(path)
    fallback = os.environ.get("CHANNEL_CONFIG")
    if fallback:
        fallback_path = Path(fallback)
        if fallback_path.exists():
            cfg = read_yaml(fallback_path)
            cfg_channel = (cfg.get("channel") or {}).get("id")
            if not cfg_channel or cfg_channel == channel_id:
                return fallback_path, cfg
    raise HTTPException(
        status_code=404,
        detail={"error": "channel_config_missing", "channel_id": channel_id},
    )


def _safe_job_id(job_id: str) -> str:
    if not job_id or not _SAFE_JOB_ID_RE.match(job_id):
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_job_id", "message": f"Invalid job_id: {job_id!r}"},
        )
    return job_id


def _base_job_id(title_seed: str, explicit: str | None) -> str:
    if explicit:
        return _safe_job_id(explicit)
    slug = slugify(title_seed)[:80].strip("-")
    return _safe_job_id(slug or "untitled")


def _unique_job_id(jobs_root: Path, title_seed: str, explicit: str | None) -> str:
    base = _base_job_id(title_seed, explicit)
    if explicit:
        return base
    candidate = base
    suffix = 2
    while (jobs_root / candidate).exists():
        tail = f"-{suffix}"
        candidate = f"{base[: 128 - len(tail)]}{tail}"
        suffix += 1
    return candidate


def collect_existing_video_ideas(
    *,
    channel_id: str,
    jobs_root: Path,
    limit: int = 100,
) -> list[dict]:
    existing: list[dict] = []
    try:
        _channel_path, channel_config = resolve_channel_config(channel_id)
        for video in load_published_videos(channel_config, repo_root() / "configs"):
            title = str(video.get("title") or "").strip()
            if title:
                existing.append({"title": title, "source": "published_videos.json"})
                if len(existing) >= limit:
                    return existing
    except Exception:
        pass
    try:
        for job_dir in sorted(jobs_root.iterdir(), reverse=True):
            if len(existing) >= limit:
                break
            job_file = job_dir / "job.json"
            if not job_file.exists():
                continue
            try:
                job = read_json(job_file)
            except Exception:
                continue
            if job.get("channel_id") != channel_id:
                continue
            idea: dict[str, Any] = {}
            try:
                if (job_dir / IDEA_FILE).exists():
                    idea = read_json(job_dir / IDEA_FILE)
            except Exception:
                idea = {}
            seo_title = ""
            try:
                if (job_dir / "seo.json").exists():
                    seo = read_json(job_dir / "seo.json")
                    seo_title = str(seo.get("title") or "").strip()
            except Exception:
                seo_title = ""
            title = seo_title or str(idea.get("title_seed") or idea.get("topic") or "").strip()
            if not title:
                continue
            existing.append(
                {
                    "job_id": job.get("job_id") or job_dir.name,
                    "title": title,
                    "topic": idea.get("topic", ""),
                    "angle": idea.get("angle", ""),
                    "target_keyword": idea.get("target_keyword", ""),
                    "source": "seo.json" if seo_title else "idea.json",
                }
            )
    except Exception:
        return existing[:limit]
    return existing[:limit]


def _validate_request_title(title_seed: str) -> None:
    if not isinstance(title_seed, str) or not 10 <= len(title_seed.strip()) <= 160:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_title_seed",
                "message": "title_seed must be 10-160 characters.",
            },
        )


def _validate_idea(idea: dict, *, require_title_seed: bool = True) -> dict:
    try:
        validate_json(idea, repo_root() / "schemas" / "manual-idea.schema.json")
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_idea", "message": str(exc)},
        ) from exc
    if require_title_seed and not str(idea.get("title_seed") or "").strip():
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_idea", "message": "title_seed is required."},
        )
    topic = _normalize_text(idea.get("topic", ""))
    angle = _normalize_text(idea.get("angle", ""))
    title = _normalize_text(idea.get("title_seed", ""))
    if title and (topic == title or angle == title):
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_idea", "message": "topic and angle must be more specific than title_seed."},
        )
    lowered = _normalize_text(_idea_text(idea))
    for claim in _BANNED_HEALTH_CLAIMS:
        if re.search(rf"\b{re.escape(claim)}\b", lowered):
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_idea", "message": f"Unsafe health claim: {claim}"},
            )
    return idea


def _validate_duration(
    idea: dict,
    *,
    duration_mode: str,
    target_duration_sec: int | None,
    min_duration_sec: int,
    max_duration_sec: int,
) -> None:
    if duration_mode not in {"auto", "fixed"}:
        raise HTTPException(status_code=400, detail={"error": "invalid_duration_mode"})
    if duration_mode == "fixed":
        if target_duration_sec is None or not 300 <= int(target_duration_sec) <= 1800:
            raise HTTPException(status_code=400, detail={"error": "invalid_target_duration_sec"})
        idea["target_duration_sec"] = int(target_duration_sec)
        idea["duration_mode"] = "fixed"
        return
    if min_duration_sec < 300 or max_duration_sec > 1800 or min_duration_sec > max_duration_sec:
        raise HTTPException(status_code=400, detail={"error": "invalid_duration_bounds"})
    duration = int(idea.get("target_duration_sec") or 0)
    if not min_duration_sec <= duration <= max_duration_sec:
        raise HTTPException(
            status_code=422,
            detail={"error": "idea_expansion_failed", "last_validation_error": "target_duration_sec outside auto bounds"},
        )
    if not str(idea.get("duration_reason") or "").strip():
        raise HTTPException(
            status_code=422,
            detail={"error": "idea_expansion_failed", "last_validation_error": "duration_reason is required for auto mode"},
        )
    idea["duration_mode"] = "auto"


def _apply_duplicate_policy(
    *,
    verdict: DuplicateVerdict,
    policy: str,
    allow_rewrite: bool,
) -> DuplicateVerdict:
    if verdict.verdict == "UNIQUE":
        verdict.policy_action = "allowed"
        return verdict
    if policy == "warn_only":
        verdict.policy_action = "warning"
        return verdict
    if policy == "rewrite_angle" and allow_rewrite:
        verdict.policy_action = "rewrite_requested"
        return verdict
    verdict.policy_action = "blocked"
    return verdict


def _chatgpt_duplicate_verdict(idea: dict) -> DuplicateVerdict | None:
    dup = idea.get("duplicate_check")
    if not isinstance(dup, dict):
        return None
    raw = str(dup.get("verdict") or "").upper()
    if raw != "TOO_SIMILAR":
        return None
    return DuplicateVerdict(
        verdict="DUPLICATE",
        closest_existing_title=str(dup.get("closest_existing_title") or ""),
        overlap_reason=str(dup.get("overlap_reason") or "ChatGPT marked the idea too similar."),
        similarity=1.0,
        policy_action="blocked",
    )


async def create_job_from_title_seed(
    *,
    channel_id: str,
    title_seed: str,
    job_id: str | None,
    duration_mode: str,
    target_duration_sec: int | None,
    min_duration_sec: int,
    max_duration_sec: int,
    run_now: bool,
    enforce_approvals: bool,
    duplicate_policy: str,
    check_duplicates: bool,
    max_existing_videos: int,
    notes: str | None,
    jobs_root: Path,
    browser_client: BrowserClient,
) -> dict:
    _validate_request_title(title_seed)
    if duplicate_policy not in _ALLOWED_TITLE_POLICIES:
        raise HTTPException(status_code=400, detail={"error": "invalid_duplicate_policy"})
    channel_path, channel_config = resolve_channel_config(channel_id)
    existing = collect_existing_video_ideas(
        channel_id=channel_id, jobs_root=jobs_root, limit=max_existing_videos
    ) if check_duplicates else []

    last_verdict = DuplicateVerdict(verdict="UNIQUE")
    max_attempts = 3
    validation_feedback = ""
    for attempt in range(max_attempts):
        retry_notes = notes or ""
        if validation_feedback:
            retry_notes = f"{retry_notes}\nPrevious attempt failed validation: {validation_feedback}".strip()
        if attempt > 0 and last_verdict.policy_action == "rewrite_requested":
            retry_notes = f"{retry_notes}\nRewrite to avoid duplicate: {last_verdict.overlap_reason}".strip()
        try:
            idea = await expand_title_to_idea(
                title_seed=title_seed,
                channel_config=channel_config,
                session_fn=lambda messages: browser_client.run_session("chatgpt", messages),
                duration_mode=duration_mode,
                target_duration_sec=target_duration_sec,
                min_duration_sec=min_duration_sec,
                max_duration_sec=max_duration_sec,
                existing_videos=existing,
                duplicate_policy=duplicate_policy,
                notes=retry_notes or None,
                max_attempts=1,
            )
        except BrowserClientError as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": "chatgpt_unavailable", "message": str(exc)},
            ) from exc
        except IdeaExpansionError as exc:
            validation_feedback = str(exc)
            if attempt < max_attempts - 1:
                continue
            raise HTTPException(
                status_code=422,
                detail={"error": "idea_expansion_failed", "last_validation_error": str(exc)},
            ) from exc
        try:
            _validate_duration(
                idea,
                duration_mode=duration_mode,
                target_duration_sec=target_duration_sec,
                min_duration_sec=min_duration_sec,
                max_duration_sec=max_duration_sec,
            )
            _validate_idea(idea)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            validation_feedback = str(
                detail.get("last_validation_error")
                or detail.get("message")
                or detail.get("error")
                or exc.detail
            )
            if attempt < max_attempts - 1:
                continue
            raise
        deterministic = check_content_duplicate(idea=idea, existing_videos=existing) if check_duplicates else DuplicateVerdict(verdict="UNIQUE")
        chatgpt_verdict = _chatgpt_duplicate_verdict(idea)
        verdict = chatgpt_verdict if chatgpt_verdict and chatgpt_verdict.verdict != "UNIQUE" else deterministic
        verdict = _apply_duplicate_policy(verdict=verdict, policy=duplicate_policy, allow_rewrite=True)
        last_verdict = verdict
        if verdict.policy_action == "rewrite_requested" and duplicate_policy == "rewrite_angle":
            continue
        if verdict.policy_action == "blocked":
            raise _duplicate_http(verdict)
        return _create_job_from_validated_idea(
            channel_id=channel_id,
            channel_path=channel_path,
            idea=idea,
            job_id=job_id,
            run_now=run_now,
            enforce_approvals=enforce_approvals,
            duplicate_verdict=verdict,
            jobs_root=jobs_root,
            idea_source="manual_title_expansion",
        )
    raise _duplicate_http(last_verdict)


async def create_job_from_full_idea(
    *,
    channel_id: str,
    idea: dict,
    job_id: str | None,
    run_now: bool,
    enforce_approvals: bool,
    duplicate_policy: str,
    check_duplicates: bool,
    max_existing_videos: int,
    jobs_root: Path,
) -> dict:
    if duplicate_policy not in _ALLOWED_IDEA_POLICIES:
        raise HTTPException(status_code=400, detail={"error": "invalid_duplicate_policy"})
    channel_path, _channel_config = resolve_channel_config(channel_id)
    idea = dict(idea)
    _validate_idea(idea)
    existing = collect_existing_video_ideas(
        channel_id=channel_id, jobs_root=jobs_root, limit=max_existing_videos
    ) if check_duplicates else []
    verdict = check_content_duplicate(idea=idea, existing_videos=existing) if check_duplicates else DuplicateVerdict(verdict="UNIQUE")
    verdict = _apply_duplicate_policy(verdict=verdict, policy=duplicate_policy, allow_rewrite=False)
    if verdict.policy_action == "blocked":
        raise _duplicate_http(verdict)
    return _create_job_from_validated_idea(
        channel_id=channel_id,
        channel_path=channel_path,
        idea=idea,
        job_id=job_id,
        run_now=run_now,
        enforce_approvals=enforce_approvals,
        duplicate_verdict=verdict,
        jobs_root=jobs_root,
        idea_source="provided_full_idea",
    )


def _duplicate_http(verdict: DuplicateVerdict) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error": "duplicate_idea",
            "message": "The idea is too similar to an existing video.",
            "closest_existing_title": verdict.closest_existing_title,
            "overlap_reason": verdict.overlap_reason,
            "similarity": verdict.similarity,
        },
    )


def _create_job_from_validated_idea(
    *,
    channel_id: str,
    channel_path: Path,
    idea: dict,
    job_id: str | None,
    run_now: bool,
    enforce_approvals: bool,
    duplicate_verdict: DuplicateVerdict,
    jobs_root: Path,
    idea_source: str,
) -> dict:
    title = str(idea.get("title_seed") or idea.get("topic") or "idea")
    final_job_id = _unique_job_id(jobs_root, title, job_id)
    job_dir = jobs_root / final_job_id
    created_dir = not job_dir.exists()
    if job_id and job_dir.exists():
        raise HTTPException(
            status_code=409,
            detail={"error": "job_exists", "message": "Job already exists. Choose another job_id."},
        )
    try:
        atomic_write_json(job_dir / IDEA_FILE, idea)
        state = create_job(
            job_dir,
            job_id=final_job_id,
            channel_id=channel_id,
            idea_path=IDEA_FILE,
        )
    except JobAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail={"error": "job_exists", "message": str(exc)}) from exc
    except Exception:
        if created_dir:
            shutil.rmtree(job_dir, ignore_errors=True)
        raise

    pipeline_status = "created_not_started"
    enqueue_error = ""
    if run_now:
        try:
            JobQueue(jobs_root / "queue.db").enqueue(final_job_id, enforce_approvals)
            pipeline_status = "queued"
        except Exception as exc:
            pipeline_status = "created_enqueue_failed"
            enqueue_error = str(exc)

    response = {
        "job_id": final_job_id,
        "channel_id": channel_id,
        "job_dir": str(job_dir),
        "idea_path": IDEA_FILE,
        "idea": idea,
        "idea_source": idea_source,
        "duplicate_verdict": duplicate_verdict.to_dict(),
        "run_now": run_now,
        "pipeline_status": pipeline_status,
        "state": state.to_dict(),
        "job_url": f"/jobs/{final_job_id}/timeline",
        "channel_path": str(channel_path),
    }
    if enqueue_error:
        response["error"] = "Could not enqueue run-all. Open the job and run manually."
        response["enqueue_error"] = enqueue_error
    return response
