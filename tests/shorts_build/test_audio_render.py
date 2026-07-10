from __future__ import annotations

from .conftest import *  # noqa: F401,F403

def test_audio_fit_guard_runs_after_tts_before_mix_and_render(tmp_path: Path):
    import wave

    from video_agent.shorts import short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    plan = {"short_id": "short-audio-fit", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    valid_scenes = {
        "channel_id": "vida-plena-45",
        "short_id": "short-audio-fit",
        "total_duration_sec": 21.0,
        "scenes": [
            {"id": "s1", "duration_sec": 2.5, "on_screen_text": "MENTE ENCENDIDA", "caption": "c", "layout": "short_hook", "visual_prompt": "vertical bedroom", "narration": "Abre fuerte."},
            {"id": "s2", "duration_sec": 4.2, "on_screen_text": "HORA DE CIERRE", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical clock", "narration": "Marca una hora de cierre."},
            {"id": "s3", "duration_sec": 4.2, "on_screen_text": "APAGA PANTALLA", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical phone", "narration": "Apaga la pantalla."},
            {"id": "s4", "duration_sec": 4.2, "on_screen_text": "RESPIRA DESPACIO", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical calm person", "narration": "Respira despacio."},
            {"id": "s5", "duration_sec": 3.5, "on_screen_text": "BAJA EL RITMO", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical calm room", "narration": "Baja el ritmo."},
            {"id": "s6", "duration_sec": 2.4, "on_screen_text": "GUARDA ESTA IDEA", "caption": "c", "layout": "short_cta", "visual_prompt": "vertical calm person", "narration": "Guarda esta idea."},
        ],
        "qa": {"verdict": "PENDING_SCENES_QA"},
    }

    def tts_fn(short_dir, short_scenes, channel_config):
        calls.append("tts")
        audio_dir = short_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        wav_path = audio_dir / "short_narration.wav"
        with wave.open(str(wav_path), "w") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\0\0" * int(30.0 * 8000))
        return wav_path

    io = _stub_io(calls)
    io["tts_fn"] = tts_fn

    cfg = _cfg()
    cfg["shorts"]["autopilot"]["max_regeneration_attempts"] = 0

    res = short_builder.build_short(
        job,
        plan,
        cfg,
        llm_fn=_llm_fn_factory(scenes=valid_scenes),
        **io,
    )

    assert res["status"] == "needs_review"
    assert res["qa_verdict"] == "FAIL"
    assert calls == ["background", "tts"]
    assert "audio_fit" in json.dumps(res).lower()


def test_audio_fit_small_tail_shortage_extends_scene_durations():
    from video_agent.shorts import validate_scenes

    scenes_doc = {
        "total_duration_sec": 22.4,
        "scenes": [
            {"id": "s1", "layout": "short_hook", "duration_sec": 2.5},
            {"id": "s2", "layout": "short_tip", "duration_sec": 4.2},
            {"id": "s3", "layout": "short_tip", "duration_sec": 4.2},
            {"id": "s4", "layout": "short_tip", "duration_sec": 4.2},
            {"id": "s5", "layout": "short_tip", "duration_sec": 4.2},
            {"id": "s6", "layout": "short_cta", "duration_sec": 2.4},
        ],
    }

    result = validate_scenes.extend_scene_durations_for_audio_tail(
        scenes_doc,
        narration_audio_sec=22.42,
    )

    assert result["changed"] is True
    assert scenes_doc["total_duration_sec"] >= 23.0
    assert validate_scenes.validate_audio_fit(scenes_doc["total_duration_sec"], 22.42) is None
    assert not validate_scenes.has_blocking_or_repairable(
        validate_scenes.validate_scene_structure(scenes_doc["scenes"], scenes_doc=scenes_doc)
    )


def test_audio_tail_repair_does_not_compress_scene_sum_to_audio_duration():
    from video_agent.shorts import validate_scenes

    scenes_doc = {
        "total_duration_sec": 21.7,
        "scenes": [
            {"id": "s01", "layout": "short_hook", "duration_sec": 2.5},
            {"id": "s02", "layout": "short_tip", "duration_sec": 3.5},
            {"id": "s03", "layout": "short_tip", "duration_sec": 3.5},
            {"id": "s04", "layout": "short_tip", "duration_sec": 3.5},
            {"id": "s05", "layout": "short_tip", "duration_sec": 3.5},
            {"id": "s06", "layout": "short_tip", "duration_sec": 3.5},
            {"id": "s07", "layout": "short_checklist", "duration_sec": 5.0},
            {"id": "s08", "layout": "short_cta", "duration_sec": 2.5},
        ],
    }

    result = validate_scenes.extend_scene_durations_for_audio_tail(
        scenes_doc,
        narration_audio_sec=20.92,
    )

    assert result["changed"] is False
    assert result["reason"] == "already_fits"
    assert scenes_doc["total_duration_sec"] == 27.5
    assert validate_scenes.validate_audio_fit(scenes_doc["total_duration_sec"], 20.92) is None


def test_audio_fit_large_shortage_is_not_stretched_past_pacing():
    from video_agent.shorts import validate_scenes

    scenes_doc = {
        "total_duration_sec": 21.0,
        "scenes": [
            {"id": "s1", "layout": "short_hook", "duration_sec": 2.5},
            {"id": "s2", "layout": "short_tip", "duration_sec": 4.2},
            {"id": "s3", "layout": "short_tip", "duration_sec": 4.2},
            {"id": "s4", "layout": "short_tip", "duration_sec": 4.2},
            {"id": "s5", "layout": "short_tip", "duration_sec": 3.5},
            {"id": "s6", "layout": "short_cta", "duration_sec": 2.4},
        ],
    }

    result = validate_scenes.extend_scene_durations_for_audio_tail(
        scenes_doc,
        narration_audio_sec=30.0,
    )

    assert result["changed"] is False
    assert scenes_doc["total_duration_sec"] == 21.0




def test_audio_fit_failure_surfaces_without_regenerating_script(tmp_path: Path):
    import wave
    from unittest.mock import patch
    from video_agent.shorts import llm_history, paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    
    script_attempts = {"n": 0, "feedbacks": []}
    
    def llm_fn(kind, prompt):
        if kind == "script":
            script_attempts["n"] += 1
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            return json.dumps(_GOOD_SCENES)
        if kind == "seo":
            return json.dumps({"title": "t", "description": "d", "hashtags": [], "pinned_comment": "p"})
        return "{}"

    original_build_script = short_builder.short_script_builder.build_short_script
    def captured_build_script(*args, **kwargs):
        script_attempts["feedbacks"].append(kwargs.get("feedback", ""))
        return original_build_script(*args, **kwargs)

    with patch("video_agent.shorts.short_script_builder.build_short_script", captured_build_script):
        def tts_fn(short_dir, short_scenes, channel_config):
            calls.append("tts")
            audio_dir = short_dir / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            wav_path = audio_dir / "short_narration.wav"
            with wave.open(str(wav_path), "w") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(8000)
                handle.writeframes(b"\0\0" * int(30.0 * 8000))
            return wav_path

        io = _stub_io(calls)
        io["tts_fn"] = tts_fn

        cfg = _cfg()
        cfg["shorts"]["autopilot"]["max_regeneration_attempts"] = 1

        plan = {"short_id": "short-audio-fit-retry", "format": "pain_to_tip", "scene_ids": ["scene-09"], "music_track": "shorts_sleep_stress"}
        res = short_builder.build_short(
            job,
            plan,
            cfg,
            llm_fn=llm_fn,
            gemini_fn=lambda p: json.dumps({
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
                    "saveability": 8.5
                }
            }),
            **io,
        )

        assert res["status"] == "needs_review"
        assert res["qa_verdict"] == "FAIL"
        assert script_attempts["n"] == 1
        assert calls == ["background", "tts"]
        assert not any("AUDIO-FIT" in f or "narration audio exceeds" in f for f in script_attempts["feedbacks"])
        assert "audio_fit" in json.dumps(res).lower()

        short_dir = paths.short_dir(job, "short-audio-fit-retry")
        status_doc = json.loads((short_dir / "short_status.json").read_text(encoding="utf-8"))
        audio_stage = next(stage for stage in status_doc["stages"] if stage["name"] == "audio")
        assert audio_stage["status"] == "failed"
        assert "Narration audio" in audio_stage["error"]

        hist = llm_history.read_history(short_dir / "json" / paths.SHORT_LLM_HISTORY_FILE)
        stage_events = [
            h for h in hist
            if h.get("provider") == "deterministic" and h.get("kind") == "stage_status"
        ]
        audio_fail_events = [
            h for h in stage_events
            if h.get("payload", {}).get("stage") == "audio" and h.get("payload", {}).get("status") == "failed"
        ]
        assert len(audio_fail_events) == 1
        assert audio_fail_events[0]["payload"]["verdict"] == "FAIL"
        assert "Narration audio" in audio_fail_events[0]["payload"]["error"]


def test_repair_scene_duration_if_possible():
    from video_agent.shorts.validate_scenes import repair_scene_duration_if_possible
    
    # Fits within cap (hook cap is 3.0s, required is 1.4s)
    s1 = {"duration_sec": 1.0, "layout": "short_hook", "narration": "Abre fuerte."}
    res1 = repair_scene_duration_if_possible(s1)
    assert res1 == "auto_extended"
    assert s1["duration_sec"] == 1.4

    # Exceeds cap (hook cap is 3.0s, narration has 8 words -> required is 4.0s)
    s2 = {"duration_sec": 1.0, "layout": "short_hook", "narration": "Abre fuerte y mira esta increible etiqueta ahora mismo."}
    res2 = repair_scene_duration_if_possible(s2)
    assert res2 == "must_split_or_compress"
    assert s2["duration_sec"] == 1.0


def test_action_specific_repair_hints():
    from video_agent.shorts.validate_scenes import build_scene_repair_plan, SceneValidationIssue

    scenes = [
        {"id": "s01", "layout": "short_hook", "narration": "a"},
        {"id": "s06", "layout": "graphic_label_callout", "narration": "b"},
        {"id": "s09", "layout": "short_quote", "narration": "c"},
        {"id": "s10", "layout": "short_cta", "narration": "d"},
        {"id": "s02", "layout": "short_tip", "narration": "e"},
    ]
    issues = [
        SceneValidationIssue(type="scene_narration_fit", scene_id="s01", severity="repairable_error", detail="x"),
        SceneValidationIssue(type="scene_narration_fit", scene_id="s06", severity="repairable_error", detail="x"),
        SceneValidationIssue(type="scene_narration_fit", scene_id="s09", severity="repairable_error", detail="x"),
        SceneValidationIssue(type="scene_narration_fit", scene_id="s10", severity="repairable_error", detail="x"),
        SceneValidationIssue(type="scene_narration_fit", scene_id="s02", severity="repairable_error", detail="x"),
    ]
    plan = build_scene_repair_plan(scenes, issues)
    inst = "\n".join(plan["instructions"])
    assert "Hook narration is too long" in inst
    assert "Current narration is too long for a single graphic_label_callout" in inst
    assert "Quote narration is too long" in inst
    # bug-505: CTA fit repair keeps the funnel wording and sizes the duration,
    # instead of the old "CTA narration is too long -> shorten to shopping CTA".
    assert "Keep the CTA wording" in inst
    assert "one short sentence" in inst


