from __future__ import annotations

import os
from pathlib import Path

from video_agent.contracts import ARTIFACT_SCENES, EVENT_LOG
from video_agent.orchestrator.job_state import load_job
from video_agent.orchestrator.stages._shared import (
    _AUDIO_SUBPROCESS_ENV,
    StageInputMissingError,
    _complete_stage,
    _resolve_artifact,
    _run_blocking_with_timeout,
    _start_stage,
    dag_mode,
)
from video_agent.runtime.providers import SubprocessAudioTaskProvider
from video_agent.utils.json_io import read_json, read_yaml
from video_agent.utils.logging import EventLogger

__all__ = [
    "_run_audio_subprocess",
    "run_whisper_timestamps_stage",
    "_rebase_words_to_scene_timestamps",
    "_run_whisper_timestamps_stage_inline",
]


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
    if not dag_mode() and state.current_stage != "whisper_timestamps":
        raise StageInputMissingError(
            f"Cannot run whisper_timestamps stage from current_stage={state.current_stage!r}"
        )
    # bug-469: this stage never called _start_stage, so started_at was always
    # left to _complete_stage's cross-stage fallback (guess = the nearest
    # EARLIER stage's completed_at). That guess is fine when stages run
    # strictly sequentially, but graphic_images/thumbnail_image/whisper_timestamps/
    # visual_schedule are dispatched concurrently by the DAG scheduler -- if the
    # much-slower graphic_images/thumbnail_image stages hadn't finished yet
    # (still completed_at=None) by the time whisper_timestamps completed, the
    # fallback walked past them to a genuinely stale, unrelated stage's
    # completed_at (observed: seo_qa's timestamp from a PREVIOUS day's run),
    # making a ~2min transcription look like it took ~17 hours on the dashboard.
    _start_stage(job_dir, "whisper_timestamps")
    if os.environ.get(_AUDIO_SUBPROCESS_ENV) != "1":
        from video_agent.orchestrator import stages as stages_pkg

        return stages_pkg._run_audio_subprocess("whisper-timestamps", job_dir)
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
            # late facade import to respect monkeypatch on stages.repo_root
            from video_agent.orchestrator import stages as stages_pkg
            channel_config_path = stages_pkg.repo_root() / "configs/vida-plena-45/channel.yaml"

        if not channel_config_path.exists():
            raise StageInputMissingError(
                f"Missing narration audio: {narration_path} and cannot auto-synthesize because channel config was not found."
            )

        channel_config = read_yaml(channel_config_path)
        # late facade import to respect monkeypatch on stages.repo_root
        from video_agent.orchestrator import stages as stages_pkg
        style = read_json(stages_pkg.repo_root() / channel_config["style_dna"]["path"])
        scenes_path = _resolve_artifact(job_dir, ARTIFACT_SCENES)
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

    scenes_path = _resolve_artifact(job_dir, ARTIFACT_SCENES)
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
    output_path = job_dir / "json/whisper_timestamps.json"
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
        # Early pass only — scenes.json may still carry PLANNED durations here;
        # the render path re-runs this with the FINAL audio-fit timeline
        # (bug-531). Writes through update_seo_fields, never a full snapshot.
        from video_agent.operator import resync_seo_chapters

        new_chapters = resync_seo_chapters(job_dir)
        if new_chapters:
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
