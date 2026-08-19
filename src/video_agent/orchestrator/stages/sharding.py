from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from video_agent.contracts import ARTIFACT_SCENES, ARTIFACT_SCRIPT, EVENT_LOG
from video_agent.operator import (
    _chatgpt_scenes_batch_prompt,
    _chatgpt_scenes_plan_prompt,
    _gemini_scenes_qa_batch_prompt,
)
from video_agent.operator_json import is_json_object_complete
from video_agent.operator_shards import (
    ShardValidationError,
    extract_json_envelope,
    merge_scene_batches,
    merge_scenes_qa_batches,
    save_envelope,
    validate_envelope,
    validate_scenes_batch,
    validate_scenes_plan,
)
from video_agent.orchestrator.job_state import load_job
from video_agent.orchestrator.stages._shared import (
    SCENES_BATCHES_DIR,
    SCENES_PLAN_PATH,
    SCENES_PROMPT_PATH,
    SCENES_QA_BATCHES_DIR,
    StageInputMissingError,
    _complete_stage,
    _resolve_artifact,
    _start_stage,
    dag_mode,
)
from video_agent.storage.atomic import atomic_write_text
from video_agent.utils.json_io import read_json, read_yaml
from video_agent.utils.json_io import write_json as _write_json
from video_agent.utils.logging import EventLogger

SessionFn = Callable[[Sequence[str]], Awaitable[str]]

# Total attempts (1 initial + retries) to obtain a scenes batch that passes
# validate_scenes_batch before the stage fails.
_BATCH_VALIDATE_ATTEMPTS = 3
_MAX_GENERATION_BATCH_SIZE = 4


def _batch_contract_header(
    *, batch_index: int, batch_total: int, scene_start: str, scene_end: str
) -> str:
    """Anti-drift preamble for every batch prompt in the persistent tab.

    After many batch turns in one conversation ChatGPT sometimes re-answers a
    previous batch (bug-438: returned batch 12 when asked for 13). Pinning the
    current batch contract at the top of every message keeps the model anchored.
    """
    return (
        f"# LOTE ACTUAL: {batch_index} de {batch_total}\n"
        f"Este mensaje corresponde EXCLUSIVAMENTE al lote batch_index={batch_index} "
        f"({scene_start}..{scene_end}).\n"
        "Los lotes anteriores de esta conversación ya están guardados: NO los "
        "repitas ni mezcles sus escenas.\n"
        f'Tu respuesta DEBE llevar "batch_index": {batch_index} y cubrir '
        f"exactamente {scene_start}..{scene_end}.\n\n"
    )


def _limit_scenes_plan_batch_size(
    plan_envelope: dict, *, max_batch_size: int = _MAX_GENERATION_BATCH_SIZE
) -> dict:
    """Split oversized cached/model-planned ranges without dropping scenes.

    Browser UI responses for six or more fully described scenes can exceed the
    stable response window.  Balanced subranges keep every original purpose and
    script-section constraint while avoiding a tiny final shard.
    """
    limited = json.loads(json.dumps(plan_envelope))
    data = limited.get("data") or {}
    batches = data.get("batches") or []
    split_batches: list[dict] = []

    for batch in batches:
        start = int(str(batch.get("scene_start") or "scene-0").rsplit("-", 1)[-1])
        end = int(str(batch.get("scene_end") or "scene-0").rsplit("-", 1)[-1])
        count = end - start + 1
        part_count = max(1, (count + max_batch_size - 1) // max_batch_size)
        base_size, extra = divmod(count, part_count)
        cursor = start
        for part_index in range(part_count):
            part_size = base_size + (1 if part_index < extra else 0)
            part_end = cursor + part_size - 1
            split_batch = dict(batch)
            split_batch["scene_start"] = f"scene-{cursor:02d}"
            split_batch["scene_end"] = f"scene-{part_end:02d}"
            split_batches.append(split_batch)
            cursor = part_end + 1

    for batch_index, batch in enumerate(split_batches, start=1):
        batch["batch_index"] = batch_index
    data["batch_size"] = max_batch_size
    data["batches"] = split_batches
    limited["data"] = data
    return limited


# Gemini QA slices the merged scene list into batches of this size. Shared by
# the authoritative scenes_qa stage and the generation-time prewarm so their
# batch files line up 1:1.
_QA_BATCH_SIZE = 8

# Scene fields Gemini QA actually judges. The retention layout planner mutates
# layout/layout_payload/planner_warnings after merge, so freshness hashes must
# only cover the LLM-authored content fields.
_SCENE_CONTENT_FIELDS = (
    "id", "narration", "caption", "on_screen_text", "visual_prompt", "duration_sec",
)


def _scenes_content_hash(scenes: Sequence[dict]) -> str:
    projection = [
        {field: scene.get(field) for field in _SCENE_CONTENT_FIELDS}
        for scene in scenes
        if isinstance(scene, dict)
    ]
    payload = json.dumps(projection, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _qa_scenes_batch_envelope(
    *,
    job_dir: Path,
    job_id: str,
    channel_id: str,
    channel_config: dict,
    session_fn: SessionFn,
    batch_scenes: list[dict],
    batch_index: int,
    batch_total: int,
    scenes_logger: EventLogger,
    step_prefix: str = "batch",
) -> dict:
    """Run Gemini QA for one scene slice and persist the envelope.

    The saved envelope carries ``source_content_hash`` so a later pass can
    tell whether the verdict still matches the scenes on disk (mtime alone
    cannot: prewarm files are written before scenes.json is merged)."""
    scenes_logger.log(
        "SCENES_QA_PROGRESS",
        {
            "job_id": job_id,
            "step": f"{step_prefix}_started",
            "batch_index": batch_index,
            "batches_total": batch_total,
        },
    )
    batch_doc = {
        "channel_id": channel_id,
        "job_id": job_id,
        "batch_index": batch_index,
        "batch_total": batch_total,
        "scenes": batch_scenes,
    }
    prompt = _gemini_scenes_qa_batch_prompt(
        channel_config,
        batch_doc,
        batch_index,
        batch_total,
    )
    envelope = await _request_shard_envelope(
        session_fn=session_fn,
        prompt=prompt,
        expected_artifact_type="scenes_qa_batch",
        expected_job_id=job_id,
        expected_channel_id=channel_id,
    )
    envelope["source_content_hash"] = _scenes_content_hash(batch_scenes)
    batch_path = job_dir / SCENES_QA_BATCHES_DIR / f"scenes_qa_batch_{batch_index:02d}.json"
    save_envelope(batch_path, envelope)
    scenes_logger.log(
        "SCENES_QA_PROGRESS",
        {
            "job_id": job_id,
            "step": f"{step_prefix}_saved",
            "batch_index": batch_index,
            "batches_total": batch_total,
        },
    )
    return envelope


async def _prewarm_scenes_qa(
    *,
    job_dir: Path,
    job_id: str,
    channel_id: str,
    channel_config: dict,
    session_fn: SessionFn,
    queue: asyncio.Queue[list[dict] | None],
    expected_qa_total: int,
    scenes_logger: EventLogger,
) -> None:
    """Best-effort overlap: QA completed scene slices on the Gemini session
    while ChatGPT is still generating later batches.

    Strictly additive — any failure is logged and the coroutine stops; the
    authoritative scenes_qa stage re-QAs whatever is missing or whose
    ``source_content_hash`` no longer matches (e.g. after a merge repair)."""
    buffered: list[dict] = []
    done = 0
    try:
        while True:
            item = await queue.get()
            finished = item is None
            if not finished:
                buffered.extend(item)
            while True:
                start = done * _QA_BATCH_SIZE
                chunk = buffered[start:start + _QA_BATCH_SIZE]
                if not chunk:
                    break
                if len(chunk) < _QA_BATCH_SIZE and not finished:
                    break  # partial slice: wait for more scenes
                done += 1
                await _qa_scenes_batch_envelope(
                    job_dir=job_dir,
                    job_id=job_id,
                    channel_id=channel_id,
                    channel_config=channel_config,
                    session_fn=session_fn,
                    batch_scenes=chunk,
                    batch_index=done,
                    batch_total=expected_qa_total,
                    scenes_logger=scenes_logger,
                    step_prefix="prewarm",
                )
            if finished:
                return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        scenes_logger.log(
            "SCENES_QA_PROGRESS",
            {
                "job_id": job_id,
                "step": "prewarm_failed",
                "batch_index": done + 1,
                "reason": str(exc)[:500],
            },
        )

__all__ = [
    "_request_shard_envelope",
    "_scene_id_to_batch_index",
    "_scene_ids_from_validation_error",
    "_scene_batch_repair_prompt",
    "_merge_scene_batches_with_repair",
    "auto_scenes_stage_sharded",
    "auto_scenes_qa_stage_sharded",
]


async def _request_shard_envelope(
    *,
    session_fn: SessionFn,
    prompt: str,
    expected_artifact_type: str,
    expected_job_id: str,
    expected_channel_id: str,
    max_attempts: int = 4,
) -> dict:
    """Send ``prompt`` and parse the model's JSON envelope, retrying with
    progressively stricter reminders if the envelope is missing or
    invalid. ChatGPT occasionally drops the envelope fields and just
    returns the inner ``data`` object — escalate the message on each
    retry so the model fixes the shape.
    """
    last_error: Exception | None = None
    last_preview = ""
    current_prompt = prompt
    raw_response = ""
    append_continuation = False
    for _attempt in range(max_attempts):
        chunk = await session_fn([current_prompt])
        if not isinstance(chunk, str) or not chunk.strip():
            last_error = StageInputMissingError(
                f"Empty model response for {expected_artifact_type}"
            )
            current_prompt = (
                "Tu respuesta anterior fue vacía. Devuelve UN SOLO objeto JSON "
                f"con artifact_type='{expected_artifact_type}', job_id='{expected_job_id}', "
                f"channel_id='{expected_channel_id}', y la sección data{{...}}.\n\n"
                + prompt
            )
            append_continuation = False
            continue
        is_fresh_envelope = bool(
            append_continuation
            and re.search(
                rf'"artifact_type"\s*:\s*"{re.escape(expected_artifact_type)}"',
                chunk,
            )
        )
        if is_fresh_envelope:
            raw_response = chunk
        else:
            raw_response = raw_response + chunk if append_continuation else chunk

        if "{" in raw_response and not is_json_object_complete(raw_response):
            last_error = StageInputMissingError(
                f"Truncated JSON response for {expected_artifact_type}"
            )
            last_preview = raw_response[:400].replace("\n", " ")
            current_prompt = (
                "Continúa EXACTAMENTE desde el último carácter de tu respuesta "
                "anterior hasta cerrar el objeto JSON completo. No repitas, no "
                "reinicies y no añadas markdown ni explicaciones. Incluye todos "
                "los campos finales que faltan, incluido warnings, y cierra cada "
                "corchete y llave."
            )
            append_continuation = True
            continue
        try:
            envelope = extract_json_envelope(raw_response)
            validate_envelope(
                envelope,
                expected_artifact_type=expected_artifact_type,
                expected_job_id=expected_job_id,
                expected_channel_id=expected_channel_id,
            )
            return envelope
        except Exception as exc:
            last_error = exc
            last_preview = raw_response[:400].replace("\n", " ")
            current_prompt = (
                f"ERROR: tu respuesta anterior no validó como envelope `{expected_artifact_type}`. "
                f"Razón: {str(exc)[:300]}. "
                "DEBES devolver EXACTAMENTE un objeto JSON con esta forma "
                "(sin markdown, sin texto adicional):\n"
                "```\n"
                "{\n"
                f'  "artifact_type": "{expected_artifact_type}",\n'
                f'  "job_id": "{expected_job_id}",\n'
                f'  "channel_id": "{expected_channel_id}",\n'
                '  "data": { ... }\n'
                "}\n"
                "```\n"
                "Vuelve a generar el artefacto cumpliendo este esquema.\n\n"
                + prompt
            )
            append_continuation = False
    raise StageInputMissingError(
        f"{expected_artifact_type} failed validation after {max_attempts} attempts: "
        f"{last_error}. Last preview: {last_preview!r}"
    )


def _scene_id_to_batch_index(batch_envelopes: list[dict]) -> dict[str, int]:
    scene_to_batch: dict[str, int] = {}
    for env in batch_envelopes:
        batch_index = int((env.get("data") or {}).get("batch_index") or env.get("batch_index") or 0)
        for scene in (env.get("data") or {}).get("scenes") or []:
            scene_id = str(scene.get("id") or "")
            if scene_id:
                scene_to_batch[scene_id] = batch_index
    return scene_to_batch


def _scene_ids_from_validation_error(error: str) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for match in re.finditer(r"\bScene\s+(scene-\d+)\b", error):
        scene_id = match.group(1)
        if scene_id not in seen:
            ids.append(scene_id)
            seen.add(scene_id)
    return ids


def _scene_batch_repair_prompt(
    *,
    channel_config: dict,
    script: dict,
    plan_envelope: dict,
    batch: dict,
    previous_envelope: dict,
    validation_error: str,
) -> str:
    base_prompt = _chatgpt_scenes_batch_prompt(
        channel_config,
        script,
        plan_envelope,
        batch,
    )
    return "\n".join(
        [
            "Validation failed for your previous scenes_batch.",
            "Regenerate the SAME batch only, fixing every validation error.",
            "Do not change scene IDs, requested scene range, job_id, channel_id, batch_index, or batch_total.",
            "Keep all valid narration/caption/on_screen_text/layout fields unless needed to satisfy validation.",
            "visual_prompt must be plain English ASCII for Pexels search. Do not use Spanish words or accented characters.",
            "",
            "Validation error:",
            validation_error,
            "",
            "Previous invalid envelope:",
            json.dumps(previous_envelope, ensure_ascii=False, indent=2),
            "",
            base_prompt,
        ]
    )


async def _merge_scene_batches_with_repair(
    *,
    job_dir: Path,
    job_id: str,
    channel_id: str,
    channel_config: dict,
    script: dict,
    plan_envelope: dict,
    batches: list[dict],
    batch_envelopes: list[dict],
    session_fn: SessionFn,
    scenes_logger: EventLogger,
    max_repair_attempts: int = 2,
) -> dict:
    batch_by_index = {int(batch.get("batch_index") or 0): batch for batch in batches}
    batch_total = len(batches)
    for repair_attempt in range(1, max_repair_attempts + 2):
        try:
            return merge_scene_batches(
                job_id=job_id,
                channel_id=channel_id,
                batch_envelopes=batch_envelopes,
                script=script,
            )
        except ShardValidationError as exc:
            if repair_attempt > max_repair_attempts:
                raise
            error_text = str(exc)
            scene_ids = _scene_ids_from_validation_error(error_text)
            scene_to_batch = _scene_id_to_batch_index(batch_envelopes)
            affected_indexes = sorted(
                {scene_to_batch[scene_id] for scene_id in scene_ids if scene_id in scene_to_batch}
            )
            if not affected_indexes:
                raise
            for batch_index in affected_indexes:
                batch = batch_by_index.get(batch_index)
                if batch is None:
                    raise
                previous = next(
                    (
                        env
                        for env in batch_envelopes
                        if int(((env.get("data") or {}).get("batch_index") or env.get("batch_index") or 0))
                        == batch_index
                    ),
                    None,
                )
                if previous is None:
                    raise
                scene_start = str(batch.get("scene_start") or "")
                scene_end = str(batch.get("scene_end") or "")
                scenes_logger.log(
                    "SCENES_PROMOTE_PROGRESS",
                    {
                        "job_id": job_id,
                        "step": "batch_repair_started",
                        "batch_index": batch_index,
                        "repair_attempt": repair_attempt,
                        "reason": error_text[:500],
                    },
                )
                repair_prompt = _scene_batch_repair_prompt(
                    channel_config=channel_config,
                    script=script,
                    plan_envelope=plan_envelope,
                    batch=batch,
                    previous_envelope=previous,
                    validation_error=error_text,
                )
                repaired = await _request_shard_envelope(
                    session_fn=session_fn,
                    prompt=repair_prompt,
                    expected_artifact_type="scenes_batch",
                    expected_job_id=job_id,
                    expected_channel_id=channel_id,
                )
                validate_scenes_batch(
                    repaired,
                    expected_batch_index=batch_index,
                    expected_batch_total=batch_total,
                    scene_start=scene_start,
                    scene_end=scene_end,
                )
                batch_path = job_dir / SCENES_BATCHES_DIR / f"scenes_batch_{batch_index:02d}.json"
                save_envelope(batch_path, repaired)
                for idx, env in enumerate(batch_envelopes):
                    env_index = int((env.get("data") or {}).get("batch_index") or env.get("batch_index") or 0)
                    if env_index == batch_index:
                        batch_envelopes[idx] = repaired
                        break
                scenes_logger.log(
                    "SCENES_PROMOTE_PROGRESS",
                    {
                        "job_id": job_id,
                        "step": "batch_repaired",
                        "batch_index": batch_index,
                        "repair_attempt": repair_attempt,
                    },
                )


async def auto_scenes_stage_sharded(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
    qa_session_fn: SessionFn | None = None,
) -> Path:
    state = load_job(job_dir)
    # Allow re-entry from scenes_promote so a resume after the pipeline
    # coroutine died (app restart, container kill) can continue the batch
    # loop instead of refusing because the "scenes" stage already completed.
    if state.current_stage not in ("scenes", "scenes_promote"):
        raise StageInputMissingError(
            f"Cannot auto-run sharded scenes from current_stage={state.current_stage!r}"
        )
    resume_after_scenes = state.current_stage == "scenes_promote"
    script_path = _resolve_artifact(job_dir, ARTIFACT_SCRIPT)
    if not script_path.exists():
        raise StageInputMissingError(f"Missing {script_path}")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    script = read_json(script_path)
    channel_config = read_yaml(channel_path)
    job_id = state.job_id
    channel_id = state.channel_id

    plan_prompt = _chatgpt_scenes_plan_prompt(channel_config, script)
    prompt_path = job_dir / SCENES_PROMPT_PATH
    if not resume_after_scenes:
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(prompt_path, plan_prompt, encoding="utf-8")
        _complete_stage(job_dir, "scenes", prompt_path)

    # The batch loop is scenes_promote work: mark it in_progress (and clear a
    # stale error from a previous failed attempt) so the dashboard reflects
    # the live retry instead of the old failure.
    _start_stage(job_dir, "scenes_promote")

    prewarm_queue: asyncio.Queue | None = None
    prewarm_task: asyncio.Task | None = None
    try:
        cached_plan_path = job_dir / SCENES_PLAN_PATH
        if resume_after_scenes and cached_plan_path.exists():
            plan_envelope = json.loads(cached_plan_path.read_text(encoding="utf-8"))
        else:
            plan_envelope = await _request_shard_envelope(
                session_fn=session_fn,
                prompt=plan_prompt,
                expected_artifact_type="scenes_plan",
                expected_job_id=job_id,
                expected_channel_id=channel_id,
            )
        plan_envelope = _limit_scenes_plan_batch_size(plan_envelope)
        validate_scenes_plan(plan_envelope)
        save_envelope(cached_plan_path, plan_envelope)

        batches = (plan_envelope.get("data") or {}).get("batches") or []
        if not isinstance(batches, list) or not batches:
            raise ShardValidationError("scenes_plan returned no batches")

        batch_envelopes: list[dict] = []
        batch_total = len(batches)

        # Overlap Gemini QA with generation: as each batch lands, a consumer
        # task QAs completed 8-scene slices on the Gemini session while
        # ChatGPT keeps writing the next batch. Best-effort — the scenes_qa
        # stage reuses only prewarmed verdicts whose content hash still
        # matches. Kill switch: SCENES_QA_PREWARM=0.
        if (
            qa_session_fn is not None
            and os.environ.get("SCENES_QA_PREWARM", "1").strip() != "0"
        ):
            target_count = int(
                (plan_envelope.get("data") or {}).get("target_scene_count") or 0
            )
            expected_qa_total = (
                -(-target_count // _QA_BATCH_SIZE) if target_count > 0 else 1
            )
            prewarm_queue = asyncio.Queue()
            prewarm_task = asyncio.create_task(
                _prewarm_scenes_qa(
                    job_dir=job_dir,
                    job_id=job_id,
                    channel_id=channel_id,
                    channel_config=channel_config,
                    session_fn=qa_session_fn,
                    queue=prewarm_queue,
                    expected_qa_total=expected_qa_total,
                    scenes_logger=EventLogger(job_dir / EVENT_LOG),
                )
            )
        # On resume, replay any already-saved batch envelopes from disk so
        # the loop continues from the first un-saved batch instead of
        # re-querying ChatGPT for batches we already have.
        existing_batches: dict[int, dict] = {}
        if resume_after_scenes:
            for f in sorted((job_dir / SCENES_BATCHES_DIR).glob("scenes_batch_*.json")):
                try:
                    env = json.loads(f.read_text(encoding="utf-8"))
                    idx = int((env.get("data") or {}).get("batch_index") or env.get("batch_index") or 0)
                    existing_batches[idx] = env
                except Exception:
                    continue
        scenes_logger = EventLogger(job_dir / EVENT_LOG)
        scenes_logger.log(
            "SCENES_PROMOTE_PROGRESS",
            {
                "job_id": job_id,
                "step": "plan_received",
                "batches_total": batch_total,
                "batches_done": 0,
            },
        )
        for batch in batches:
            if not isinstance(batch, dict):
                raise ShardValidationError("Plan batch must be an object")
            batch_index = int(batch.get("batch_index") or 0)
            scene_start = str(batch.get("scene_start") or "")
            scene_end = str(batch.get("scene_end") or "")
            # Reuse already-persisted batch on resume.
            if batch_index in existing_batches:
                batch_envelopes.append(existing_batches[batch_index])
                if prewarm_queue is not None:
                    prewarm_queue.put_nowait(
                        list(
                            (existing_batches[batch_index].get("data") or {}).get("scenes")
                            or []
                        )
                    )
                scenes_logger.log(
                    "SCENES_PROMOTE_PROGRESS",
                    {
                        "job_id": job_id,
                        "step": "batch_reused",
                        "batch_index": batch_index,
                        "batches_total": batch_total,
                        "batches_done": len(batch_envelopes),
                    },
                )
                continue
            scenes_logger.log(
                "SCENES_PROMOTE_PROGRESS",
                {
                    "job_id": job_id,
                    "step": "batch_started",
                    "batch_index": batch_index,
                    "batches_total": batch_total,
                    "batches_done": len(batch_envelopes),
                },
            )
            batch_prompt = _batch_contract_header(
                batch_index=batch_index,
                batch_total=batch_total,
                scene_start=scene_start,
                scene_end=scene_end,
            ) + _chatgpt_scenes_batch_prompt(
                channel_config,
                script,
                plan_envelope,
                batch,
            )
            batch_envelope = await _request_shard_envelope(
                session_fn=session_fn,
                prompt=batch_prompt,
                expected_artifact_type="scenes_batch",
                expected_job_id=job_id,
                expected_channel_id=channel_id,
            )
            # ChatGPT sometimes re-answers a previous batch (wrong
            # batch_index / scene range). Retry with the repair prompt —
            # which embeds the invalid envelope and the validation error —
            # instead of failing the whole stage on the first slip.
            for validate_attempt in range(_BATCH_VALIDATE_ATTEMPTS):
                try:
                    validate_scenes_batch(
                        batch_envelope,
                        expected_batch_index=batch_index,
                        expected_batch_total=batch_total,
                        scene_start=scene_start,
                        scene_end=scene_end,
                    )
                    break
                except ShardValidationError as exc:
                    if validate_attempt >= _BATCH_VALIDATE_ATTEMPTS - 1:
                        raise
                    scenes_logger.log(
                        "SCENES_PROMOTE_PROGRESS",
                        {
                            "job_id": job_id,
                            "step": "batch_retry",
                            "batch_index": batch_index,
                            "batches_total": batch_total,
                            "batches_done": len(batch_envelopes),
                            "reason": str(exc)[:500],
                        },
                    )
                    repair_prompt = _scene_batch_repair_prompt(
                        channel_config=channel_config,
                        script=script,
                        plan_envelope=plan_envelope,
                        batch=batch,
                        previous_envelope=batch_envelope,
                        validation_error=str(exc),
                    )
                    batch_envelope = await _request_shard_envelope(
                        session_fn=session_fn,
                        prompt=repair_prompt,
                        expected_artifact_type="scenes_batch",
                        expected_job_id=job_id,
                        expected_channel_id=channel_id,
                    )
            batch_path = job_dir / SCENES_BATCHES_DIR / f"scenes_batch_{batch_index:02d}.json"
            save_envelope(batch_path, batch_envelope)
            batch_envelopes.append(batch_envelope)
            if prewarm_queue is not None:
                prewarm_queue.put_nowait(
                    list((batch_envelope.get("data") or {}).get("scenes") or [])
                )
            scenes_logger.log(
                "SCENES_PROMOTE_PROGRESS",
                {
                    "job_id": job_id,
                    "step": "batch_saved",
                    "batch_index": batch_index,
                    "batches_total": batch_total,
                    "batches_done": len(batch_envelopes),
                },
            )

        if prewarm_queue is not None:
            prewarm_queue.put_nowait(None)

        merged = await _merge_scene_batches_with_repair(
            job_dir=job_dir,
            job_id=job_id,
            channel_id=channel_id,
            channel_config=channel_config,
            script=script,
            plan_envelope=plan_envelope,
            batches=batches,
            batch_envelopes=batch_envelopes,
            session_fn=session_fn,
            scenes_logger=scenes_logger,
        )
        scenes_path = _resolve_artifact(job_dir, ARTIFACT_SCENES)
        _write_json(scenes_path, merged)
        if prewarm_task is not None:
            # Wait for the QA prewarm to drain so the scenes_qa stage never
            # runs concurrently with it on the same Gemini session. The
            # consumer swallows its own errors; this is belt-and-braces.
            try:
                await prewarm_task
            except Exception:
                pass
    except Exception as exc:
        if prewarm_task is not None and not prewarm_task.done():
            prewarm_task.cancel()
        raise StageInputMissingError(str(exc)) from exc

    _complete_stage(job_dir, "scenes_promote", scenes_path)
    return scenes_path


async def auto_scenes_qa_stage_sharded(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    state = load_job(job_dir)
    if not dag_mode() and state.current_stage != "scenes_qa":
        raise StageInputMissingError(
            f"Cannot auto-run sharded scenes_qa from current_stage={state.current_stage!r}"
        )
    scenes_path = _resolve_artifact(job_dir, ARTIFACT_SCENES)
    if not scenes_path.exists():
        raise StageInputMissingError(f"Missing {scenes_path}")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    _start_stage(job_dir, "scenes_qa")
    state = load_job(job_dir)
    scenes_doc = read_json(scenes_path)
    channel_config = read_yaml(channel_path)
    scenes = scenes_doc.get("scenes") or []
    if not isinstance(scenes, list) or not scenes:
        raise StageInputMissingError("scenes.json contains no scenes to QA")

    scene_batches = [
        scenes[index:index + _QA_BATCH_SIZE]
        for index in range(0, len(scenes), _QA_BATCH_SIZE)
    ]
    qa_envelopes: list[dict] = []
    batch_total = len(scene_batches)
    scenes_logger = EventLogger(job_dir / EVENT_LOG)
    # (envelope, fresh_by_mtime) — hash-carrying envelopes (prewarm or newer
    # runs) are judged by content hash in the loop below; legacy hash-less
    # files fall back to the mtime rule.
    existing_batches: dict[int, tuple[dict, bool]] = {}
    batch_dir = job_dir / SCENES_QA_BATCHES_DIR
    scenes_mtime = scenes_path.stat().st_mtime
    for f in sorted(batch_dir.glob("scenes_qa_batch_*.json")):
        try:
            env = json.loads(f.read_text(encoding="utf-8"))
            validate_envelope(
                env,
                expected_artifact_type="scenes_qa_batch",
                expected_job_id=state.job_id,
                expected_channel_id=state.channel_id,
            )
            idx = int(env.get("batch_index") or 0)
            if idx:
                existing_batches[idx] = (env, f.stat().st_mtime >= scenes_mtime)
        except Exception:
            continue
    scenes_logger.log(
        "SCENES_QA_PROGRESS",
        {
            "job_id": state.job_id,
            "step": "plan_received",
            "batches_total": batch_total,
            "batches_done": 0,
        },
    )
    try:
        for batch_index, batch_scenes in enumerate(scene_batches, start=1):
            cached = existing_batches.get(batch_index)
            if cached is not None:
                env, fresh_by_mtime = cached
                cached_hash = env.get("source_content_hash")
                fresh = (
                    cached_hash == _scenes_content_hash(batch_scenes)
                    if cached_hash
                    else fresh_by_mtime
                )
                if fresh:
                    qa_envelopes.append(env)
                    scenes_logger.log(
                        "SCENES_QA_PROGRESS",
                        {
                            "job_id": state.job_id,
                            "step": "batch_reused",
                            "batch_index": batch_index,
                            "batches_total": batch_total,
                            "batches_done": len(qa_envelopes),
                        },
                    )
                    continue
            envelope = await _qa_scenes_batch_envelope(
                job_dir=job_dir,
                job_id=state.job_id,
                channel_id=state.channel_id,
                channel_config=channel_config,
                session_fn=session_fn,
                batch_scenes=batch_scenes,
                batch_index=batch_index,
                batch_total=batch_total,
                scenes_logger=scenes_logger,
            )
            qa_envelopes.append(envelope)

        merged = merge_scenes_qa_batches(
            job_id=state.job_id,
            channel_id=state.channel_id,
            qa_batch_envelopes=qa_envelopes,
        )
        output_path = job_dir / "operator" / "gemini" / "scenes_qa.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(output_path, merged)
    except Exception as exc:
        raise StageInputMissingError(str(exc)) from exc

    if str(merged.get("verdict") or "").upper() != "PASS":
        issues = merged.get("issues") or merged.get("required_changes") or []
        raise StageInputMissingError(f"Gemini QA verdict for scenes is {merged.get('verdict')}: {issues}")
    _complete_stage(job_dir, "scenes_qa", output_path)
    return output_path
