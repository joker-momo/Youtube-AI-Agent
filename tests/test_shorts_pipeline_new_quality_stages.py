from __future__ import annotations

import json
from pathlib import Path


def _long_job(tmp_path: Path) -> Path:
    job = tmp_path / "long-job"
    job.mkdir()
    (job / "scenes.json").write_text(json.dumps({"scenes": [{"id": "scene-01", "narration": "Mira la etiqueta del pan.", "duration_sec": 20.0}], "total_duration_sec": 600}), encoding="utf-8")
    (job / "script.json").write_text(json.dumps({"hook": "h", "sections": [], "narration": "n", "cta": "c"}), encoding="utf-8")
    (job / "seo.json").write_text(json.dumps({"title": "Pan integral después de los 45"}), encoding="utf-8")
    (job / "whisper_timestamps.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")
    (job / "video.mp4").write_bytes(b"x")
    return job


def _cfg() -> dict:
    return {
        "channel": {"id": "vida-plena-45"},
        "shorts": {
            "autopilot": {"max_regeneration_attempts": 1},
            "duration": {"min_sec": 20, "target_max_sec": 60},
            "tts": {"provider": "kokoro", "voice_id": "ef_dora", "speed": 1.07},
            "funnel": {"default_cta_without_url": "Vídeo completo en el canal.", "cta_max_words": 8},
        },
    }


def _plan(short_id: str = "short-01") -> dict:
    return {
        "short_id": short_id,
        "source_long_job_id": "long-job",
        "format": "pain_to_tip",
        "hook_angle": "No todo pan oscuro es integral.",
        "viewer_pain": "elegir pan por color",
        "practical_payoff": "mirar ingredientes y fibra",
        "music_track": "shorts_sleep_stress",
        "source_scene_ids": ["scene-01"],
        "narration_seed": "Mira la etiqueta del pan antes de comprar.",
        "funnel": {"cta": "Guárdalo para comprar."},
    }


def _script(short_id: str = "short-01", *, generic: bool = False) -> dict:
    narration = (
        "Es importante recordar consejos saludables para mantener hábitos saludables de forma equilibrada."
        if generic
        else "No todo pan oscuro es integral. Gira la bolsa and mira el primer ingrediente. Luego busca fibra por 100 gramos. Sin culpa: después de los 45, decidir rápido ayuda."
    )
    return {
        "short_id": short_id,
        "source_long_job_id": "long-job",
        "short_format": "pain_to_tip",
        "target_duration_sec": 28,
        "hook": "Recuerda estos consejos saludables." if generic else "No todo pan oscuro es integral.",
        "narration": narration,
        "beats": ["hook", "proof", "payoff", "cta", "outro"],
        "cta": "Guárdalo para comprar.",
        "micro_tension_lines": ["No basta con el color.", "Pero la etiqueta sí te da la pista."],
        "comment_trigger": "¿También mirabas solo el color?",
        "qa": {"verdict": "PENDING_SHORTS_QA"},
    }


def _scenes(short_id: str = "short-01") -> dict:
    return {
        "channel_id": "vida-plena-45",
        "short_id": short_id,
        "total_duration_sec": 21.0,
        "scenes": [
            {"id": "s1", "duration_sec": 2.5, "on_screen_text": "PAN OSCURO", "caption": "c", "layout": "short_hook", "visual_prompt": "vertical bread package label", "narration": "Pan oscuro no basta."},
            {"id": "s2", "duration_sec": 4.2, "on_screen_text": "INGREDIENTES", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical supermarket bread label", "narration": "Mira el primer ingrediente."},
            {"id": "s3", "duration_sec": 4.2, "on_screen_text": "FIBRA", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical hands reading nutrition label", "narration": "Busca fibra por 100 gramos."},
            {"id": "s4", "duration_sec": 4.2, "on_screen_text": "SIN CULPA", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical warm kitchen adult 45+", "narration": "Sin culpa, decide rápido."},
            {"id": "s5", "duration_sec": 3.5, "on_screen_text": "COMPARA", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical comparing two breads", "narration": "Compara dos panes."},
            {"id": "s6", "duration_sec": 2.4, "on_screen_text": "GUÁRDALO", "caption": "c", "layout": "short_cta", "visual_prompt": "vertical warm close up", "narration": "Guárdalo para comprar."},
        ],
        "qa": {"verdict": "PENDING_SHORTS_QA"},
    }


def _generic_scenes(short_id: str = "short-01") -> dict:
    """Scenes whose narration covers the generic (AI-slop) script, so the
    deterministic source-fidelity coverage guard (bug-503) passes and the pipeline
    reaches the anti_ai_review stage that this generic content is meant to fail."""
    return {
        "channel_id": "vida-plena-45",
        "short_id": short_id,
        "total_duration_sec": 21.0,
        "scenes": [
            {"id": "s1", "duration_sec": 2.5, "on_screen_text": "CONSEJOS", "caption": "c", "layout": "short_hook", "visual_prompt": "vertical generic wellness", "narration": "Es importante recordar consejos saludables."},
            {"id": "s2", "duration_sec": 4.2, "on_screen_text": "HÁBITOS", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical generic wellness", "narration": "Para mantener hábitos saludables."},
            {"id": "s3", "duration_sec": 4.2, "on_screen_text": "EQUILIBRIO", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical generic wellness", "narration": "De forma equilibrada cada día."},
            {"id": "s4", "duration_sec": 4.2, "on_screen_text": "BIENESTAR", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical generic wellness", "narration": "Cuida tu bienestar general."},
            {"id": "s5", "duration_sec": 3.5, "on_screen_text": "SIGUE", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical generic wellness", "narration": "Sigue estos consejos siempre."},
            {"id": "s6", "duration_sec": 2.4, "on_screen_text": "GUÁRDALO", "caption": "c", "layout": "short_cta", "visual_prompt": "vertical close", "narration": "Guárdalo para comprar."},
        ],
        "qa": {"verdict": "PENDING_SHORTS_QA"},
    }


def _llm(script_sequence: list[dict] | None = None, scenes_sequence: list[dict] | None = None):
    scripts = list(script_sequence or [_script()])
    scenes = list(scenes_sequence or [_scenes()])

    def fn(kind: str, prompt: str) -> str:
        if kind == "script":
            return json.dumps(scripts.pop(0) if len(scripts) > 1 else scripts[0])
        if kind == "scenes":
            return json.dumps(scenes.pop(0) if len(scenes) > 1 else scenes[0])
        if kind == "seo":
            return json.dumps({"title": "Pan integral después de los 45", "description": "Mira la etiqueta.", "hashtags": ["#alimentacionsaludable", "#shorts"], "pinned_comment": "¿También mirabas solo el color?"})
        return "{}"

    return fn


def _stub_io(calls: list[str]) -> dict:
    def background_fn(short_dir, short_scenes, channel_config, on_scene_resolved=None):
        calls.append("background")
        scenes = short_scenes.get("scenes") or []
        for i, sc in enumerate(scenes):
            sc.setdefault("asset_refs", {})["background"] = f"jobs/x/assets/{sc['id']}.mp4"
            if on_scene_resolved:
                on_scene_resolved({"index": i, "total": len(scenes), "scene_id": sc["id"], "phase": "resolved", "background_source": "Pexels video"})

    def tts_fn(short_dir, short_scenes, channel_config):
        calls.append("tts")
        (short_dir / "audio").mkdir(parents=True, exist_ok=True)
        out = short_dir / "audio" / "short_narration.wav"
        out.write_bytes(b"w")
        return out

    def mix_fn(short_dir, narration_wav, music_track, channel_config, duration_sec):
        calls.append("mix")
        out = short_dir / "audio" / "short_mix.m4a"
        out.write_bytes(b"m")
        return out

    def render_fn(short_dir, channel_config, stop_request_path=None):
        calls.append("render")
        (short_dir / "outputs").mkdir(parents=True, exist_ok=True)
        out = short_dir / "outputs" / "short.mp4"
        out.write_bytes(b"v")
        return out

    return {"background_fn": background_fn, "tts_fn": tts_fn, "mix_fn": mix_fn, "render_fn": render_fn}


def test_build_short_writes_quality_artifacts_and_background_stage(tmp_path: Path):
    from video_agent.shorts import paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []

    status = short_builder.build_short(
        job,
        _plan(),
        _cfg(),
        llm_fn=_llm(),
        **_stub_io(calls),
    )

    stage_names = [stage["name"] for stage in status["stages"]]
    for expected in ("retention_plan", "spoken_humanization", "visual_rhythm_plan", "anti_ai_review", "background", "performance_memory"):
        assert expected in stage_names
    # Thumbnail stage was removed from the Shorts pipeline.
    assert "thumbnail" not in stage_names

    jd = paths.short_json_dir(job, "short-01")
    for filename in (
        paths.SHORT_RETENTION_PLAN_FILE,
        paths.SHORT_HUMANIZATION_FILE,
        paths.SHORT_VISUAL_RHYTHM_FILE,
        paths.SHORT_ANTI_AI_REVIEW_FILE,
        paths.SHORT_PERFORMANCE_MEMORY_FILE,
    ):
        assert (jd / filename).exists()

    bg_stage = next(stage for stage in status["stages"] if stage["name"] == "background")
    assert bg_stage["status"] == "completed"
    assert [s["scene_id"] for s in bg_stage["per_scene"]]  # per-scene source report present
    assert "background" in calls


def test_anti_ai_fail_blocks_render_and_updates_performance_memory(tmp_path: Path):
    from video_agent.shorts import paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []

    status = short_builder.build_short(
        job,
        _plan("short-fail"),
        _cfg(),
        llm_fn=_llm(script_sequence=[_script("short-fail", generic=True), {**_script("short-fail", generic=True), "hook": "Different generic hook."}], scenes_sequence=[_generic_scenes("short-fail"), _generic_scenes("short-fail")]),
        **_stub_io(calls),
    )

    assert status["status"] in {"failed", "needs_review"}
    assert status["failure_stage"] == "anti_ai_review"
    assert "render" not in calls
    memory = json.loads((paths.short_json_dir(job, "short-fail") / paths.SHORT_PERFORMANCE_MEMORY_FILE).read_text(encoding="utf-8"))
    assert memory["status"] == "failed"
    assert memory["failure_stage"] == "anti_ai_review"


def test_anti_ai_fail_retries_once_then_renders_on_warn_or_pass(tmp_path: Path):
    from video_agent.shorts import short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []

    status = short_builder.build_short(
        job,
        _plan("short-retry"),
        _cfg(),
        llm_fn=_llm(script_sequence=[_script("short-retry", generic=True), _script("short-retry", generic=False)], scenes_sequence=[_scenes("short-retry"), _scenes("short-retry")]),
        **_stub_io(calls),
    )

    assert status["status"] == "rendered"
    assert status["anti_ai_regeneration_attempts"] == 1
    assert calls.count("render") == 1
