from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.utils.json_io import read_json, write_json

SCHEMA_VERSION = "2026-05-json-shards-v1"


class ShardValidationError(ValueError):
    pass


def extract_json_envelope(raw_text: str) -> dict[str, Any]:
    from video_agent.operator import extract_json_objects

    candidates = extract_json_objects(raw_text)
    if not candidates:
        raise ShardValidationError("No JSON envelope found in model response.")
    return candidates[-1]


def validate_envelope(
    envelope: dict[str, Any],
    *,
    expected_artifact_type: str,
    expected_job_id: str,
    expected_channel_id: str,
) -> None:
    if not isinstance(envelope, dict):
        raise ShardValidationError("Envelope must be an object.")
    if envelope.get("artifact_type") != expected_artifact_type:
        raise ShardValidationError(
            f"artifact_type mismatch: expected {expected_artifact_type!r}, "
            f"got {envelope.get('artifact_type')!r}"
        )
    if envelope.get("schema_version") != SCHEMA_VERSION:
        raise ShardValidationError(
            f"schema_version must be {SCHEMA_VERSION!r}, got {envelope.get('schema_version')!r}"
        )
    if envelope.get("job_id") != expected_job_id:
        raise ShardValidationError(
            f"job_id mismatch: expected {expected_job_id!r}, got {envelope.get('job_id')!r}"
        )
    if envelope.get("channel_id") != expected_channel_id:
        raise ShardValidationError(
            f"channel_id mismatch: expected {expected_channel_id!r}, got {envelope.get('channel_id')!r}"
        )
    status = envelope.get("status")
    if status not in {"complete", "partial", "error"}:
        raise ShardValidationError(f"Invalid envelope status: {status!r}")
    if status == "error":
        raise ShardValidationError("Model returned error shard status.")
    if status == "partial":
        hint = envelope.get("next_batch_hint") or ""
        raise ShardValidationError(f"Model returned partial shard status: {hint}")
    if not isinstance(envelope.get("data"), dict):
        raise ShardValidationError("Envelope data must be an object.")
    if not isinstance(envelope.get("warnings"), list):
        raise ShardValidationError("Envelope warnings must be a list.")


def save_envelope(path: Path, envelope: dict[str, Any]) -> Path:
    write_json(path, envelope)
    return path


def load_envelope(path: Path) -> dict[str, Any]:
    data = read_json(path)
    if not isinstance(data, dict):
        raise ShardValidationError(f"Envelope file is not an object: {path}")
    return data


def _scene_num(scene_id: str) -> int:
    if not isinstance(scene_id, str) or not scene_id.startswith("scene-"):
        raise ShardValidationError(f"Invalid scene id: {scene_id!r}")
    try:
        return int(scene_id.split("-", 1)[1])
    except ValueError as exc:
        raise ShardValidationError(f"Invalid scene id: {scene_id!r}") from exc


def _scene_id(num: int) -> str:
    return f"scene-{num:02d}"


def validate_scenes_plan(plan_envelope: dict[str, Any]) -> None:
    validate_envelope(
        plan_envelope,
        expected_artifact_type="scenes_plan",
        expected_job_id=str(plan_envelope.get("job_id") or ""),
        expected_channel_id=str(plan_envelope.get("channel_id") or ""),
    )
    data = plan_envelope["data"]
    if not isinstance(data.get("target_scene_count"), int) or data["target_scene_count"] <= 0:
        raise ShardValidationError("scenes_plan data.target_scene_count must be a positive integer.")
    if not isinstance(data.get("batch_size"), int) or data["batch_size"] <= 0:
        raise ShardValidationError("scenes_plan data.batch_size must be a positive integer.")
    batches = data.get("batches")
    if not isinstance(batches, list) or not batches:
        raise ShardValidationError("scenes_plan data.batches must be a non-empty list.")
    for idx, batch in enumerate(batches, start=1):
        if not isinstance(batch, dict):
            raise ShardValidationError(f"Plan batch {idx} must be an object.")
        if batch.get("batch_index") != idx:
            raise ShardValidationError(f"Plan batch {idx} has wrong batch_index.")
        _scene_num(str(batch.get("scene_start") or ""))
        _scene_num(str(batch.get("scene_end") or ""))


_REQUIRED_SCENE_FIELDS = {
    "id",
    "duration_sec",
    "narration",
    "on_screen_text",
    "caption",
    "visual_prompt",
    "motion",
    "asset_refs",
}


def validate_scenes_batch(
    batch_envelope: dict[str, Any],
    *,
    expected_batch_index: int,
    expected_batch_total: int,
    scene_start: str,
    scene_end: str,
) -> None:
    validate_envelope(
        batch_envelope,
        expected_artifact_type="scenes_batch",
        expected_job_id=str(batch_envelope.get("job_id") or ""),
        expected_channel_id=str(batch_envelope.get("channel_id") or ""),
    )
    if batch_envelope.get("batch_index") != expected_batch_index:
        raise ShardValidationError(
            f"Expected batch_index {expected_batch_index}, got {batch_envelope.get('batch_index')!r}"
        )
    if batch_envelope.get("batch_total") != expected_batch_total:
        raise ShardValidationError(
            f"Expected batch_total {expected_batch_total}, got {batch_envelope.get('batch_total')!r}"
        )
    scenes = batch_envelope["data"].get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ShardValidationError("scenes_batch data.scenes must be a non-empty list.")

    start_num = _scene_num(scene_start)
    end_num = _scene_num(scene_end)
    expected_ids = [_scene_id(num) for num in range(start_num, end_num + 1)]
    actual_ids = [scene.get("id") if isinstance(scene, dict) else None for scene in scenes]
    if actual_ids != expected_ids:
        raise ShardValidationError(
            f"Scenes batch range mismatch: expected {expected_ids}, got {actual_ids}"
        )

    seen: set[str] = set()
    for scene in scenes:
        if not isinstance(scene, dict):
            raise ShardValidationError("Each scene must be an object.")
        missing = sorted(_REQUIRED_SCENE_FIELDS - set(scene))
        if missing:
            raise ShardValidationError(f"Scene {scene.get('id')}: missing fields {missing}.")
        if scene["id"] in seen:
            raise ShardValidationError(f"Duplicate scene id in batch: {scene['id']}")
        seen.add(scene["id"])
        if not isinstance(scene.get("duration_sec"), (int, float)):
            raise ShardValidationError(f"Scene {scene['id']}: duration_sec must be numeric.")
        if not str(scene.get("visual_prompt") or "").strip():
            raise ShardValidationError(f"Scene {scene['id']}: visual_prompt must be non-empty.")
        if not isinstance(scene.get("asset_refs"), dict):
            raise ShardValidationError(f"Scene {scene['id']}: asset_refs must be an object.")
        if "layout_payload" in scene and not isinstance(scene.get("layout_payload"), dict):
            raise ShardValidationError(f"Scene {scene['id']}: layout_payload must be an object.")


def merge_scene_batches(
    *,
    job_id: str,
    channel_id: str,
    batch_envelopes: list[dict[str, Any]],
) -> dict[str, Any]:
    if not batch_envelopes:
        raise ShardValidationError("No scene batches to merge.")
    ordered = sorted(batch_envelopes, key=lambda env: int(env.get("batch_index") or 0))
    scenes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for env in ordered:
        validate_envelope(
            env,
            expected_artifact_type="scenes_batch",
            expected_job_id=job_id,
            expected_channel_id=channel_id,
        )
        for scene in env["data"].get("scenes") or []:
            scene_id = scene.get("id")
            if scene_id in seen:
                raise ShardValidationError(f"Duplicate scene ID while merging: {scene_id}")
            seen.add(scene_id)
            scenes.append(scene)

    expected_ids = [_scene_id(idx) for idx in range(1, len(scenes) + 1)]
    actual_ids = [scene.get("id") for scene in scenes]
    if actual_ids != expected_ids:
        raise ShardValidationError(
            f"Merged scenes must be sequential from scene-01. Expected {expected_ids}, got {actual_ids}"
        )
    total_duration = sum(float(scene.get("duration_sec") or 0) for scene in scenes)
    if total_duration.is_integer():
        total_duration = int(total_duration)
    return {
        "channel_id": channel_id,
        "job_id": job_id,
        "scenes": scenes,
        "total_duration_sec": total_duration,
        "qa": {"verdict": "PENDING_CLAUDE_QA"},
    }


_RISK_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


def merge_scenes_qa_batches(
    *,
    job_id: str,
    channel_id: str,
    qa_batch_envelopes: list[dict[str, Any]],
) -> dict[str, Any]:
    if not qa_batch_envelopes:
        raise ShardValidationError("No scenes QA batches to merge.")

    verdict = "PASS"
    compliant = True
    risk_level = "none"
    violations: list[Any] = []
    issues: list[Any] = []
    required_changes: list[Any] = []
    scores: dict[str, Any] = {}
    batch_results: list[dict[str, Any]] = []

    for env in sorted(qa_batch_envelopes, key=lambda item: int(item.get("batch_index") or 0)):
        validate_envelope(
            env,
            expected_artifact_type="scenes_qa_batch",
            expected_job_id=job_id,
            expected_channel_id=channel_id,
        )
        data = env["data"]
        batch_verdict = str(data.get("verdict") or "").upper()
        if batch_verdict != "PASS":
            verdict = "NEEDS_REWORK"
        policy = data.get("youtube_policy") or {}
        if policy.get("compliant") is False:
            compliant = False
            verdict = "NEEDS_REWORK"
        batch_risk = str(policy.get("risk_level") or "none").lower()
        if _RISK_RANK.get(batch_risk, 0) > _RISK_RANK.get(risk_level, 0):
            risk_level = batch_risk
        violations.extend(policy.get("violations") or [])
        issues.extend(data.get("issues") or [])
        required_changes.extend(data.get("required_changes") or [])
        for key, value in (data.get("scores") or {}).items():
            if isinstance(value, (int, float)):
                scores[key] = value if key not in scores else min(scores[key], value)
        batch_results.append(
            {
                "batch_index": env.get("batch_index"),
                "verdict": batch_verdict,
                "issues": data.get("issues") or [],
                "required_changes": data.get("required_changes") or [],
            }
        )

    return {
        "artifact": "scenes",
        "verdict": verdict,
        "youtube_policy": {
            "compliant": compliant,
            "risk_level": risk_level,
            "violations": violations,
        },
        "scores": scores,
        "issues": issues,
        "required_changes": required_changes,
        "batch_results": batch_results,
    }
