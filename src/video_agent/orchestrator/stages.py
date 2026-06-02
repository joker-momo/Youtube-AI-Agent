from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from video_agent.contracts import EVENT_LOG, repo_root
from video_agent.operator import (
    _chatgpt_scenes_batch_prompt,
    _chatgpt_scenes_plan_prompt,
    _chatgpt_scenes_prompt,
    _chatgpt_seo_prompt,
    _chatgpt_script_prompt,
    _gemini_scenes_qa_batch_prompt,
    _gemini_qa_prompt,
    extract_json_object,
    extract_json_objects,
    get_scenes_qa_feedback,
    promote_operator_artifact,
    promote_operator_qa,
    write_operator_review,
)
from video_agent.operator_shards import (
    ShardValidationError,
    extract_json_envelope,
    merge_scenes_qa_batches,
    merge_scene_batches,
    save_envelope,
    validate_envelope,
    validate_scenes_batch,
    validate_scenes_plan,
)
from video_agent.utils.json_io import write_json as _write_json
from video_agent.operator_validators import _looks_like_spanish_visual_prompt
from video_agent.orchestrator.job_state import load_job, save_job
from video_agent.orchestrator.orchestrator import _now
from video_agent.pipeline import OperatorRenderOptions, render_operator_job
from video_agent.utils.json_io import read_json, read_yaml
from video_agent.utils.logging import EventLogger
from video_agent.runtime.providers import AUDIO_SUBPROCESS_ENV, SubprocessAudioTaskProvider
from video_agent.storage.atomic import atomic_write_text
from video_agent.storage.public_jobs import prepare_public_job_dir

IDEA_FILE = "idea.json"
SCRIPT_PROMPT_PATH = Path("operator/chatgpt/script_prompt.md")
SCRIPT_RAW_PATH = Path("operator/chatgpt/script.raw.txt")
SCENES_PROMPT_PATH = Path("operator/chatgpt/scenes_prompt.md")
SCENES_RAW_PATH = Path("operator/chatgpt/scenes.raw.txt")
SCENES_PLAN_PATH = Path("operator/chatgpt/scenes_plan.json")
SCENES_BATCHES_DIR = Path("operator/chatgpt/scenes_batches")
SCENES_QA_BATCHES_DIR = Path("operator/gemini/scenes_qa_batches")
SEO_PROMPT_PATH = Path("operator/chatgpt/seo_prompt.md")
SEO_RAW_PATH = Path("operator/chatgpt/seo.raw.txt")
SCRIPT_QA_RAW_PATH = Path("operator/gemini/script_qa.raw.txt")
SCENES_QA_RAW_PATH = Path("operator/gemini/scenes_qa.raw.txt")
SEO_QA_RAW_PATH = Path("operator/gemini/seo_qa.raw.txt")


class StageInputMissingError(Exception):
    pass


_AUDIO_SUBPROCESS_ENV = AUDIO_SUBPROCESS_ENV


def _run_blocking_with_timeout(
    label: str,
    timeout_sec: int,
    fn: Callable,
    *args,
    **kwargs,
):
    """Run blocking work in a helper thread with a hard timeout."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError as exc:
            raise RuntimeError(
                f"{label} timed out after {timeout_sec}s. "
                "Please restart worker and resume this stage."
            ) from exc


def _start_stage(job_dir: Path, stage_name: str) -> None:
    """Mark ``stage_name`` as in_progress with a real ``started_at`` timestamp.

    The pipeline used to transition stages directly from ``pending`` to
    ``completed`` inside ``_complete_stage`` with both timestamps set to
    ``now``, which reported every stage as taking 0 seconds. Stage runners
    that wrap real work (script generation, scene shards, render, etc.)
    should call ``_start_stage`` at the top of their work so the dashboard
    shows the real elapsed time once the stage finishes.

    Idempotent: only sets ``started_at`` if it has not been set yet, so a
    re-run after a transient failure does not overwrite the original
    timestamp.
    """
    state = load_job(job_dir)
    stage = state.stage(stage_name)
    ts = _now()
    if stage.status not in {"completed", "skipped"} and stage.started_at is None:
        stage.started_at = ts
    if stage.status == "pending":
        stage.status = "in_progress"
    state.updated_at = ts
    save_job(job_dir, state)


def _complete_stage(job_dir: Path, stage_name: str, output: Path) -> None:
    state = load_job(job_dir)
    ts = _now()
    stage = state.stage(stage_name)
    if stage.started_at is None:
        # Stage was never explicitly started. Best-effort guess: use the
        # most recent completed_at of any earlier stage in the list. The
        # pipeline runs stages sequentially, so the previous stage's end
        # is a reasonable proxy for this stage's start. Falls back to
        # ``ts`` (duration = 0) only when no earlier stage carries a
        # timestamp at all (e.g. very first stage in a brand-new job).
        previous_end: str | None = None
        for earlier in state.stages:
            if earlier.name == stage_name:
                break
            if earlier.completed_at:
                previous_end = earlier.completed_at
        stage.started_at = previous_end or ts
    stage.status = "completed"
    stage.completed_at = ts
    next_pending = next((s for s in state.stages if s.status == "pending"), None)
    if next_pending is not None:
        state.current_stage = next_pending.name
    state.updated_at = ts
    save_job(job_dir, state)

    logger = EventLogger(job_dir / EVENT_LOG)
    logger.log(
        "STAGE_COMPLETED",
        {
            "job_id": state.job_id,
            "stage": stage_name,
            "output": str(output.relative_to(job_dir)),
        },
    )
    if next_pending is None:
        logger.log("JOB_COMPLETED", {"job_id": state.job_id})


def run_script_stage(job_dir: Path, channel_path: Path) -> Path:
    """Produce the ChatGPT script prompt for the job.

    Reads ``job_dir/idea.json`` and the channel YAML, renders the prompt
    via the existing v2 helper, writes ``operator/chatgpt/script_prompt.md``,
    marks the ``script`` stage completed, and emits a ``STAGE_COMPLETED``
    event so consumers of ``events.jsonl`` see the same shape as v2.
    """
    idea_path = job_dir / IDEA_FILE
    if not idea_path.exists():
        raise StageInputMissingError(f"Missing {idea_path}")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    state = load_job(job_dir)
    if state.current_stage != "script":
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
    if state.current_stage != "script_promote":
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


def run_scenes_stage(job_dir: Path, channel_path: Path) -> Path:
    script_path = job_dir / "script.json"
    if not script_path.exists():
        raise StageInputMissingError(f"Missing {script_path}")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    state = load_job(job_dir)
    if state.current_stage != "scenes":
        raise StageInputMissingError(
            f"Cannot run scenes stage from current_stage={state.current_stage!r}"
        )

    script = read_json(script_path)
    channel_config = read_yaml(channel_path)
    qa_feedback = get_scenes_qa_feedback(job_dir)
    prompt_text = _chatgpt_scenes_prompt(channel_config, script, qa_feedback=qa_feedback)

    output_path = job_dir / SCENES_PROMPT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path, prompt_text, encoding="utf-8")

    _complete_stage(job_dir, "scenes", output_path)
    return output_path


def promote_scenes_stage(job_dir: Path, channel_path: Path, raw_response: str) -> Path:
    state = load_job(job_dir)
    if state.current_stage != "scenes_promote":
        raise StageInputMissingError(
            f"Cannot run scenes_promote stage from current_stage={state.current_stage!r}"
        )
    if not raw_response.strip():
        raise StageInputMissingError("Missing raw ChatGPT scenes response.")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    raw_path = job_dir / SCENES_RAW_PATH
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(raw_path, raw_response, encoding="utf-8")

    try:
        result = promote_operator_artifact(
            job_dir,
            "scenes",
            raw_path,
            channel_path=channel_path,
        )
    except ValueError as exc:
        raise StageInputMissingError(str(exc)) from exc

    _complete_stage(job_dir, "scenes_promote", result.output_path)
    return result.output_path


def run_seo_stage(job_dir: Path, channel_path: Path) -> Path:
    script_path = job_dir / "script.json"
    scenes_path = job_dir / "scenes.json"
    if not script_path.exists():
        raise StageInputMissingError(f"Missing {script_path}")
    if not scenes_path.exists():
        raise StageInputMissingError(f"Missing {scenes_path}")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    state = load_job(job_dir)
    if state.current_stage != "seo":
        raise StageInputMissingError(
            f"Cannot run seo stage from current_stage={state.current_stage!r}"
        )

    script = read_json(script_path)
    scenes = read_json(scenes_path)
    channel_config = read_yaml(channel_path)
    prompt_text = _chatgpt_seo_prompt(channel_config, script, scenes)

    output_path = job_dir / SEO_PROMPT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path, prompt_text, encoding="utf-8")

    _complete_stage(job_dir, "seo", output_path)
    return output_path


def promote_seo_stage(job_dir: Path, channel_path: Path, raw_response: str) -> Path:
    state = load_job(job_dir)
    if state.current_stage != "seo_promote":
        raise StageInputMissingError(
            f"Cannot run seo_promote stage from current_stage={state.current_stage!r}"
        )
    if not raw_response.strip():
        raise StageInputMissingError("Missing raw ChatGPT SEO response.")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    raw_path = job_dir / SEO_RAW_PATH
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(raw_path, raw_response, encoding="utf-8")

    try:
        result = promote_operator_artifact(
            job_dir,
            "seo",
            raw_path,
            channel_path=channel_path,
        )
    except ValueError as exc:
        raise StageInputMissingError(str(exc)) from exc

    _complete_stage(job_dir, "seo_promote", result.output_path)
    return result.output_path


def _run_audio_subprocess(command: str, job_dir: Path) -> Path:
    if command != "whisper-timestamps":
        raise StageInputMissingError(
            f"Unsupported audio subprocess command: {command}"
        )
    try:
        return SubprocessAudioTaskProvider().run_whisper_timestamps(job_dir)
    except RuntimeError as exc:
        raise StageInputMissingError(str(exc)) from exc


def run_whisper_timestamps_stage(job_dir: Path) -> Path:
    state = load_job(job_dir)
    if state.current_stage != "whisper_timestamps":
        raise StageInputMissingError(
            f"Cannot run whisper_timestamps stage from current_stage={state.current_stage!r}"
        )
    if os.environ.get(_AUDIO_SUBPROCESS_ENV) != "1":
        return _run_audio_subprocess("whisper-timestamps", job_dir)
    return _run_whisper_timestamps_stage_inline(job_dir)


def _rebase_words_to_scene_timestamps(scenes: list[dict], all_words: list[dict]) -> list[dict]:
    scene_data = []
    offset = 0.0
    for scene in scenes:
        dur = float(scene.get("duration_sec") or 15)
        scene_end = offset + dur
        scene_words = [
            w for w in all_words
            if offset <= (float(w["start"]) + float(w["end"])) / 2 < scene_end
        ]
        rebased = []
        for w in scene_words:
            start = max(0.0, float(w["start"]) - offset)
            end = min(dur, max(start, float(w["end"]) - offset))
            rebased.append({
                "text": str(w["word"]).strip(),
                "start": round(start, 4),
                "end": round(end, 4),
            })
        scene_data.append({
            "scene_id": scene["id"],
            "audio_offset_sec": round(offset, 4),
            "word_segments": rebased,
        })
        offset = scene_end
    return scene_data


def _run_whisper_timestamps_stage_inline(job_dir: Path) -> Path:
    """Run Whisper on narration.wav and write per-scene word segments.

    Reads ``jobs/<id>/assets/narration.wav`` (produced by assets_chatgpt),
    runs Whisper tiny model with word_timestamps=True, groups words into
    ~7-word chunks, maps chunks to scenes by cumulative duration offset,
    and writes ``jobs/<id>/whisper_timestamps.json``.
    """
    state = load_job(job_dir)
    logger = EventLogger(job_dir / EVENT_LOG)
    logger.log("WHISPER_STAGE_PROGRESS", {"job_id": state.job_id, "step": "start"})

    narration_path = job_dir / "assets" / "narration.wav"
    synth_timeout_sec = int(os.environ.get("WHISPER_SYNTH_TIMEOUT_SEC", "900"))
    whisper_load_timeout_sec = int(os.environ.get("WHISPER_MODEL_LOAD_TIMEOUT_SEC", "300"))
    whisper_transcribe_timeout_sec = int(os.environ.get("WHISPER_TRANSCRIBE_TIMEOUT_SEC", "1800"))
    if not narration_path.exists():
        from video_agent.stages.assets import prepare_assets

        channel_config_path = Path(
            os.environ.get(
                "CHANNEL_CONFIG",
                "/app/configs/vida-plena-45/channel.yaml",
            )
        )
        if not channel_config_path.exists():
            channel_config_path = repo_root() / "configs/vida-plena-45/channel.yaml"

        if not channel_config_path.exists():
            raise StageInputMissingError(
                f"Missing narration audio: {narration_path} and cannot auto-synthesize because channel config was not found."
            )

        channel_config = read_yaml(channel_config_path)
        style = read_json(repo_root() / channel_config["style_dna"]["path"])
        scenes_path = job_dir / "scenes.json"
        if not scenes_path.exists():
            raise StageInputMissingError(f"Missing {scenes_path}")
        scene_doc = read_json(scenes_path)

        logger.log(
            "WHISPER_STAGE_PROGRESS",
            {
                "job_id": state.job_id,
                "step": "synthesizing_narration_audio",
                "timeout_sec": synth_timeout_sec,
            },
        )
        _run_blocking_with_timeout(
            label="Narration synthesis",
            timeout_sec=synth_timeout_sec,
            fn=prepare_assets,
            job_dir=job_dir,
            style_dna=style,
            scene_doc=scene_doc,
            visual_config=channel_config.get("visuals"),
            tts_config=channel_config.get("tts"),
            channel_id=channel_config["channel"]["id"],
        )
        logger.log(
            "WHISPER_STAGE_PROGRESS",
            {"job_id": state.job_id, "step": "narration_audio_ready"},
        )

    if not narration_path.exists():
        raise StageInputMissingError(f"Missing narration audio: {narration_path}")

    scenes_path = job_dir / "scenes.json"
    if not scenes_path.exists():
        raise StageInputMissingError(f"Missing {scenes_path}")

    import threading
    import time
    import wave
    import whisper  # lazy import — heavy dep

    scene_doc = read_json(scenes_path)
    scenes = scene_doc["scenes"]

    # --- Log audio file metadata ---
    audio_size_bytes = narration_path.stat().st_size
    audio_duration_sec: float | None = None
    try:
        with wave.open(str(narration_path), "rb") as wf:
            audio_duration_sec = round(wf.getnframes() / wf.getframerate(), 2)
    except Exception:
        pass
    logger.log(
        "WHISPER_STAGE_PROGRESS",
        {
            "job_id": state.job_id,
            "step": "audio_info",
            "file": narration_path.name,
            "size_bytes": audio_size_bytes,
            "size_mb": round(audio_size_bytes / 1_048_576, 2),
            "duration_sec": audio_duration_sec,
            "scene_count": len(scenes),
        },
    )

    logger.log(
        "WHISPER_STAGE_PROGRESS",
        {
            "job_id": state.job_id,
            "step": "loading_whisper_model_tiny",
            "timeout_sec": whisper_load_timeout_sec,
        },
    )
    model = _run_blocking_with_timeout(
        label="Whisper model load",
        timeout_sec=whisper_load_timeout_sec,
        fn=whisper.load_model,
        name="tiny",
    )
    logger.log(
        "WHISPER_STAGE_PROGRESS",
        {
            "job_id": state.job_id,
            "step": "whisper_model_loaded",
        },
    )
    logger.log(
        "WHISPER_STAGE_PROGRESS",
        {
            "job_id": state.job_id,
            "step": "transcribing_audio",
            "timeout_sec": whisper_transcribe_timeout_sec,
            "audio_duration_sec": audio_duration_sec,
        },
    )

    # --- Heartbeat thread: emit progress every 10s while transcribing ---
    _transcribe_done = threading.Event()
    _transcribe_start = time.monotonic()

    def _heartbeat_thread():
        heartbeat_interval = 10
        while not _transcribe_done.wait(timeout=heartbeat_interval):
            elapsed = round(time.monotonic() - _transcribe_start, 1)
            pct: float | None = None
            if audio_duration_sec and audio_duration_sec > 0:
                # Whisper tiny ~10–15× realtime on CPU; estimate progress
                estimated_total = audio_duration_sec / 12.0
                pct = round(min(elapsed / estimated_total * 100, 99.0), 1)
            logger.log(
                "WHISPER_STAGE_PROGRESS",
                {
                    "job_id": state.job_id,
                    "step": "transcribing_audio_heartbeat",
                    "elapsed_sec": elapsed,
                    "estimated_pct": pct,
                },
            )

    hb = threading.Thread(target=_heartbeat_thread, daemon=True)
    hb.start()
    try:
        result = _run_blocking_with_timeout(
            label="Whisper transcription",
            timeout_sec=whisper_transcribe_timeout_sec,
            fn=model.transcribe,
            audio=str(narration_path),
            word_timestamps=True,
            language="es",
            fp16=False,
        )
    finally:
        _transcribe_done.set()
    elapsed_total = round(time.monotonic() - _transcribe_start, 1)
    # --- Log transcription stats ---
    segments = result.get("segments") or []
    total_words_raw = sum(len(seg.get("words") or []) for seg in segments)
    audio_covered_sec: float | None = None
    if segments:
        try:
            audio_covered_sec = round(float(segments[-1]["end"]), 2)
        except Exception:
            pass
    logger.log(
        "WHISPER_STAGE_PROGRESS",
        {
            "job_id": state.job_id,
            "step": "transcription_complete",
            "elapsed_sec": elapsed_total,
            "segment_count": len(segments),
            "word_count": total_words_raw,
            "audio_covered_sec": audio_covered_sec,
        },
    )

    # Flatten all words from all segments (cast np.float64 → float for JSON)
    all_words: list[dict] = []
    for seg in segments:
        for w in seg.get("words") or []:
            all_words.append({"word": w["word"].strip(), "start": float(w["start"]), "end": float(w["end"])})
    logger.log(
        "WHISPER_STAGE_PROGRESS",
        {
            "job_id": state.job_id,
            "step": "mapping_words_to_scenes",
            "total_words": len(all_words),
            "scene_count": len(scenes),
        },
    )

    # Map individual rebased words to scenes by cumulative audio offset.
    # Boundary-spanning words can start slightly before a scene offset; clamp
    # them to the scene range so render_props remains schema-valid.
    scene_data = _rebase_words_to_scene_timestamps(scenes, all_words)
    scenes_with_words = sum(1 for s in scene_data if s["word_segments"])
    scenes_without_words = len(scene_data) - scenes_with_words
    logger.log(
        "WHISPER_STAGE_PROGRESS",
        {
            "job_id": state.job_id,
            "step": "scene_mapping_complete",
            "scenes_with_words": scenes_with_words,
            "scenes_without_words": scenes_without_words,
            "total_scenes": len(scenes),
            "total_audio_duration_sec": round(
                sum(float(scene.get("duration_sec") or 15) for scene in scenes),
                2,
            ),
        },
    )

    output = {"scenes": scene_data}
    output_path = job_dir / "whisper_timestamps.json"
    from video_agent.utils.json_io import write_json
    write_json(output_path, output)
    logger.log(
        "WHISPER_STAGE_PROGRESS",
        {"job_id": state.job_id, "step": "timestamps_written"},
    )

    # Re-anchor the YouTube chapter timestamps in seo.json against the
    # real scene timeline. seo_promote may have run when scenes.json was
    # still being reworked or when ChatGPT injected timestamps stretching
    # past actual video length (e.g. last chapter at 16:12 for an 11-min
    # video). Idempotent — safe even if chapters are already correct.
    try:
        from video_agent.operator import (
            _compute_chapter_timestamps,
            _rewrite_description_chapters,
        )
        seo_path = job_dir / "seo.json"
        scenes_path = job_dir / "scenes.json"
        script_path = job_dir / "script.json"
        if seo_path.exists() and scenes_path.exists():
            seo_obj = json.loads(seo_path.read_text(encoding="utf-8"))
            scene_doc = json.loads(scenes_path.read_text(encoding="utf-8"))
            script_obj = (
                json.loads(script_path.read_text(encoding="utf-8"))
                if script_path.exists()
                else None
            )
            new_chapters = _compute_chapter_timestamps(scene_doc, script_obj)
            if new_chapters:
                seo_obj["description"] = _rewrite_description_chapters(
                    seo_obj.get("description", ""), new_chapters
                )
                from video_agent.utils.json_io import write_json as _wj
                _wj(seo_path, seo_obj)
                logger.log(
                    "WHISPER_STAGE_PROGRESS",
                    {
                        "job_id": state.job_id,
                        "step": "seo_chapters_resynced",
                        "chapter_count": len(new_chapters),
                    },
                )
    except Exception as exc:
        logger.log(
            "WHISPER_STAGE_PROGRESS",
            {
                "job_id": state.job_id,
                "step": "seo_chapters_resync_failed",
                "error": str(exc)[:200],
            },
        )

    _complete_stage(job_dir, "whisper_timestamps", output_path)
    return output_path


def run_render_stage(job_dir: Path, channel_path: Path, *, notify_telegram: bool = True) -> Path:
    state = load_job(job_dir)
    if state.current_stage != "render":
        raise StageInputMissingError(
            f"Cannot run render stage from current_stage={state.current_stage!r}"
        )
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    try:
        render_operator_job(
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
        output_path = write_operator_review(job_dir)
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

    script = read_json(job_dir / "script.json")
    scenes = read_json(job_dir / "scenes.json")
    seo = read_json(job_dir / "seo.json")
    visual_review = read_json(job_dir / "visual_review.json")
    whisper_ok = (job_dir / "whisper_timestamps.json").exists()
    video_ok = (job_dir / "video.mp4").exists()

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
                profile_txt = (repo_root() / profile_rel).read_text(encoding="utf-8")
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
    output_path = job_dir / "persona_eval.json"
    _write_json(output_path, payload)
    _complete_stage(job_dir, "persona_eval", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Gemini QA stages (script_qa, scenes_qa, seo_qa).
# ---------------------------------------------------------------------------

_QA_ARTIFACT_FILE = {
    "script": "script.json",
    "scenes": "scenes.json",
    "seo": "seo.json",
}
_QA_RAW_PATH = {
    "script": SCRIPT_QA_RAW_PATH,
    "scenes": SCENES_QA_RAW_PATH,
    "seo": SEO_QA_RAW_PATH,
}


def promote_qa_stage(
    job_dir: Path,
    artifact: str,
    raw_response: str,
    *,
    channel_path: Path | None = None,
) -> Path:
    """Promote a raw Gemini QA response into ``operator/gemini/<art>_qa.json``.

    ``artifact`` is one of ``script``, ``scenes``, ``seo``. The stage
    name written to the job state is ``<artifact>_qa``. Verdict must
    be PASS to advance; on NEEDS_REWORK (or any other non-PASS value)
    the parsed QA JSON is still saved so the operator can inspect the
    issues, and ``StageInputMissingError`` is raised with the issue
    list so /run-all halts cleanly at this stage.
    """
    if artifact not in _QA_ARTIFACT_FILE:
        raise StageInputMissingError(f"Unsupported QA artifact: {artifact}")
    stage_name = f"{artifact}_qa"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} from current_stage={state.current_stage!r}"
        )
    if not raw_response.strip():
        raise StageInputMissingError(f"Missing raw Gemini QA response for {artifact}")

    raw_path = job_dir / _QA_RAW_PATH[artifact]
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(raw_path, raw_response, encoding="utf-8")

    qa_output = job_dir / "operator" / "gemini" / f"{artifact}_qa.json"

    try:
        result = promote_operator_qa(job_dir, artifact, raw_path)
    except ValueError as exc:
        # Verdict != PASS or shape invalid. We still want a parsed QA
        # file on disk so the operator can read the issues/required
        # changes without grepping raw text. Parse defensively; if the
        # raw response is not JSON at all, surface the original error.
        try:
            parsed = extract_json_object(raw_response)
        except Exception:
            # extract_json_object failed (e.g. truncated prefix from Gemini UI).
            # Try extract_json_objects which tolerates corrupt leading chunks.
            objects = extract_json_objects(raw_response)
            if not objects:
                raise StageInputMissingError(str(exc)) from exc
            # Use the last complete object (most likely the full response).
            parsed = objects[-1]
        qa_payload = {
            "artifact": artifact,
            "verdict": str(parsed.get("verdict", "")).upper() or "MISSING",
            "issues": parsed.get("issues") or [],
            "required_changes": (
                parsed.get("required_changes")
                or parsed.get("suggested_fixes")
                or []
            ),
            "scores": parsed.get("scores") or {},
        }
        qa_output.parent.mkdir(parents=True, exist_ok=True)
        _write_json(qa_output, qa_payload)
        raise StageInputMissingError(
            f"Gemini QA verdict for {artifact} is "
            f"{qa_payload['verdict']}: {qa_payload['issues']}"
        ) from exc

    qa_payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    if artifact == "seo":
        _enforce_seo_language_qa(
            job_dir,
            result.output_path,
            qa_payload,
            channel_path=channel_path,
        )
        qa_payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    elif artifact == "scenes":
        _enforce_scenes_visual_prompt_english(job_dir, result.output_path, qa_payload)
        qa_payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    verdict = str(qa_payload.get("verdict", "")).upper()
    if verdict != "PASS":
        issues = qa_payload.get("issues") or qa_payload.get("required_changes") or []
        raise StageInputMissingError(
            f"Gemini QA verdict for {artifact} is {verdict or 'MISSING'}: {issues}"
        )

    _complete_stage(job_dir, stage_name, result.output_path)
    return result.output_path


def _enforce_seo_language_qa(
    job_dir: Path,
    qa_output: Path,
    qa_payload: dict,
    *,
    channel_path: Path | None = None,
) -> None:
    """Force SEO rework if Gemini misses the configured language contract."""
    seo_path = job_dir / "seo.json"
    if not seo_path.exists():
        return
    try:
        seo_payload = json.loads(seo_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return

    if channel_path is None:
        try:
            state = load_job(job_dir)
        except Exception:
            # If job.json is missing or corrupted we cannot know which channel this
            # belongs to. Skip the enforcement layer rather than aborting the QA
            # promotion entirely; the artifact has already been written to disk.
            return
        channel_config_path = repo_root() / "configs" / state.channel_id / "channel.yaml"
    else:
        channel_config_path = channel_path
    channel_config = read_yaml(channel_config_path) if channel_config_path.exists() else {}
    expected_language = (
        (channel_config.get("seo") or {}).get("language")
        or (channel_config.get("audience") or {}).get("language")
        or "es-ES"
    )
    actual_language = seo_payload.get("language")
    if actual_language == expected_language:
        return

    issue = (
        f"SEO language must be exactly {expected_language}; got {actual_language}. "
        "ChatGPT must regenerate the SEO artifact with the configured language."
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
        current_channel_fit = int(scores.get("channel_fit") or 5)
    except (TypeError, ValueError):
        current_channel_fit = 5
    scores["channel_fit"] = min(current_channel_fit, 3)
    updated["scores"] = scores
    issues = list(updated.get("issues") or [])
    if issue not in issues:
        issues.append(issue)
    updated["issues"] = issues
    required = (
        f"Set seo.language to exactly {expected_language} and use "
        f"{expected_language} consistently in SEO text."
    )
    changes = list(updated.get("required_changes") or [])
    if required not in changes:
        changes.append(required)
    updated["required_changes"] = changes
    _write_json(qa_output, updated)


def _enforce_scenes_visual_prompt_english(
    job_dir: Path,
    qa_output: Path,
    qa_payload: dict,
) -> None:
    """Force scenes rework if any visual_prompt is Spanish.

    Pexels stock search is English-keyword based, so Spanish visual_prompts
    produce off-topic backgrounds (Bellagio fountains for sleep scenes, etc.).
    This QA layer flips the verdict to NEEDS_REWORK with a per-scene list of
    offending visual_prompts so ChatGPT regenerates them in English.
    """
    scenes_path = job_dir / "scenes.json"
    if not scenes_path.exists():
        return
    try:
        scenes_payload = json.loads(scenes_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return

    offenders: list[tuple[str, str]] = []  # (scene_id, reason)
    for scene in scenes_payload.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        prompt = str(scene.get("visual_prompt") or "")
        is_spanish, reason = _looks_like_spanish_visual_prompt(prompt)
        if is_spanish:
            offenders.append((str(scene.get("id", "?")), reason or "Spanish detected"))

    if not offenders:
        return

    summary = ", ".join(f"{sid} ({reason})" for sid, reason in offenders[:5])
    if len(offenders) > 5:
        summary += f", and {len(offenders) - 5} more"
    issue = (
        f"{len(offenders)} scene visual_prompt fields are Spanish. "
        "Pexels stock search is English-keyword based; Spanish prompts produce "
        f"off-topic backgrounds. Offenders: {summary}."
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
        current_clarity = int(scores.get("clarity") or 5)
    except (TypeError, ValueError):
        current_clarity = 5
    scores["clarity"] = min(current_clarity, 3)
    updated["scores"] = scores
    issues = list(updated.get("issues") or [])
    if issue not in issues:
        issues.append(issue)
    updated["issues"] = issues
    required = (
        "Rewrite every visual_prompt in ENGLISH for Pexels stock search. "
        "Format: person + setting + action + lighting + camera framing. "
        "Example: 'Mature woman in her 50s drinking herbal tea on a sofa "
        "at night, warm tungsten light, medium shot'. "
        f"Scenes to fix: {', '.join(sid for sid, _ in offenders)}."
    )
    changes = list(updated.get("required_changes") or [])
    if required not in changes:
        changes.append(required)
    updated["required_changes"] = changes
    _write_json(qa_output, updated)


# ---------------------------------------------------------------------------
# Auto-driven stages: orchestrator -> browser-worker -> ChatGPT.
# ---------------------------------------------------------------------------

PromptFn = Callable[[str], Awaitable[str]]
"""Async callable: takes a prompt string, returns the raw model response."""

SessionFn = Callable[[Sequence[str]], Awaitable[str]]
"""Async callable: takes a list of messages to send in one temp chat,
returns the last assistant response."""


async def _auto_run_then_promote(
    *,
    job_dir: Path,
    channel_path: Path,
    prompt_path: Path,
    runner: Callable[[Path, Path], Path],
    promoter: Callable[[Path, Path, str], Path],
    session_fn: SessionFn,
    run_stage_name: str,
    promote_stage_name: str,
    briefing_stage_name: str,
) -> Path:
    """Generic auto-stage: render prompt, send task into the session, promote.

    The caller (typically /run-all) is responsible for opening the
    persistent temp chat and sending the initial briefing **once** at
    the start. This stage helper only builds and sends the
    stage-specific task message; the model already has the channel DNA
    in its context.

    If ``session_fn`` is the legacy one-shot ``run_session``, the
    standalone /stages/X/auto routes wrap it so that the initial
    briefing is sent as the first message of the same one-shot tab.
    """
    state = load_job(job_dir)
    if state.current_stage == run_stage_name:
        runner(job_dir, channel_path)
        state = load_job(job_dir)
    if state.current_stage != promote_stage_name:
        raise StageInputMissingError(
            f"Cannot auto-run {run_stage_name}/{promote_stage_name} from "
            f"current_stage={state.current_stage!r}"
        )

    if not prompt_path.exists():
        raise StageInputMissingError(f"Missing prompt file {prompt_path}")
    prompt_text = prompt_path.read_text(encoding="utf-8")

    from video_agent.orchestrator.briefing import build_task_prompt

    task = build_task_prompt(
        briefing_stage_name,
        prompt_text,
        channel_config=read_yaml(channel_path),
    )

    raw_response = await session_fn([task])
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise StageInputMissingError(
            "browser-worker returned an empty response for "
            f"{promote_stage_name}"
        )

    # Continuation loop: if the model truncated mid-JSON, send "Continúa"
    # and append until the JSON parses or we give up (max 4 continuations).
    from video_agent.operator import extract_json_objects

    _CONTINUE_MSG = (
        "Continúa exactamente desde donde te quedaste, "
        "sin repetir nada de lo anterior."
    )
    _max_continuations = 4
    for _attempt in range(_max_continuations):
        if extract_json_objects(raw_response):
            break  # valid JSON extracted — proceed to promote
        # Check if there's any JSON start; if not, don't bother continuing
        if "{" not in raw_response:
            break
        # Looks truncated — ask the model to continue
        continuation = await session_fn([_CONTINUE_MSG])
        if not isinstance(continuation, str) or not continuation.strip():
            break
        raw_response = raw_response + continuation
    else:
        pass  # exhausted continuations — let promoter decide

    try:
        return promoter(job_dir, channel_path, raw_response)
    except ValueError as exc:
        raise StageInputMissingError(
            f"Promotion failed for {promote_stage_name}: {exc}"
        ) from exc


async def auto_script_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    return await _auto_run_then_promote(
        job_dir=job_dir,
        channel_path=channel_path,
        prompt_path=job_dir / SCRIPT_PROMPT_PATH,
        runner=run_script_stage,
        promoter=promote_script_stage,
        session_fn=session_fn,
        run_stage_name="script",
        promote_stage_name="script_promote",
        briefing_stage_name="script",
    )


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
    for attempt in range(max_attempts):
        raw = await session_fn([current_prompt])
        if not isinstance(raw, str) or not raw.strip():
            last_error = StageInputMissingError(
                f"Empty model response for {expected_artifact_type}"
            )
            current_prompt = (
                "Tu respuesta anterior fue vacía. Devuelve UN SOLO objeto JSON "
                f"con artifact_type='{expected_artifact_type}', job_id='{expected_job_id}', "
                f"channel_id='{expected_channel_id}', y la sección data{{...}}.\n\n"
                + prompt
            )
            continue
        try:
            envelope = extract_json_envelope(raw)
            validate_envelope(
                envelope,
                expected_artifact_type=expected_artifact_type,
                expected_job_id=expected_job_id,
                expected_channel_id=expected_channel_id,
            )
            return envelope
        except Exception as exc:
            last_error = exc
            last_preview = (raw or "")[:400].replace("\n", " ")
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
    raise StageInputMissingError(
        f"{expected_artifact_type} failed validation after {max_attempts} attempts: "
        f"{last_error}. Last preview: {last_preview!r}"
    )


def _scene_id_to_batch_index(batch_envelopes: list[dict]) -> dict[str, int]:
    scene_to_batch: dict[str, int] = {}
    for env in batch_envelopes:
        batch_index = int(((env.get("data") or {}).get("batch_index") or env.get("batch_index") or 0))
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
                    env_index = int(((env.get("data") or {}).get("batch_index") or env.get("batch_index") or 0))
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
    script_path = job_dir / "script.json"
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
            validate_scenes_plan(plan_envelope)
            save_envelope(cached_plan_path, plan_envelope)

        batches = (plan_envelope.get("data") or {}).get("batches") or []
        if not isinstance(batches, list) or not batches:
            raise ShardValidationError("scenes_plan returned no batches")

        batch_envelopes: list[dict] = []
        batch_total = len(batches)
        # On resume, replay any already-saved batch envelopes from disk so
        # the loop continues from the first un-saved batch instead of
        # re-querying ChatGPT for batches we already have.
        existing_batches: dict[int, dict] = {}
        if resume_after_scenes:
            for f in sorted((job_dir / SCENES_BATCHES_DIR).glob("scenes_batch_*.json")):
                try:
                    env = json.loads(f.read_text(encoding="utf-8"))
                    idx = int(((env.get("data") or {}).get("batch_index") or env.get("batch_index") or 0))
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
            batch_prompt = _chatgpt_scenes_batch_prompt(
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
            validate_scenes_batch(
                batch_envelope,
                expected_batch_index=batch_index,
                expected_batch_total=batch_total,
                scene_start=scene_start,
                scene_end=scene_end,
            )
            batch_path = job_dir / SCENES_BATCHES_DIR / f"scenes_batch_{batch_index:02d}.json"
            save_envelope(batch_path, batch_envelope)
            batch_envelopes.append(batch_envelope)
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
        scenes_path = job_dir / "scenes.json"
        _write_json(scenes_path, merged)
    except Exception as exc:
        raise StageInputMissingError(str(exc)) from exc

    _complete_stage(job_dir, "scenes_promote", scenes_path)
    return scenes_path


async def auto_scenes_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    if os.environ.get("SCENES_SHARDED_GENERATION", "").strip() == "1":
        return await auto_scenes_stage_sharded(job_dir, channel_path, session_fn)
    return await _auto_run_then_promote(
        job_dir=job_dir,
        channel_path=channel_path,
        prompt_path=job_dir / SCENES_PROMPT_PATH,
        runner=run_scenes_stage,
        promoter=promote_scenes_stage,
        session_fn=session_fn,
        run_stage_name="scenes",
        promote_stage_name="scenes_promote",
        briefing_stage_name="scenes",
    )


async def auto_seo_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    return await _auto_run_then_promote(
        job_dir=job_dir,
        channel_path=channel_path,
        prompt_path=job_dir / SEO_PROMPT_PATH,
        runner=run_seo_stage,
        promoter=promote_seo_stage,
        session_fn=session_fn,
        run_stage_name="seo",
        promote_stage_name="seo_promote",
        briefing_stage_name="seo",
    )


# ---------------------------------------------------------------------------
# Auto Gemini QA stages: orchestrator -> browser-worker -> Gemini.
# ---------------------------------------------------------------------------


async def _auto_qa(
    *,
    job_dir: Path,
    channel_path: Path,
    artifact: str,
    session_fn: SessionFn,
) -> Path:
    stage_name = f"{artifact}_qa"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot auto-run {stage_name} from current_stage={state.current_stage!r}"
        )
    artifact_path = job_dir / _QA_ARTIFACT_FILE[artifact]
    if not artifact_path.exists():
        raise StageInputMissingError(f"Missing {artifact_path}")

    artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    channel_config = read_yaml(channel_path)
    base_prompt = _gemini_qa_prompt(artifact, artifact_payload, channel_config)

    from video_agent.orchestrator.briefing import build_task_prompt

    task = build_task_prompt(stage_name, base_prompt, channel_config=channel_config)

    raw_response = await session_fn([task])
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise StageInputMissingError(
            f"browser-worker returned an empty Gemini QA response for {artifact}"
        )
    return promote_qa_stage(job_dir, artifact, raw_response, channel_path=channel_path)


async def auto_script_qa_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    return await _auto_qa(
        job_dir=job_dir,
        channel_path=channel_path,
        artifact="script",
        session_fn=session_fn,
    )


async def auto_scenes_qa_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    if os.environ.get("SCENES_SHARDED_GENERATION", "").strip() == "1":
        return await auto_scenes_qa_stage_sharded(job_dir, channel_path, session_fn)
    return await _auto_qa(
        job_dir=job_dir,
        channel_path=channel_path,
        artifact="scenes",
        session_fn=session_fn,
    )


async def auto_scenes_qa_stage_sharded(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    state = load_job(job_dir)
    if state.current_stage != "scenes_qa":
        raise StageInputMissingError(
            f"Cannot auto-run sharded scenes_qa from current_stage={state.current_stage!r}"
        )
    scenes_path = job_dir / "scenes.json"
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

    batch_size = 8
    scene_batches = [
        scenes[index:index + batch_size]
        for index in range(0, len(scenes), batch_size)
    ]
    qa_envelopes: list[dict] = []
    batch_total = len(scene_batches)
    scenes_logger = EventLogger(job_dir / EVENT_LOG)
    existing_batches: dict[int, dict] = {}
    batch_dir = job_dir / SCENES_QA_BATCHES_DIR
    scenes_mtime = scenes_path.stat().st_mtime
    for f in sorted(batch_dir.glob("scenes_qa_batch_*.json")):
        try:
            if f.stat().st_mtime < scenes_mtime:
                continue
            env = json.loads(f.read_text(encoding="utf-8"))
            validate_envelope(
                env,
                expected_artifact_type="scenes_qa_batch",
                expected_job_id=state.job_id,
                expected_channel_id=state.channel_id,
            )
            idx = int(env.get("batch_index") or 0)
            if idx:
                existing_batches[idx] = env
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
            batch_doc = {
                "channel_id": state.channel_id,
                "job_id": state.job_id,
                "batch_index": batch_index,
                "batch_total": batch_total,
                "scenes": batch_scenes,
            }
            if batch_index in existing_batches:
                qa_envelopes.append(existing_batches[batch_index])
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
            scenes_logger.log(
                "SCENES_QA_PROGRESS",
                {
                    "job_id": state.job_id,
                    "step": "batch_started",
                    "batch_index": batch_index,
                    "batches_total": batch_total,
                    "batches_done": len(qa_envelopes),
                },
            )
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
                expected_job_id=state.job_id,
                expected_channel_id=state.channel_id,
            )
            batch_path = job_dir / SCENES_QA_BATCHES_DIR / f"scenes_qa_batch_{batch_index:02d}.json"
            save_envelope(batch_path, envelope)
            qa_envelopes.append(envelope)
            scenes_logger.log(
                "SCENES_QA_PROGRESS",
                {
                    "job_id": state.job_id,
                    "step": "batch_saved",
                    "batch_index": batch_index,
                    "batches_total": batch_total,
                    "batches_done": len(qa_envelopes),
                },
            )

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


async def auto_seo_qa_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    return await _auto_qa(
        job_dir=job_dir,
        channel_path=channel_path,
        artifact="seo",
        session_fn=session_fn,
    )


# ---------------------------------------------------------------------------
# idea_research stage: record topic keywords before script.
# ---------------------------------------------------------------------------

RESEARCH_FILE = "research.json"


def _idea_keywords(idea: dict) -> list[str]:
    """Build 3-5 keyword variants from idea.topic + title_seed."""
    base = idea.get("topic", "").strip()
    seed = idea.get("title_seed", "").strip()
    keywords = []
    if base:
        keywords.append(base)
    if seed and seed.lower() != base.lower():
        keywords.append(seed)
    # short variant (first 6 words)
    words = base.split()
    if len(words) > 4:
        short = " ".join(words[:5])
        if short not in keywords:
            keywords.append(short)
    return keywords[:5]


async def auto_idea_research_stage(
    job_dir: Path,
    channel_path: Path,
) -> Path:
    """Record topic keyword variants and advance the research stage."""
    stage_name = "idea_research"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} from current_stage={state.current_stage!r}"
        )
    idea_path = job_dir / IDEA_FILE
    if not idea_path.exists():
        raise StageInputMissingError(f"Missing {idea_path}")

    idea = json.loads(idea_path.read_text(encoding="utf-8"))
    keywords = _idea_keywords(idea)
    if not keywords:
        raise StageInputMissingError("idea.json has no topic or title_seed for research")

    research = {
        "keywords": keywords,
        "verdict": "pass",
    }
    output_path = job_dir / RESEARCH_FILE
    _write_json(output_path, research)

    EventLogger(job_dir / EVENT_LOG).log(
        "IDEA_RESEARCH_COMPLETE",
        {"job_id": state.job_id, "keywords": keywords, "verdict": "pass"},
    )

    _complete_stage(job_dir, stage_name, output_path)
    return output_path


# ---------------------------------------------------------------------------
# Per-scene image generation via ChatGPT projects.
# ---------------------------------------------------------------------------


_ASSET_GEN_PROMPT_PREFIX = (
    "Photorealistic, 16:9 cinematic, soft natural light, no text overlay, "
    "no watermark, no logos. Audience: adultos 45+. Scene visual: "
)


def _scene_project_name(job_id: str, scene_id: str) -> str:
    return f"{job_id[:35]}-{scene_id}"[:45]


def _build_thumbnail_prompt(
    title: str,
    thumbnail_text: str,
    accent_color: str,
    channel_description: str,
) -> str:
    """Build a ChatGPT image prompt for a FULL composite thumbnail.

    The generated image will contain both the photorealistic background
    AND the bold text hook baked directly into the image, so background
    and typography are visually coherent from the start.
    """
    return (
        f"Create a complete YouTube thumbnail image — ultra-high-resolution, "
        f"crystal-clear photorealistic, 1920x1080 (16:9 aspect ratio), 4K quality, "
        f"tack-sharp focus, professional DSLR photograph (85mm portrait lens look, "
        f"shot at f/2.8, ISO 100), no motion blur, no compression artifacts, "
        f"no JPEG haze — every pixel crisp. "
        f"Topic: '{title}'. "
        f"Channel context: {channel_description}. "
        f"\n\n"
        f"SUBJECT: A Hispanic or Latina woman aged 45-55 years old. "
        f"She is positioned in the LEFT half of the frame, face clearly visible, "
        f"tack-sharp focus on the eyes (skin pores, eyelashes, fine hair strands "
        f"all distinctly visible), looking slightly toward the right (center of image). "
        f"Her expression is emotional and expressive — conveying concern, relief, or urgency "
        f"that matches the hook text '{thumbnail_text}'. "
        f"Skin tone natural, color-graded for warm cinematic look, no plastic / "
        f"airbrushed / AI-smoothed appearance. "
        f"\n\n"
        f"PAIN-ANGLE ALIGNMENT: The background and subject must visually express the "
        f"same pain angle as the title, not a generic wellness portrait. If the title "
        f"mentions a plate taking energy after 45, include visual cues like plate, energy, fatigue, "
        f"uncertainty, or a simple meal decision. "
        f"\n\n"
        f"BACKGROUND: Simple, warm-toned, professional studio lighting (key + fill + rim), "
        f"shallow depth of field bokeh (creamy, smooth — not noisy). High dynamic range, "
        f"natural skin tones, no banding, no posterization. "
        f"\n\n"
        f"TEXT OVERLAY — render this EXACTLY in the image: \"{thumbnail_text}\". "
        f"Placement: right half of the image, vertically centered or lower-right area. "
        f"Style: extremely bold, ALL-CAPS, very large font (occupying ~40% of image width), "
        f"white color. MAXIMUM CONTRAST — the text must read instantly against ANY "
        f"background: apply a thick black stroke/outline (5-6px, fully opaque, clean even "
        f"width on every letter) PLUS a heavy dark drop shadow (offset, large soft blur, "
        f"high opacity) so the type detaches completely from the scene behind it. If the "
        f"background area behind the text is busy or light, add a subtle semi-transparent "
        f"dark gradient/vignette directly behind the text block to guarantee separation — "
        f"keep it understated, never a hard box. Letterforms must be razor-sharp, no jagged "
        f"edges, no anti-alias fuzz — render at the highest typographic fidelity. "
        f"Font style similar to Impact, Anton, or Bebas Neue — punchy and attention-grabbing. "
        f"Accent color for a thin decorative underline or glow beneath the text: {accent_color}. "
        f"\n\n"
        f"PROP OBJECT: Include ONE real, photorealistic physical object that instantly "
        f"signals the topic at a glance — a tangible prop the woman holds or that sits "
        f"naturally in the scene (e.g. for sleep/night themes a real alarm clock or a cup "
        f"of tea on a nightstand; for nutrition themes an actual plate of food; for energy "
        f"themes a glass of water or supplement). The object must be a genuine, detailed, "
        f"in-context photograph — NOT a flat icon, illustration, sticker, or emoji — lit and "
        f"focused to match the scene, clearly recognizable. Place it so it supports the hook "
        f"without covering the face or the text. Exactly ONE prop — keep the composition "
        f"clean; the prop should clarify the theme, not clutter it. "
        f"\n\n"
        f"QUALITY KEYWORDS: 4K, UHD, ultra-sharp, photorealistic, hyper-detailed, "
        f"high-resolution, professional photography, magazine cover quality, "
        f"award-winning portrait. NEGATIVE — explicitly avoid: blurry, soft focus, "
        f"out-of-focus subject, low-resolution, pixelated, noisy, grainy, JPEG artifacts, "
        f"oil-painting look, illustration, cartoon, plastic-skin, over-smoothed, "
        f"AI-generated artifacts, distorted faces, extra fingers, warped anatomy. "
        f"\n\n"
        f"RULES: No additional text, captions, watermarks, or UI elements. "
        f"Only the subject, background, and the exact hook text \"{thumbnail_text}\". "
        f"The final result must look like a polished professional YouTube thumbnail "
        f"that holds up when zoomed in to 200%."
    )


async def generate_scene_asset(
    job_dir: Path,
    channel_path: Path,
    scene_id: str,
    image_fn,
) -> dict:
    """Generate a ChatGPT image for ``scene_id`` and update scenes.json.

    Looks up the scene in ``scenes.json`` by id, builds an image prompt
    from its ``visual_prompt`` (plus a brand-consistent style prefix),
    calls ``image_fn(prompt, project_name, out_path)`` (typically
    ``BrowserClient.generate_image``), saves the bytes under
    ``jobs/<id>/assets/<scene_id>.png``, and patches the scene's
    ``asset_refs.primary`` to the relative path so the v2 render
    stage picks it up.

    Returns the image_fn payload plus the scene id.
    """
    scenes_path = job_dir / "scenes.json"
    if not scenes_path.exists():
        raise StageInputMissingError(f"Missing {scenes_path}")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", scene_id or ""):
        raise StageInputMissingError(f"Invalid scene_id: {scene_id!r}")
    scenes_doc = json.loads(scenes_path.read_text(encoding="utf-8"))
    target = None
    for s in scenes_doc.get("scenes", []):
        if s.get("id") == scene_id:
            target = s
            break
    if target is None:
        raise StageInputMissingError(
            f"Scene {scene_id!r} not found in {scenes_path}"
        )
    visual_prompt = target.get("visual_prompt") or target.get("caption") or ""
    if not visual_prompt:
        raise StageInputMissingError(
            f"Scene {scene_id} has no visual_prompt to feed image gen."
        )

    state = load_job(job_dir)
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    out_path = assets_dir / f"{scene_id}.png"
    project_name = _scene_project_name(state.job_id, scene_id)
    prompt = _ASSET_GEN_PROMPT_PREFIX + visual_prompt

    result = await image_fn(
        prompt=prompt,
        project_name=project_name,
        out_path=str(out_path),
    )

    # Update scenes.json -> asset_refs.primary with the job-relative path.
    rel = str(out_path.relative_to(job_dir))
    refs = target.get("asset_refs")
    if not isinstance(refs, dict):
        refs = {}
    refs["primary"] = rel
    refs["primary_source"] = "chatgpt_image"
    refs["primary_url"] = result.get("src", "")
    refs["primary_project"] = project_name
    target["asset_refs"] = refs
    _write_json(scenes_path, scenes_doc)

    EventLogger(job_dir / EVENT_LOG).log(
        "SCENE_ASSET_GENERATED",
        {
            "job_id": state.job_id,
            "scene_id": scene_id,
            "local_path": rel,
            "bytes": result.get("bytes"),
        },
    )
    return {"scene_id": scene_id, **result, "asset_refs_primary": rel}


# ---------------------------------------------------------------------------
# Batch image generation stage: assets_chatgpt.
# ---------------------------------------------------------------------------


async def auto_assets_chatgpt_stage(
    job_dir: Path,
    channel_path: Path,
    image_fn,
    *,
    throttle_sec: float = 8.0,
) -> Path:
    """Generate ChatGPT images for every scene, with per-scene throttle + fallback.

    Iterates all scenes in scenes.json and calls ``generate_scene_asset()``
    for each. A failed scene logs a ``SCENE_ASSET_FAILED`` event and
    continues — the render stage falls back to stock/placeholder for that
    scene so the pipeline never halts on a single image failure.

    Returns ``scenes.json`` (updated in-place with ``asset_refs.primary``
    for each successfully generated scene).
    """
    stage_name = "assets_chatgpt"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} from current_stage={state.current_stage!r}"
        )

    scenes_path = job_dir / "scenes.json"
    if not scenes_path.exists():
        raise StageInputMissingError(f"Missing {scenes_path}")

    scenes_doc = json.loads(scenes_path.read_text(encoding="utf-8"))
    logger = EventLogger(job_dir / EVENT_LOG)
    scene_ids: list[str] = []
    for s in scenes_doc.get("scenes", []):
        sid = s.get("id")
        if sid:
            scene_ids.append(sid)
        else:
            logger.log(
                "SCENE_ASSET_FAILED",
                {"job_id": state.job_id, "scene_id": None, "error": "scene missing id"},
            )

    for idx, scene_id in enumerate(scene_ids):
        if idx > 0:
            await asyncio.sleep(throttle_sec)
        try:
            await generate_scene_asset(job_dir, channel_path, scene_id, image_fn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.log(
                "SCENE_ASSET_FAILED",
                {"job_id": state.job_id, "scene_id": scene_id, "error": str(exc)},
            )

    _complete_stage(job_dir, stage_name, scenes_path)
    return scenes_path


# ---------------------------------------------------------------------------
# Thumbnail background image generation stage.
# ---------------------------------------------------------------------------


async def auto_thumbnail_image_stage(
    job_dir: Path,
    channel_path: Path,
    image_fn,
    *,
    throttle_sec: float = 8.0,
) -> Path:
    """Generate full-composite thumbnails (background + text baked in) via ChatGPT.

    Generates one JPEG per title_variant (up to 3) so each has its own
    visually coherent hook text. Outputs:
      jobs/<id>/thumbnail_1.jpg  ← variant 1 (primary)
      jobs/<id>/thumbnail_2.jpg  ← variant 2
      jobs/<id>/thumbnail_3.jpg  ← variant 3
      jobs/<id>/thumbnail.jpg    ← alias of thumbnail_1.jpg (backward compat)

    The render stage detects these files and skips the Remotion still step.
    """
    import shutil as _shutil
    from PIL import Image as _PilImage

    stage_name = "thumbnail_image"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} from current_stage={state.current_stage!r}"
        )

    seo_path = job_dir / "seo.json"
    if not seo_path.exists():
        raise StageInputMissingError(f"Missing {seo_path}")
    seo = json.loads(seo_path.read_text(encoding="utf-8"))

    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")
    channel_config = read_yaml(channel_path)

    title = seo.get("title") or ""
    palette = (channel_config.get("style") or {}).get("palette") or {}
    accent_color = palette.get("accent", "#F2C94C")
    channel_description = (
        (channel_config.get("channel") or {}).get("description", "Wellness channel for adults 45+")
    )

    # Build variant list: up to 3 title_variants, fallback to top-level thumbnail_text.
    raw_variants = seo.get("title_variants") or []
    variants: list[str] = [
        v.get("thumbnail_text") or ""
        for v in raw_variants[:3]
        if v.get("thumbnail_text")
    ]
    if not variants:
        fallback = seo.get("thumbnail_text") or title.split(" ")[:5]
        variants = [fallback if isinstance(fallback, str) else " ".join(fallback)]

    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    logger = EventLogger(job_dir / EVENT_LOG)
    generated: list[Path] = []   # successfully created .jpg files
    errors: list[str] = []
    last_exc: Exception | None = None

    has_batch = hasattr(image_fn, "generate_images")
    if has_batch:
        prompts = []
        png_paths = []
        jpg_paths = []
        project_name = f"{state.job_id[:30]}-thumbnails"[:45]
        for i, thumb_text in enumerate(variants, start=1):
            prompt = _build_thumbnail_prompt(title, thumb_text, accent_color, channel_description)
            prompts.append(prompt)
            png_paths.append((assets_dir / f"thumbnail_{i}.png").resolve())
            jpg_paths.append((job_dir / f"thumbnail_{i}.jpg").resolve())

        try:
            await image_fn.generate_images(
                prompts=prompts,
                project_name=project_name,
                out_paths=[str(p) for p in png_paths],
            )
            for i, (png_path, jpg_path, thumb_text) in enumerate(zip(png_paths, jpg_paths, variants), start=1):
                if png_path.exists():
                    img = _PilImage.open(png_path).convert("RGB")
                    img.save(jpg_path, "JPEG", quality=92, optimize=True)
                    png_path.unlink(missing_ok=True)  # remove intermediate PNG
                    generated.append(jpg_path)
                    logger.log(
                        "THUMBNAIL_IMAGE_GENERATED",
                        {"job_id": state.job_id, "variant": i, "path": str(jpg_path), "text": thumb_text},
                    )
                else:
                    errors.append(f"variant {i} ('{thumb_text}'): Output image file not found.")
                    logger.log(
                        "THUMBNAIL_IMAGE_FAILED",
                        {"job_id": state.job_id, "variant": i, "error": "Output image file missing"},
                    )
        except Exception as exc:
            last_exc = exc
            errors.append(f"Batch generation failed: {exc}")
            logger.log(
                "THUMBNAIL_IMAGE_BATCH_FAILED",
                {"job_id": state.job_id, "error": str(exc)},
            )
    else:
        for i, thumb_text in enumerate(variants, start=1):
            if i > 1:
                await asyncio.sleep(throttle_sec)

            prompt = _build_thumbnail_prompt(title, thumb_text, accent_color, channel_description)
            project_name = f"{state.job_id[:30]}-thumb{i}"[:45]
            png_path = (assets_dir / f"thumbnail_{i}.png").resolve()
            jpg_path = (job_dir / f"thumbnail_{i}.jpg").resolve()

            try:
                response = await image_fn(
                    prompt=prompt,
                    project_name=project_name,
                    out_path=str(png_path),
                )

                # Convert PNG → JPG (Pillow — already a project dependency)
                source_path = png_path
                if not source_path.exists() and isinstance(response, dict):
                    returned_path = response.get("local_path")
                    if returned_path:
                        source_path = Path(str(returned_path)).expanduser()
                if not source_path.exists():
                    raise FileNotFoundError(f"Generated image file not found: {png_path}")
                img = _PilImage.open(source_path).convert("RGB")
                img.save(jpg_path, "JPEG", quality=92, optimize=True)
                source_path.unlink(missing_ok=True)  # remove intermediate PNG

                generated.append(jpg_path)
                logger.log(
                    "THUMBNAIL_IMAGE_GENERATED",
                    {"job_id": state.job_id, "variant": i, "path": str(jpg_path), "text": thumb_text},
                )
            except Exception as exc:
                last_exc = exc
                errors.append(f"variant {i} ('{thumb_text}'): {exc}")
                logger.log(
                    "THUMBNAIL_IMAGE_FAILED",
                    {"job_id": state.job_id, "variant": i, "error": str(exc)},
                )

    if not generated:
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(
            "All thumbnail variants failed: " + "; ".join(errors)
        )

    # thumbnail.jpg = alias of the FIRST successfully generated variant.
    # Uses generated[0] (not hardcoded thumbnail_1.jpg) so that if variant 1
    # failed but variant 2+ succeeded, thumbnail.jpg is still populated.
    primary = generated[0]
    _shutil.copy2(primary, job_dir / "thumbnail.jpg")

    # Copy all generated thumbnails to remotion/public/ so Remotion Studio
    # and the Thumbnail.tsx preview component can load them via staticFile().
    public_job_dir = prepare_public_job_dir(repo_root(), job_dir.name)
    for jpg in generated:
        _shutil.copy2(jpg, public_job_dir / jpg.name)
    _shutil.copy2(primary, public_job_dir / "thumbnail.jpg")

    # seo.thumbnail_path: use public-relative path of the primary thumbnail.
    # staticFile()-compatible so Remotion Studio can load it in Thumbnail.tsx.
    public_ref = f"jobs/{job_dir.name}/{primary.name}"
    seo["thumbnail_path"] = public_ref
    _write_json(seo_path, seo)

    if errors:
        logger.log(
            "THUMBNAIL_IMAGE_PARTIAL",
            {"job_id": state.job_id, "generated": len(generated), "errors": errors},
        )

    _complete_stage(job_dir, stage_name, seo_path)
    return seo_path


# ---------------------------------------------------------------------------
# Rework loop: QA NEEDS_REWORK -> feed issues back to ChatGPT -> re-promote.
# ---------------------------------------------------------------------------


_QA_STAGE_FN = {
    "script": auto_script_qa_stage,
    "scenes": auto_scenes_qa_stage,
    "seo": auto_seo_qa_stage,
}

_ARTIFACT_PROMOTER = {
    "script": promote_script_stage,
    "scenes": promote_scenes_stage,
    "seo": promote_seo_stage,
}

_ARTIFACT_RAW_PATH = {
    "script": SCRIPT_RAW_PATH,
    "scenes": SCENES_RAW_PATH,
    "seo": SEO_RAW_PATH,
}


def _reset_promote_and_qa(job_dir: Path, artifact: str) -> None:
    """Reset ``<artifact>_promote`` and ``<artifact>_qa`` to pending."""
    promote_name = f"{artifact}_promote"
    qa_name = f"{artifact}_qa"
    state = load_job(job_dir)
    for s in state.stages:
        if s.name in (promote_name, qa_name):
            s.status = "pending"
            s.started_at = None
            s.completed_at = None
            s.error = None
    state.current_stage = promote_name
    state.updated_at = _now()
    save_job(job_dir, state)


async def auto_rework_artifact(
    artifact: str,
    job_dir: Path,
    channel_path: Path,
    chatgpt_fn: SessionFn,
    validation_feedback: str | None = None,
) -> Path:
    """Send QA issues back to ChatGPT and re-promote the artifact.

    Reads ``operator/gemini/<artifact>_qa.json`` to extract issues and
    required_changes, resets the ``<artifact>_promote`` + ``<artifact>_qa``
    stages to pending, sends a rework message into the persistent
    ChatGPT tab, and re-runs the promoter with the new response.
    """
    qa_path = job_dir / "operator" / "gemini" / f"{artifact}_qa.json"
    if not qa_path.exists():
        raise StageInputMissingError(
            f"Missing {qa_path}; cannot rework {artifact}"
        )
    qa_payload = json.loads(qa_path.read_text(encoding="utf-8"))
    issues = qa_payload.get("issues") or []
    required_changes = qa_payload.get("required_changes") or []
    channel_config = read_yaml(channel_path)
    expected_language = (
        (channel_config.get("seo") or {}).get("language")
        or (channel_config.get("audience") or {}).get("language")
        or "es-ES"
    )

    _reset_promote_and_qa(job_dir, artifact)

    issue_lines = "\n".join(f"- {i}" for i in issues) or "- (sin issues listadas)"
    change_lines = (
        "\n".join(f"- {c}" for c in required_changes)
        or "- (sin required_changes listadas)"
    )
    validation_block = ""
    if validation_feedback and validation_feedback.strip():
        validation_block = (
            "\n\n## Error de validación del intento anterior\n"
            f"{validation_feedback.strip()}\n"
            "Ese error es obligatorio de corregir antes de devolver el JSON."
        )
    artifact_rules = ""
    if artifact == "scenes":
        artifact_rules = (
            "\n\n## Reglas obligatorias para scenes\n"
            "- narration, caption y on_screen_text deben mantener el idioma del canal.\n"
            "- visual_prompt debe estar en inglés, preferiblemente ASCII, porque se usa para búsqueda en Pexels.\n"
            "- No uses palabras españolas dentro de visual_prompt."
        )
    rework_msg = (
        f"# Rework del artefacto `{artifact}`\n"
        f"Tu artefacto anterior recibió verdict NEEDS_REWORK del revisor "
        f"(Gemini). Reescribe el artefacto JSON corrigiendo SOLO los puntos "
        f"a continuación. Mantén el mismo esquema, idioma {expected_language}, job_id y "
        f"channel_id.\n\n"
        f"## Issues detectadas\n{issue_lines}\n\n"
        f"## Cambios requeridos\n{change_lines}\n\n"
        f"{validation_block}"
        f"{artifact_rules}\n\n"
        f"Devuelve UN SOLO objeto JSON válido del artefacto `{artifact}` "
        f"completo (no solo el diff). Sin markdown ni comentarios."
    )

    new_raw = await chatgpt_fn([rework_msg])
    if not isinstance(new_raw, str) or not new_raw.strip():
        raise StageInputMissingError(
            f"browser-worker returned an empty rework response for {artifact}"
        )

    promoter = _ARTIFACT_PROMOTER[artifact]
    return promoter(job_dir, channel_path, new_raw)


def _max_retries_per_qa(channel_path: Path, default: int = 3) -> int:
    try:
        cfg = read_yaml(channel_path)
        return int(
            cfg.get("qa_rules", {}).get("thresholds", {}).get(
                "max_retry_per_qa", default
            )
        )
    except Exception:
        return default


async def auto_qa_with_rework(
    artifact: str,
    job_dir: Path,
    channel_path: Path,
    chatgpt_fn: SessionFn,
    qa_session_fn: SessionFn,
) -> Path:
    """Run ``<artifact>_qa``; if NEEDS_REWORK, rework via ChatGPT and retry.

    Honours ``channel.yaml -> qa_rules.thresholds.max_retry_per_qa``
    (default 3). After all retries are exhausted, re-raises the last
    ``StageInputMissingError`` so /run-all halts cleanly with the
    failed QA's issues in the response detail.
    """
    qa_fn = _QA_STAGE_FN[artifact]
    max_retries = _max_retries_per_qa(channel_path)
    last_exc: StageInputMissingError | None = None
    for attempt in range(max_retries + 1):
        try:
            state = load_job(job_dir)
            promote_stage = f"{artifact}_promote"
            if state.current_stage == promote_stage:
                raw_path = job_dir / _ARTIFACT_RAW_PATH[artifact]
                if not raw_path.exists():
                    raise StageInputMissingError(
                        f"Cannot promote {artifact}; missing raw response {raw_path}"
                    )
                _ARTIFACT_PROMOTER[artifact](
                    job_dir,
                    channel_path,
                    raw_path.read_text(encoding="utf-8"),
                )
            return await qa_fn(job_dir, channel_path, qa_session_fn)
        except StageInputMissingError as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            # Only attempt rework if qa.json exists (QA ran but verdict=NEEDS_REWORK).
            # If qa.json is missing, the QA response itself was empty/invalid — skip
            # rework and retry qa_fn directly on the next iteration.
            qa_path = job_dir / "operator" / "gemini" / f"{artifact}_qa.json"
            if qa_path.exists():
                try:
                    await auto_rework_artifact(
                        artifact,
                        job_dir,
                        channel_path,
                        chatgpt_fn,
                        validation_feedback=str(exc),
                    )
                except StageInputMissingError:
                    # Rework failed (e.g. qa.json vanished) — retry qa_fn directly.
                    pass
    assert last_exc is not None
    raise last_exc
