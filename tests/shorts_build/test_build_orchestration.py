from __future__ import annotations

from .conftest import *  # noqa: F401,F403

def test_short_stage_retry_clears_stale_completion_and_error():
    from video_agent.shorts.short_builder import _update_short_stage

    status = {
        "stages": [{
            "name": "audio",
            "label": "Audio TTS & Mix",
            "status": "failed",
            "started_at": "2026-06-06T20:53:33+00:00",
            "completed_at": "2026-06-06T20:54:19+00:00",
            "actual_seconds": 45,
            "error": "Narration audio exceeds video duration.",
            "qa_verdict": "FAIL",
        }]
    }

    _update_short_stage(status, "audio", "in_progress", now_str="2026-06-06T20:58:27+00:00")

    stage = status["stages"][0]
    assert stage["status"] == "in_progress"
    assert stage["completed_at"] is None
    assert stage["actual_seconds"] is None
    assert "error" not in stage
    assert "qa_verdict" not in stage


def test_build_short_pass_renders_and_writes_artifacts(tmp_path: Path):
    from video_agent.shorts import short_builder, paths
    job = _long_job(tmp_path)
    calls: list[str] = []
    plan = {"short_id": "short-01", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    res = short_builder.build_short(job, plan, _cfg(), llm_fn=_llm_fn_factory(), **_stub_io(calls))
    assert res["status"] == "rendered"
    assert res["qa_verdict"] == "PASS"
    sd = paths.short_dir(job, "short-01")
    for f in ("short_script.json", "short_scenes.json", "short_source_map.json", "short_seo.json", "short_script_qa.json", "short_scenes_qa.json"):
        assert (sd / "json" / f).exists(), f
    for f in ("short.mp4", "short_cover.jpg"):
        assert (sd / "outputs" / f).exists(), f
    assert calls == ["background", "tts", "mix", "render", "cover"]
    assert res["music_track"] == "shorts_sleep_stress"


def test_build_short_records_stage_pass_fail_status_in_prompt_history(tmp_path: Path):
    from video_agent.shorts import llm_history, paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    plan = {"short_id": "short-history-stages", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}

    short_builder.build_short(job, plan, _cfg(), llm_fn=_llm_fn_factory(), **_stub_io(calls))

    hist_path = paths.short_dir(job, "short-history-stages") / "json" / paths.SHORT_LLM_HISTORY_FILE
    stage_events = [
        h for h in llm_history.read_history(hist_path)
        if h.get("provider") == "deterministic" and h.get("kind") == "stage_status"
    ]
    assert any(e["payload"]["stage"] == "script" and e["payload"]["status"] == "completed" for e in stage_events)
    assert any(e["payload"]["stage"] == "qa_script" and e["payload"]["verdict"] == "PASS" for e in stage_events)
    assert any(e["payload"]["stage"] == "audio" and e["payload"]["status"] == "completed" for e in stage_events)


def test_short_render_props_use_scene_sum_when_total_duration_is_stale(tmp_path: Path):
    from video_agent.shorts import paths, short_builder

    short_dir = tmp_path / "short"
    scenes = [
        {"id": "s01", "duration_sec": 2.5},
        {"id": "s02", "duration_sec": 3.5},
        {"id": "s03", "duration_sec": 3.5},
        {"id": "s04", "duration_sec": 3.5},
        {"id": "s05", "duration_sec": 3.5},
        {"id": "s06", "duration_sec": 3.5},
        {"id": "s07", "duration_sec": 5.0},
        {"id": "s08", "duration_sec": 2.5},
    ]
    assert round(sum(float(scene["duration_sec"]) for scene in scenes), 1) == 27.5

    short_builder._write_render_props(
        short_dir,
        {"total_duration_sec": 21.7, "scenes": scenes},
        _cfg(),
        "shorts_sleep_stress",
    )

    props = json.loads((short_dir / "json" / paths.SHORT_RENDER_PROPS_FILE).read_text(encoding="utf-8"))
    assert props["total_duration_sec"] == 27.5
    assert props["render"]["duration_sec"] == 27.5


def test_build_short_keeps_audio_and_video_in_sync_at_planned_durations(tmp_path: Path):
    # Shorts TTS runs with dynamic_sync=False (see shorts.audio): each scene's
    # audio is padded to its planned duration_sec, so the single Remotion
    # narration track stays aligned with the per-scene visual sequences. The
    # builder must therefore KEEP the planned scene durations and drive
    # render/mix at the planned total — never shrink visuals to raw speech,
    # which previously desynced audio from video.
    import wave

    from video_agent.shorts import llm_history, paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    mix_durations: list[float] = []
    plan = {"short_id": "short-preserve-duration", "format": "mistake_list", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Cinco errores con el pan."}
    scenes_doc = {
        "channel_id": "vida-plena-45",
        "short_id": "short-preserve-duration",
        "total_duration_sec": 27.6,
        "scenes": [
            {"id": "s01", "duration_sec": 2.2, "on_screen_text": "NO ES EL PAN", "caption": "SON 5 HÁBITOS", "layout": "short_hook", "visual_prompt": "Realistic bread on Spanish kitchen table", "narration": "No es el pan."},
            {"id": "s02", "duration_sec": 3.6, "on_screen_text": "DE PIE", "caption": "Sin plato", "layout": "short_pain", "visual_prompt": "Realistic person eating bread standing in kitchen", "narration": "Uno: comerlo de pie."},
            {"id": "s03", "duration_sec": 3.6, "on_screen_text": "SUMAR SIN DECIDIR", "caption": "Con arroz o pasta", "layout": "graphic_comparison", "visual_prompt": "Graphic comparison card: bread alone vs bread added to rice or pasta", "narration": "Dos: sumarlo sin decidir.", "layout_payload": {"title": "DECIDE PRIMERO", "left": {"heading": "MEJOR", "text": "Elige una porción"}, "right": {"heading": "CUIDADO", "text": "Pan encima de arroz"}}},
            {"id": "s04", "duration_sec": 3.6, "on_screen_text": "BARRA A LA VISTA", "caption": "Demasiado a mano", "layout": "short_pain", "visual_prompt": "Realistic bread bar left on dining table", "narration": "Tres: dejar la barra a la vista."},
            {"id": "s05", "duration_sec": 3.6, "on_screen_text": "CANSANCIO", "caption": "Otro trozo", "layout": "short_pain", "visual_prompt": "Realistic tired adult cutting another bread slice", "narration": "Cuatro: cortar por cansancio."},
            {"id": "s06", "duration_sec": 3.6, "on_screen_text": "CENA IMPROVISADA", "caption": "A bocados", "layout": "short_pain", "visual_prompt": "Realistic bread and cheese dinner bites on plate", "narration": "Cinco: cenar improvisando."},
            {"id": "s07", "duration_sec": 4.8, "on_screen_text": "MEJOR ASÍ", "caption": "Porción visible", "layout": "graphic_checklist", "visual_prompt": "Graphic checklist card: visible bread portion, small plate, complete meal", "narration": "Mejor: porción visible, plato pequeño, comida completa.", "layout_payload": {"title": "MEJOR ASÍ", "items": ["Porción visible", "Plato pequeño", "Comida completa"]}},
            {"id": "s08", "duration_sec": 2.6, "on_screen_text": "GUÁRDALO", "caption": "PARA TU PRÓXIMA CENA", "layout": "short_cta", "visual_prompt": "Realistic warm kitchen close-up", "narration": "Guárdalo."},
        ],
        "qa": {"verdict": "PENDING_SCENES_QA"},
    }

    def tts_fn(short_dir, short_scenes, channel_config):
        # Faithful dynamic_sync=False stand-in: pad each scene's audio to its
        # planned duration, leave scene durations untouched, and emit a
        # narration track whose length equals the planned total (27.6s).
        calls.append("tts")
        planned_total = round(
            sum(float(s["duration_sec"]) for s in short_scenes["scenes"]), 2
        )
        audio_dir = short_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        wav_path = audio_dir / "short_narration.wav"
        with wave.open(str(wav_path), "w") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\0\0" * int(planned_total * 8000))
        return wav_path

    io = _stub_io(calls)
    io["tts_fn"] = tts_fn

    def mix_fn(short_dir, narration_wav, music_track, channel_config, duration_sec):
        calls.append("mix")
        mix_durations.append(duration_sec)
        (short_dir / "audio").mkdir(parents=True, exist_ok=True)
        out = short_dir / "audio" / "short_mix.m4a"
        out.write_bytes(b"m")
        return out

    io["mix_fn"] = mix_fn

    res = short_builder.build_short(
        job,
        plan,
        _cfg(),
        llm_fn=_llm_fn_factory(scenes=scenes_doc),
        gemini_fn=lambda p: json.dumps({
            "verdict": "PASS",
            "issues": [],
            "required_changes": [],
            "warnings": [],
            "product_scores": {
                "audience_fit_45_plus": 10,
                "hook_strength": 10,
                "visual_specificity": 10,
                "clarity": 10,
                "retention_pacing": 9,
                "natural_spanish": 10,
                "saveability": 10,
            },
        }),
        **io,
    )

    short_dir = paths.short_dir(job, "short-preserve-duration")
    saved_scenes = json.loads((short_dir / "json" / paths.SHORT_SCENES_FILE).read_text(encoding="utf-8"))
    render_props = json.loads((short_dir / "json" / paths.SHORT_RENDER_PROPS_FILE).read_text(encoding="utf-8"))
    hist = llm_history.read_history(short_dir / "json" / paths.SHORT_LLM_HISTORY_FILE)
    audio_tail_events = [h for h in hist if h.get("kind") == "audio_tail_repair"]

    assert res["status"] == "rendered"
    # Sync invariant: the visual timeline, the render duration, the mix duration
    # and the narration audio all agree. This is what keeps audio aligned with
    # video; the exact number depends on the planned scene durations.
    visual_total = round(sum(float(scene["duration_sec"]) for scene in saved_scenes["scenes"]), 1)
    assert saved_scenes["total_duration_sec"] == visual_total
    assert render_props["render"]["duration_sec"] == visual_total
    assert mix_durations == [visual_total]
    # Narration audio fills essentially the whole visual timeline. Before the
    # fix, audio (19.4s) ran ~11s short of the 30.2s visuals — gross desync.
    # Now the gap is only the intentional end-of-video tail hold (<= ~1s).
    with wave.open(str(short_dir / "audio" / "short_narration.wav")) as w:
        narration_sec = round(w.getnframes() / w.getframerate(), 1)
    assert narration_sec <= visual_total
    assert visual_total - narration_sec <= 1.0

    # Fix E: a measurable audio_sync_summary is written and passes.
    sync = json.loads((short_dir / "json" / paths.SHORT_AUDIO_SYNC_SUMMARY_FILE).read_text(encoding="utf-8"))
    assert sync["verdict"] == "PASS"
    assert sync["pass_delta_sec"] > 0

    # Fix C: a reason-aware call_budget_summary is written on success.
    budget = json.loads((short_dir / "json" / paths.SHORT_CALL_BUDGET_SUMMARY_FILE).read_text(encoding="utf-8"))
    assert budget["stage"] == "call_budget_summary"
    assert "by_reason" in budget and "provider_error" in budget["by_reason"]
    assert "by_provider" in budget and "retry_counts" in budget
    assert budget["verdict"] in ("PASS", "WARN")
    # v4 §2.2: the stage must also appear in the LLM history/log.
    from video_agent.shorts import llm_history as _llm_hist
    hist = _llm_hist.read_history(short_dir / "json" / paths.SHORT_LLM_HISTORY_FILE)
    assert any((h.get("kind") or h.get("event")) == "call_budget_summary" for h in hist)


def test_build_short_soft_scene_validation_warning_proceeds_to_gemini_qa(tmp_path: Path, monkeypatch):
    from video_agent.shorts import short_builder, validate_scenes

    job = _long_job(tmp_path)
    calls: list[str] = []
    qa_calls = {"n": 0}

    def soft_scene_validation(*args, **kwargs):
        return [
            validate_scenes.SceneValidationIssue(
                type="slideshow_risk",
                scene_id=None,
                severity="warning",
                detail="Footage-led candidate has mild list density.",
            )
        ]

    def gemini_fn(prompt):
        if "Scenes QA reviewer" in prompt:
            qa_calls["n"] += 1
        return json.dumps({
            "verdict": "PASS",
            "issues": [],
            "required_changes": [],
            "warnings": [],
            "product_scores": {
                "hook_strength": 8,
                "retention_pacing": 8,
                "visual_scene_fit": 8,
                "mobile_readability": 8,
                "layout_variety": 8,
                "source_fidelity": 8,
                "overall_product_quality": 8,
            },
        })

    monkeypatch.setattr(validate_scenes, "validate_scene_structure", soft_scene_validation)

    plan = {"short_id": "short-soft-scene-warning", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    res = short_builder.build_short(
        job,
        plan,
        _cfg(),
        llm_fn=_llm_fn_factory(scenes={**_GOOD_SCENES, "short_id": "short-soft-scene-warning"}),
        gemini_fn=gemini_fn,
        **_stub_io(calls),
    )

    assert qa_calls["n"] >= 1
    assert res["status"] in {"rendered", "needs_review"}


def test_build_short_records_render_exception_in_status(tmp_path: Path):
    from video_agent.shorts import short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []

    def render_fn(short_dir, channel_config):
        calls.append("render")
        raise RuntimeError("render schema validation failed")

    io = _stub_io(calls)
    io["render_fn"] = render_fn
    plan = {"short_id": "short-render-error", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}

    try:
        short_builder.build_short(job, plan, _cfg(), llm_fn=_llm_fn_factory(), **io)
    except RuntimeError:
        pass

    status_doc = json.loads((job / "shorts" / "short-render-error" / "short_status.json").read_text())
    render_stage = next(s for s in status_doc["stages"] if s["name"] == "render")
    assert status_doc["status"] == "failed"
    assert "render schema validation failed" in render_stage["error"]
    assert "render schema validation failed" in status_doc["error"]


def test_build_short_persists_auto_extended_scene_durations_before_gemini_qa(tmp_path: Path):
    from video_agent.shorts import paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    captured: dict[str, str] = {}
    scenes = {
        **_GOOD_SCENES,
        "short_id": "short-auto-duration",
        "total_duration_sec": 20.3,
        "scenes": [
            *_GOOD_SCENES["scenes"][:1],
            {
                **_GOOD_SCENES["scenes"][1],
                "duration_sec": 2.0,
                "narration": "Marca una hora de cierre.",
            },
            *_GOOD_SCENES["scenes"][2:4],
            {**_GOOD_SCENES["scenes"][4], "duration_sec": 5.0},
            *_GOOD_SCENES["scenes"][5:],
        ],
    }

    def gemini_fn(prompt):
        captured["prompt"] = prompt
        return json.dumps({
            "verdict": "PASS",
            "issues": [],
            "required_changes": [],
            "product_scores": {
                "audience_fit_45_plus": 9,
                "hook_strength": 9,
                "visual_specificity": 9,
                "clarity": 9,
                "retention_pacing": 9,
                "natural_spanish": 9,
                "saveability": 8.5,
            },
        })

    plan = {"short_id": "short-auto-duration", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}

    res = short_builder.build_short(
        job,
        plan,
        _cfg(),
        llm_fn=_llm_fn_factory(scenes=scenes),
        gemini_fn=gemini_fn,
        **_stub_io(calls),
    )

    sd = paths.short_dir(job, "short-auto-duration")
    saved = json.loads((sd / "json" / paths.SHORT_SCENES_FILE).read_text(encoding="utf-8"))

    assert res["status"] == "rendered"
    assert saved["scenes"][1]["duration_sec"] == 2.7
    # v4 §4.3: short_tip hard max tightened 5.0 -> 4.5, so the tail repair can
    # absorb a little less room here (total 20.7 instead of the old 21.2).
    assert saved["total_duration_sec"] == 20.7
    assert '"duration_sec": 2.7' in captured["prompt"]
    assert '"total_duration_sec": 20.7' in captured["prompt"]


def test_build_short_regenerates_then_needs_review_after_limit(tmp_path: Path):
    from video_agent.shorts import short_builder, paths
    job = _long_job(tmp_path)
    calls: list[str] = []
    attempts = {"n": 0}
    bad_script = {**_GOOD_SCRIPT, "hook": "Hola a todos", "narration": "Hola, bienvenidos. Hoy vamos a hablar."}

    def llm_fn(kind, prompt):
        if kind == "script":
            attempts["n"] += 1
            # Vary hook slightly to avoid collapse protection
            return json.dumps({**bad_script, "hook": f"Hola a todos {attempts['n']}"})
        if kind == "scenes":
            return json.dumps(_GOOD_SCENES)
        return json.dumps({"title": "t", "description": "d", "hashtags": [], "pinned_comment": "p"})

    plan = {"short_id": "short-02", "format": "mistake_to_avoid", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress", "narration_seed": "x"}
    res = short_builder.build_short(job, plan, _cfg(), llm_fn=llm_fn, **_stub_io(calls))
    assert res["status"] == "needs_review"
    assert res["requires_user_review"] is True
    # initial + 2 regenerations = 3 generations
    assert attempts["n"] == 3
    assert "render" not in calls  # never rendered a failing short
    assert not (paths.short_dir(job, "short-02") / "short.mp4").exists()


def test_build_short_exposes_qa_scenes_attempt_count(tmp_path: Path):
    from video_agent.shorts import short_builder, paths

    job = _long_job(tmp_path)
    calls: list[str] = []
    qa_attempts = {"n": 0}
    scene_attempts = {"n": 0}

    def llm_fn(kind, prompt):
        if kind == "script":
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            scene_attempts["n"] += 1
            v_scenes = dict(_GOOD_SCENES)
            v_scenes["scenes"] = [dict(s) for s in _GOOD_SCENES["scenes"]]
            v_scenes["scenes"][0]["caption"] = f"c{scene_attempts['n']}"
            return json.dumps(v_scenes)
        if kind == "seo":
            return json.dumps({"title": "Dormir mejor 45+", "description": "d", "hashtags": ["#shorts"],
                               "pinned_comment": "Mira el vídeo largo"})
        return "{}"

    def gemini_fn(prompt: str, **kwargs):
        if "Scenes QA reviewer" in prompt:
            qa_attempts["n"] += 1
            if qa_attempts["n"] == 1:
                return json.dumps({
                    "verdict": "FAIL",
                    "issues": [{"type": "retention_pacing", "severity": "repairable_error", "detail": "Merge repeated final scenes."}],
                    "required_changes": ["Merge repeated final scenes."],
                    "warnings": [],
                    "product_scores": {
                        "hook_strength": 8,
                        "retention_pacing": 6,
                        "visual_scene_fit": 8,
                        "mobile_readability": 8,
                        "layout_variety": 8,
                        "source_fidelity": 8,
                        "overall_product_quality": 8,
                    },
                })
            return json.dumps({
                "verdict": "PASS",
                "issues": [],
                "required_changes": [],
                "warnings": [],
                "product_scores": {
                    "hook_strength": 8,
                    "retention_pacing": 8,
                    "visual_scene_fit": 8,
                    "mobile_readability": 8,
                    "layout_variety": 8,
                    "source_fidelity": 8,
                    "overall_product_quality": 8,
                },
            })
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []})

    plan = {"short_id": "short-qa-scenes-count", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}

    res = short_builder.build_short(
        job,
        plan,
        _cfg(),
        llm_fn=llm_fn,
        gemini_fn=gemini_fn,
        **_stub_io(calls),
    )

    status_doc = json.loads(
        (paths.short_dir(job, "short-qa-scenes-count") / paths.SHORT_STATUS_FILE).read_text(encoding="utf-8")
    )
    assert res["qa_scenes_attempts"] == qa_attempts["n"]
    assert status_doc["qa_scenes_attempts"] == qa_attempts["n"]


def test_gemini_scene_qa_fail_blocks_audio_seo_and_render(tmp_path: Path):
    from video_agent.shorts import paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []

    def llm_fn(kind: str, prompt: str):
        calls.append(kind)
        if kind == "script":
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            return json.dumps(_GOOD_SCENES)
        if kind == "seo":
            return json.dumps({"title": "Should not run", "description": "d", "hashtags": ["#shorts"]})
        return "{}"

    def gemini_fn(prompt: str):
        if "Scenes QA reviewer" not in prompt:
            return json.dumps({"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []})
        return json.dumps({
            "verdict": "FAIL",
            "issues": [
                {
                    "type": "visual",
                    "scene_id": "s2",
                    "severity": "major",
                    "detail": "Acceptable duration, but verify the visual is warm enough.",
                }
            ],
            "required_changes": ["Verify the visual is warm enough."],
            "warnings": [],
            "product_scores": {
                "audience_fit_45_plus": 5,
                "hook_strength": 5,
                "visual_specificity": 5,
                "clarity": 5,
                "retention_pacing": 5,
                "natural_spanish": 5,
                "saveability": 5,
            },
        })

    cfg = _cfg()
    cfg["shorts"]["autopilot"]["max_regeneration_attempts"] = 0
    plan = {
        "short_id": "short-qa-fail-gate",
        "format": "pain_to_tip",
        "scene_ids": ["scene-09"],
        "source_start_sec": 183.0,
        "source_end_sec": 199.0,
        "music_track": "shorts_sleep_stress",
        "narration_seed": "Marca una hora de cierre.",
    }

    res = short_builder.build_short(
        job,
        plan,
        cfg,
        llm_fn=llm_fn,
        gemini_fn=gemini_fn,
        **_stub_io(calls),
    )

    short_dir = paths.short_dir(job, "short-qa-fail-gate")
    qa_doc = json.loads(paths.resolve_short_json(short_dir, paths.SHORT_SCENES_QA_FILE).read_text(encoding="utf-8"))
    failure_doc = json.loads(paths.resolve_short_json(short_dir, paths.SHORT_FAILURE_REPORT_FILE).read_text(encoding="utf-8"))
    retry_memory = json.loads((short_dir / "json" / "scene_retry_memory.json").read_text(encoding="utf-8"))

    assert qa_doc["verdict"] == "FAIL"
    assert qa_doc["provider_call_ok"] is True
    assert qa_doc["qa_pass"] is False
    assert failure_doc["latest_scene_qa_ok"] is False
    assert res["status"] == "needs_review"
    assert res["qa_verdict"] == "FAIL"
    assert "tts" not in calls
    assert "seo" not in calls
    assert "render" not in calls
    assert not (short_dir / paths.SHORT_SEO_FILE).exists()
    assert not (short_dir / "outputs" / "short.mp4").exists()
    active_details = [
        str(issue.get("detail") or "")
        for issue in retry_memory["active_issues"].values()
    ]
    assert any("visual is warm enough" in detail for detail in active_details)
    assert any("Verify the visual is warm enough" in detail for detail in active_details)


def test_build_short_prepare_mode_stops_before_render(tmp_path: Path):
    from video_agent.shorts import short_builder, paths

    job = _long_job(tmp_path)
    calls: list[str] = []
    plan = {"short_id": "short-03", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}

    res = short_builder.build_short(
        job,
        plan,
        _cfg(),
        llm_fn=_llm_fn_factory(),
        require_render_confirmation=True,
        **_stub_io(calls),
    )

    assert res["status"] == "ready_for_render"
    assert res["rendered"] is False
    assert res["requires_render_confirmation"] is True
    assert calls == ["background", "tts"]




def test_build_short_passes_source_artifacts_to_script_builder(tmp_path: Path, monkeypatch):
    from video_agent.shorts import paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    captured: dict[str, object] = {}
    plan = {
        "short_id": "short-04",
        "format": "pain_to_tip",
        "scene_ids": ["scene-09"],
        "source_scene_ids": ["scene-09"],
        "idea_id": "idea-01",
        "narration_seed": "Marca una hora de cierre.",
        "music_track": "shorts_sleep_stress",
    }

    def fake_build_short_script(long_job_dir, short_plan, channel_config, llm_fn, **kwargs):
        captured["source_artifacts"] = kwargs.get("source_artifacts")
        return _GOOD_SCRIPT

    monkeypatch.setattr(short_builder.short_script_builder, "build_short_script", fake_build_short_script)
    monkeypatch.setattr(
        short_builder.qa,
        "run_short_script_qa",
        lambda *args, **kwargs: {"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []},
    )
    monkeypatch.setattr(
        short_builder.qa,
        "run_short_scenes_qa",
        lambda *args, **kwargs: {"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []},
    )

    res = short_builder.build_short(
        job,
        plan,
        _cfg(),
        llm_fn=_llm_fn_factory(),
        source_artifacts={"idea": {"idea_id": "idea-01"}, "source_scenes": [{"scene_id": "scene-09"}]},
        **_stub_io(calls),
    )

    assert res["status"] == "rendered"
    assert captured["source_artifacts"]["idea"]["idea_id"] == "idea-01"
    sd = paths.short_dir(job, "short-03")
    assert not (sd / "short.mp4").exists()
    assert not (sd / "short_cover.jpg").exists()


# --- scene normalization (render/TTS compatibility) ------------------------



def test_script_escalation_after_repeated_scene_failures(tmp_path: Path):
    from unittest.mock import patch
    from video_agent.shorts import short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    
    script_attempts = {"n": 0, "feedbacks": []}
    scene_calls = {"n": 0}
    
    def llm_fn(kind, prompt):
        if kind == "script":
            script_attempts["n"] += 1
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            scene_calls["n"] += 1
            # Body scene duration 1.0s, but narration has 15 words -> requires
            # ~7.0s (exceeds the layout cap). Keep the hook faithful so the
            # hook-restoration normalizer does not mask the fit failure under test.
            bad_scenes = {
                "channel_id": f"vida-plena-45-{scene_calls['n']}",
                "short_id": "short-escalate",
                "total_duration_sec": 5.5,
                "scenes": [
                    {"id": "s1", "duration_sec": 2.5, "layout": "short_hook", "narration": "¿Duermes pero te levantas cansado?", "on_screen_text": f"x{scene_calls['n']}", "caption": "c"},
                    {"id": "s2", "duration_sec": 1.0, "layout": "short_tip", "narration": "Abre fuerte y mira esta increible etiqueta ahora mismo con mucho cuidado y atencion.", "on_screen_text": "TIP", "caption": "c"},
                    {"id": "s3", "duration_sec": 2.0, "layout": "short_cta", "narration": "Vídeo completo.", "on_screen_text": "CANAL", "caption": "c"},
                ]
            }
            return json.dumps(bad_scenes)
        return "{}"

    original_build_script = short_builder.short_script_builder.build_short_script
    def captured_build_script(*args, **kwargs):
        script_attempts["feedbacks"].append(kwargs.get("feedback", ""))
        return original_build_script(*args, **kwargs)

    with patch("video_agent.shorts.short_script_builder.build_short_script", captured_build_script):
        io = _stub_io(calls)
        cfg = _cfg()
        cfg["shorts"]["autopilot"]["max_regeneration_attempts"] = 2

        plan = {"short_id": "short-escalate", "format": "pain_to_tip", "scene_ids": ["scene-09"], "music_track": "shorts_sleep_stress"}
        res = short_builder.build_short(
            job,
            plan,
            cfg,
            llm_fn=llm_fn,
            gemini_fn=lambda p: json.dumps({"verdict": "PASS", "issues": [], "required_changes": []}),
            **io,
        )

        assert script_attempts["n"] >= 2
        assert any("SCRIPT COMPRESSION REQUIRED" in f for f in script_attempts["feedbacks"])
        assert res["status"] == "needs_review"


def test_best_candidate_fallback_blocked_by_low_scores(tmp_path: Path):
    from video_agent.shorts import short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    
    def gemini_fn(prompt):
        return json.dumps({
            "verdict": "PASS",
            "issues": [],
            "required_changes": [],
            "product_scores": {
                "audience_fit_45_plus": 8,
                "hook_strength": 8,
                "visual_specificity": 8,
                "clarity": 5, # low score!
                "retention_pacing": 8,
                "natural_spanish": 8,
                "saveability": 8
            }
        })

    io = _stub_io(calls)
    cfg = _cfg()
    cfg["shorts"]["autopilot"]["max_regeneration_attempts"] = 0

    plan = {"short_id": "short-low-scores", "format": "pain_to_tip", "scene_ids": ["scene-09"], "music_track": "shorts_sleep_stress"}
    res = short_builder.build_short(
        job,
        plan,
        cfg,
        llm_fn=_llm_fn_factory(),
        gemini_fn=gemini_fn,
        **io,
    )

    assert res["status"] == "needs_review"
    assert res["qa_verdict"] == "FAIL"


def test_best_candidate_fallback_blocked_by_gemini_audio_fit_issue(tmp_path: Path):
    from video_agent.shorts import short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    gemini_calls = {"n": 0}

    def gemini_fn(prompt):
        gemini_calls["n"] += 1
        if gemini_calls["n"] == 1:
            return json.dumps({"verdict": "PASS", "issues": [], "required_changes": []})
        return json.dumps({
            "verdict": "FAIL",
            "issues": [{
                "type": "audio_fit_risk",
                "scene_id": None,
                "severity": "major",
                "detail": "Narration density creates an audio_fit risk before rendering.",
            }],
            "required_changes": ["Shorten narration before render; audio_fit risk is high."],
            "product_scores": {
                "audience_fit_45_plus": 8,
                "hook_strength": 8,
                "visual_specificity": 8,
                "clarity": 8,
                "retention_pacing": 6,
                "natural_spanish": 8,
                "saveability": 8,
            },
        })

    cfg = _cfg()
    cfg["shorts"]["autopilot"]["max_regeneration_attempts"] = 0

    plan = {"short_id": "short-audio-fit-risk", "format": "pain_to_tip", "scene_ids": ["scene-09"], "music_track": "shorts_sleep_stress"}
    res = short_builder.build_short(
        job,
        plan,
        cfg,
        llm_fn=_llm_fn_factory(),
        gemini_fn=gemini_fn,
        **_stub_io(calls),
    )

    assert res["status"] == "needs_review"
    assert res["qa_verdict"] == "FAIL"
    assert gemini_calls["n"] >= 2
    assert "render" not in calls


# --------------------------------------------------------------------------
# Regression: graphic-count false positive + pacing simplification repair
# --------------------------------------------------------------------------

def test_two_graphics_not_failed_for_at_most_2_rule():
    """A candidate with exactly 2 graphics must not fail QA when Gemini
    incorrectly complains about an "at most 2 graphics" rule."""
    from video_agent.shorts.qa import normalize_gemini_scenes_qa

    parsed = {
        "verdict": "FAIL",
        "issues": [{
            "type": "graphic_count",
            "scene_id": None,
            "severity": "major",
            "detail": "Scene already has 2 graphics; at most 2 graphics allowed, remove one.",
        }],
        "required_changes": ["Remove one graphic — at most 2 graphics allowed."],
        "warnings": [],
        "scores": {},
        "product_scores": {
            "audience_fit_45_plus": 9,
            "hook_strength": 9,
            "visual_specificity": 9,
            "clarity": 9,
            "retention_pacing": 9,
            "natural_spanish": 9,
            "saveability": 8.5
        }
    }

    res = normalize_gemini_scenes_qa(parsed, graphic_count=2, graphic_led=False)
    assert res["verdict"] == "PASS"
    assert not any(i.get("type") == "graphic_count" for i in res["issues"])
    assert not res["required_changes"]
    assert any("Downgraded graphic-count" in w for w in res["warnings"])

    # But a genuine over-cap (>=4 graphics) is still a real, blocking issue.
    res4 = normalize_gemini_scenes_qa(parsed, graphic_count=4, graphic_led=False)
    assert res4["verdict"] == "FAIL"
    assert any(i.get("type") == "graphic_count" for i in res4["issues"])


def test_two_graphics_not_failed_for_allowed_2_graphic_limit_phrase():
    from video_agent.shorts.qa import normalize_gemini_scenes_qa

    parsed = {
        "verdict": "FAIL",
        "issues": [{
            "type": "product_quality_score_low",
            "scene_id": None,
            "severity": "major",
            "detail": (
                "The scene flow exceeds the allowed 2 graphic scene limit "
                "(s04, s05 are graphics, but the structure creates unnecessary scene bloat)."
            ),
        }],
        "required_changes": ["Ensure only 2 scenes use graphic_* layout."],
        "warnings": [],
        "product_scores": {
            "audience_fit_45_plus": 9,
            "hook_strength": 9,
            "visual_specificity": 9,
            "clarity": 9,
            "retention_pacing": 9,
            "natural_spanish": 9,
            "saveability": 8.5
        }
    }

    res = normalize_gemini_scenes_qa(parsed, graphic_count=2, graphic_led=False)

    assert res["verdict"] == "PASS"
    assert not res["issues"]
    assert not res["required_changes"]
    assert any("Downgraded graphic-count" in w for w in res["warnings"])


def _nine_scene_doc():
    scenes = [
        {"id": "s1", "duration_sec": 2.5, "layout": "short_hook", "on_screen_text": "ELIGE BIEN", "caption": "c", "visual_prompt": "v vertical", "narration": "¿Eliges bien el pan?"},
        {"id": "s2", "duration_sec": 3.0, "layout": "short_tip", "on_screen_text": "REVISA ETIQUETA", "caption": "c", "visual_prompt": "v vertical", "narration": "Revisa la etiqueta primero."},
        {"id": "s3", "duration_sec": 3.0, "layout": "short_tip", "on_screen_text": "HARINA INTEGRAL", "caption": "c", "visual_prompt": "v vertical", "narration": "Busca harina integral."},
        {"id": "s4", "duration_sec": 3.0, "layout": "short_tip", "on_screen_text": "MIRA FIBRA", "caption": "c", "visual_prompt": "v vertical", "narration": "Mira la fibra."},
        {"id": "s5", "duration_sec": 3.0, "layout": "short_tip", "on_screen_text": "MENOS AZUCAR", "caption": "c", "visual_prompt": "v vertical", "narration": "Compara los azúcares."},
        {"id": "s6", "duration_sec": 3.0, "layout": "short_tip", "on_screen_text": "SIN ANADIDO", "caption": "c", "visual_prompt": "v vertical", "narration": "Evita azúcar añadido."},
        {"id": "s7", "duration_sec": 3.0, "layout": "short_tip", "on_screen_text": "LISTA CORTA", "caption": "c", "visual_prompt": "v vertical", "narration": "Lee la lista corta."},
        {"id": "s8", "duration_sec": 3.0, "layout": "short_tip", "on_screen_text": "PAN DENSO", "caption": "c", "visual_prompt": "v vertical", "narration": "Elige pan denso."},
        {"id": "s9", "duration_sec": 2.5, "layout": "short_cta", "on_screen_text": "GUARDA ESTA LISTA", "caption": "c", "visual_prompt": "v vertical", "narration": "Guarda esta lista."},
    ]
    return {
        "channel_id": "vida-plena-45", "short_id": "short-pacing",
        "total_duration_sec": 26.0, "scenes": scenes,
        "qa": {"verdict": "PENDING_SHORTS_QA"},
    }


def test_nine_scenes_soft_pacing_triggers_simplification_not_max_regen(tmp_path: Path):
    """A 9-scene candidate with retention_pacing=6 (soft) should be rescued by
    deterministic simplification rather than failing after max regenerations."""
    from video_agent.shorts import short_builder, paths

    job = _long_job(tmp_path)
    calls: list[str] = []

    script9 = {
        **_GOOD_SCRIPT,
        "short_id": "short-pacing",
        "narration": "Revisa esta lista para elegir mejor pan. Mira la etiqueta y la fibra.",
        "cta": "Guarda esta lista.",
    }

    def gemini_fn(prompt):
        # Scene QA: structurally fine, but pacing is a soft 6 -> product FAIL.
        return json.dumps({
            "verdict": "PASS",
            "issues": [],
            "required_changes": [],
            "product_scores": {
                "audience_fit_45_plus": 8,
                "hook_strength": 8,
                "visual_specificity": 8,
                "clarity": 8,
                "retention_pacing": 6,   # soft pacing
                "natural_spanish": 8,
                "saveability": 8,
            },
        })

    plan = {"short_id": "short-pacing", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Revisa esta lista."}

    res = short_builder.build_short(
        job,
        plan,
        _cfg(),
        llm_fn=_llm_fn_factory(script=script9, scenes=_nine_scene_doc()),
        gemini_fn=gemini_fn,
        **_stub_io(calls),
    )

    # Under new strict thresholds, pacing=6 blocks render and results in needs_review
    assert res["status"] == "needs_review"
    assert "render" not in calls


# --------------------------------------------------------------------------
# Regression: 3-graphic normal Short must fail deterministically before Gemini
# --------------------------------------------------------------------------

def _three_graphic_scenes():
    """Mirrors the failing short-02_idea-02 candidate: a checklist Short with
    3 graphics (setup checklist + label callout + comparison)."""
    return [
        {"id": "s01", "duration_sec": 3.0, "layout": "short_hook", "on_screen_text": "MARRON NO BASTA", "caption": "c", "visual_prompt": "manos sostienen pan integral en el súper, vertical", "narration": "El pan marrón no es integral."},
        {"id": "s02", "duration_sec": 3.5, "layout": "short_tip", "on_screen_text": "REVISA RAPIDO", "caption": "c", "visual_prompt": "carrito de la compra en pasillo de panadería, vertical", "narration": "Haz esta revisión rápida."},
        {"id": "s03", "duration_sec": 4.0, "layout": "graphic_checklist", "on_screen_text": "TRES PASOS", "caption": "c", "visual_prompt": "checklist", "narration": "Tres comprobaciones rápidas.", "layout_payload": {"title": "TRES PASOS", "items": ["Color no basta", "Primer ingrediente", "Compara fibra"]}},
        {"id": "s04", "duration_sec": 4.5, "layout": "graphic_label_callout", "on_screen_text": "PRIMER INGREDIENTE", "caption": "c", "visual_prompt": "vertical nutrition label close-up", "narration": "Busca harina integral al principio.", "layout_payload": {"title": "PRIMER INGREDIENTE", "productLabel": "Pan integral", "callouts": [{"label": "Harina", "value": "integral"}, {"label": "Fibra", "value": "6 g"}]}},
        {"id": "s05", "duration_sec": 3.5, "layout": "short_tip", "on_screen_text": "EN EL SUPER", "caption": "c", "visual_prompt": "persona comparando dos panes en el supermercado, vertical", "narration": "Compáralo en el súper."},
        {"id": "s06", "duration_sec": 4.5, "layout": "graphic_comparison", "on_screen_text": "FIBRA Y AZUCAR", "caption": "c", "visual_prompt": "vertical two labels", "narration": "Compara fibra y azúcar.", "layout_payload": {"title": "EN EL SÚPER", "left": {"heading": "MEJOR", "text": "Más fibra"}, "right": {"heading": "CUIDADO", "text": "Más azúcar"}}},
        {"id": "s07", "duration_sec": 2.5, "layout": "short_cta", "on_screen_text": "GUARDA ESTA LISTA", "caption": "c", "visual_prompt": "pan en cesta de la compra, vertical", "narration": "Guarda esta lista."},
    ]
