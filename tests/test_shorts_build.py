"""Shorts Autopilot v5 — Phase 3/4: prompts, source map, seo, QA, build_short."""
from __future__ import annotations

import json
from pathlib import Path


def _long_job(tmp_path: Path) -> Path:
    job = tmp_path / "long-job"
    job.mkdir()
    scenes = [
        {"id": "scene-09", "duration_sec": 16.0, "narration": "Empieza por marcar una hora de cierre.",
         "visual_prompt": "woman at night, vertical", "layout": "short_tip", "audio_offset_sec": 183.0},
    ]
    (job / "scenes.json").write_text(json.dumps({"scenes": scenes, "total_duration_sec": 600}), encoding="utf-8")
    (job / "script.json").write_text(json.dumps({"hook": "h", "sections": [], "narration": "n", "cta": "c"}), encoding="utf-8")
    (job / "seo.json").write_text(json.dumps({"title": "Dormir mejor después de los 45"}), encoding="utf-8")
    (job / "whisper_timestamps.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")
    (job / "video.mp4").write_bytes(b"x")
    return job


def _cfg():
    return {
        "channel": {"id": "vida-plena-45"},
        "shorts": {
            "autopilot": {"max_regeneration_attempts": 2},
            "duration": {"min_sec": 20, "target_max_sec": 45},
            "tts": {"provider": "kokoro", "voice_id": "ef_dora", "speed": 1.07},
            "funnel": {"default_cta_without_url": "Vídeo completo en el canal.", "cta_max_words": 8},
        },
    }


# --------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------

def test_short_script_prompt_has_retention_and_language_rules():
    from video_agent.shorts import prompts
    p = prompts.short_script_prompt(_cfg(), {"short_id": "short-01", "format": "pain_to_tip", "narration_seed": "x"}, {})
    low = p.lower()
    assert "json" in low
    assert "no greeting" in low or "no greetings" in low or "saludo" in low
    assert "ancianos" in low  # forbidden term explicitly listed
    assert "pending_shorts_qa" in low


def test_short_scene_prompt_requires_vertical_and_layouts():
    from video_agent.shorts import prompts
    p = prompts.short_scene_prompt(_cfg(), {"short_id": "short-01"}, {"narration": "x"})
    low = p.lower()
    assert "vertical" in low
    assert "short_hook" in low


def test_phase15_graphic_layouts_are_in_scene_and_qa_prompts():
    from video_agent.shorts import prompts

    scene_prompt = prompts.short_scene_prompt_v6(
        _cfg(),
        {"short_id": "short-01"},
        {
            "narration": (
                "Mira la etiqueta del pan: fibra, azúcares y sal por 100 g. "
                "Compara mejor y cuidado. Divide la rutina en 10 min + 10 min."
            )
        },
        feedback="",
    ).lower()
    qa_prompt = prompts.gemini_scenes_qa_prompt(
        _cfg(),
        {"narration": "Mira la etiqueta y compara opciones."},
        {"scenes": []},
    ).lower()

    for layout in ("graphic_label_callout", "graphic_comparison", "graphic_routine_split"):
        assert layout in scene_prompt
        assert layout in qa_prompt

    assert "productlabel" in scene_prompt
    assert "callouts" in scene_prompt
    assert "six allowed graphic" in qa_prompt


def test_short_seo_prompt_prefers_broad_nutrition_tags_over_nutricion45():
    from video_agent.shorts import prompts

    p = prompts.short_seo_prompt(
        _cfg(),
        {"short_id": "short-01", "format": "pain_to_tip"},
        {"hook": "¿El pan engorda?", "narration": "Usa la regla del plato saludable."},
    )
    low = p.lower()

    assert "nutricion45" not in low
    assert "nutrición45" not in low
    assert "#alimentacionsaludable" in low
    assert "#platosaludable" in low


def test_short_seo_prompt_uses_high_volume_keywords_with_spain_45_intent():
    from video_agent.shorts import prompts

    p = prompts.short_seo_prompt(
        _cfg(),
        {"short_id": "short-01", "format": "pain_to_tip", "pillar": "nutrition"},
        {
            "hook": "El error no es comer pan. Es no darle sitio.",
            "narration": "Usa la regla del plato: medio verduras, un cuarto proteína y un cuarto pan o hidrato.",
        },
    )
    low = p.lower()

    assert "high-volume" in low
    assert "alimentación saludable" in low
    assert "plato saludable" in low
    assert "el pan engorda" in low
    assert "combine one broad search keyword" in low
    assert "spain" in low
    assert "45+" in low
    assert "description must reuse" in low


def test_short_seo_normalizes_concatenated_hashtags_and_removes_nutricion45():
    from video_agent.shorts.short_seo_builder import _normalize_hashtags

    assert _normalize_hashtags(["#nutricion45#pan", "#Plato Saludable", "#shorts"]) == [
        "#nutricion",
        "#pan",
        "#platosaludable",
        "#shorts",
    ]


def test_build_short_seo_rewrites_description_with_spaced_normalized_hashtags(tmp_path: Path):
    from video_agent.shorts import short_seo_builder

    job = _long_job(tmp_path)

    def llm_fn(kind, prompt):
        return json.dumps({
            "title": "¿El pan engorda?",
            "description": "Dale sitio al pan.#nutricion45#pan#platosaludable",
            "hashtags": ["#nutricion45#pan", "#platosaludable"],
            "pinned_comment": "¿Cómo lo haces tú?",
        })

    seo = short_seo_builder.build_short_seo(
        job,
        "short-01",
        {"short_id": "short-01"},
        {"hook": "¿El pan engorda?", "narration": "Dale sitio al pan."},
        _cfg(),
        llm_fn,
    )

    assert seo["hashtags"] == ["#nutricion", "#pan", "#platosaludable"]
    assert seo["description"].endswith("#nutricion #pan #platosaludable")
    assert "#nutricion45#pan" not in seo["description"]


def test_phase15_graphic_layouts_preserved_by_scene_normalizer():
    from video_agent.shorts.short_scene_builder import normalize_short_scenes

    doc = {
        "scenes": [
            {"scene_id": "s1", "layout": "graphic_label_callout", "narration": "Mira la etiqueta."},
            {"scene_id": "s2", "layout": "graphic_comparison", "narration": "Compara dos opciones."},
            {"scene_id": "s3", "layout": "graphic_routine_split", "narration": "Divide la rutina."},
        ]
    }

    out = normalize_short_scenes(doc, {"narration": "Mira. Compara. Divide."})

    assert [s["layout"] for s in out["scenes"]] == [
        "graphic_label_callout",
        "graphic_comparison",
        "graphic_routine_split",
    ]
    assert all(s["visual_type"] == "graphic" for s in out["scenes"])


def test_phase15_graphic_validator_accepts_new_layouts_and_stubs_blank_fields():
    from video_agent.shorts.validate_scenes import validate_short_graphic_scenes

    scenes = [
        {
            "id": "s1",
            "layout": "graphic_label_callout",
            "duration_sec": 4,
            "on_screen_text": "",
            "layout_payload": {
                "title": "MIRA PRIMERO",
                "productLabel": "Pan integral",
                "callouts": [
                    {"label": "Fibra", "value": "6 g", "note": "mejor saciedad"},
                    {"label": "Azúcares", "value": "3 g", "note": "por 100 g"},
                ],
            },
        },
        {
            "id": "s2",
            "layout": "graphic_comparison",
            "duration_sec": 4,
            "layout_payload": {
                "title": "EN EL SÚPER",
                "left": {"heading": "MEJOR", "text": "Integral con buena fibra"},
                "right": {"heading": "CUIDADO", "text": "Oscuro, pero sin grano integral"},
            },
        },
        {
            "id": "s3",
            "layout": "graphic_routine_split",
            "duration_sec": 4,
            "layout_payload": {
                "title": "RUTINA 30 MINUTOS",
                "totalLabel": "30 min",
                "blocks": [
                    {"time": "10 min", "text": "Cerrar el día"},
                    {"time": "10 min", "text": "Preparar dormitorio"},
                    {"time": "10 min", "text": "Respirar"},
                ],
            },
        },
    ]

    warnings = validate_short_graphic_scenes(scenes)

    assert warnings  # three graphics is allowed for preview but warned for normal Shorts.
    assert scenes[0]["on_screen_text"] == "MIRA PRIMERO"
    assert scenes[0]["asset_refs"]["background"] == ""


def test_phase15_graphic_validator_rejects_forbidden_comparison_words():
    from video_agent.shorts.validate_scenes import validate_short_graphic_scenes

    scene = {
        "id": "s1",
        "layout": "graphic_comparison",
        "duration_sec": 4,
        "layout_payload": {
            "title": "EN EL SÚPER",
            "left": {"heading": "MEJOR", "text": "Integral con fibra"},
            "right": {"heading": "CUIDADO", "text": "Esto es veneno"},
        },
    }

    try:
        validate_short_graphic_scenes([scene])
    except ValueError as exc:
        assert "veneno" in str(exc).lower()
    else:
        raise AssertionError("expected forbidden comparison language to fail validation")


# --------------------------------------------------------------------------
# source map
# --------------------------------------------------------------------------

def test_build_source_map_records_used_scenes_with_timestamps(tmp_path: Path):
    from video_agent.shorts import source_map
    job = _long_job(tmp_path)
    sm = source_map.build_source_map(
        job,
        short_plan={"short_id": "short-01", "scene_ids": ["scene-09"], "source_start_sec": 183.0, "source_end_sec": 199.0},
        short_script={"narration": "Marca una hora de cierre.", "cta": "Vídeo completo en el canal."},
        channel_config=_cfg(),
    )
    assert sm["short_id"] == "short-01"
    used = sm["used_source_scenes"]
    assert used[0]["scene_id"] == "scene-09"
    assert used[0]["source_start_sec"] == 183.0
    assert "original_narration" in used[0]
    assert sm["funnel"]["cta"]


def test_build_source_map_includes_synthesis_idea_metadata(tmp_path: Path):
    from video_agent.shorts import source_map

    job = _long_job(tmp_path)
    sm = source_map.build_source_map(
        job,
        short_plan={
            "short_id": "short-01",
            "idea_id": "idea-01",
            "idea_type": "synthesis",
            "scene_ids": ["scene-09"],
            "source_scene_ids": ["scene-09"],
            "key_points": [{"point": "Marca una hora", "source_scene_ids": ["scene-09"]}],
        },
        short_script={"narration": "Marca una hora de cierre.", "cta": "Vídeo completo en el canal."},
        channel_config=_cfg(),
    )

    assert sm["idea_id"] == "idea-01"
    assert sm["idea_type"] == "synthesis"
    assert sm["key_points"][0]["source_scene_ids"] == ["scene-09"]


# --------------------------------------------------------------------------
# QA (rule-based)
# --------------------------------------------------------------------------

def _good_short_dir(tmp_path: Path) -> Path:
    from video_agent.shorts import paths
    job = _long_job(tmp_path)
    sd = paths.short_dir(job, "short-01")
    sd.mkdir(parents=True)
    (sd / "short_script.json").write_text(json.dumps({
        "short_id": "short-01", "hook": "¿Duermes pero te levantas cansado?",
        "narration": "¿Duermes pero te levantas cansado? Marca una hora de cierre y apaga la pantalla.\nNotarás la diferencia.",
        "cta": "Vídeo completo en el canal.", "target_duration_sec": 32,
    }), encoding="utf-8")
    (sd / "short_scenes.json").write_text(json.dumps({
        "short_id": "short-01", "total_duration_sec": 32,
        "scenes": [
            {"id": "s1", "duration_sec": 2.5, "on_screen_text": "Mente encendida", "caption": "c", "layout": "short_hook", "visual_prompt": "v vertical"},
            {"id": "s2", "duration_sec": 4.0, "on_screen_text": "Hora de cierre", "caption": "c", "layout": "short_tip", "visual_prompt": "v vertical"},
            {"id": "s3", "duration_sec": 4.0, "on_screen_text": "Apaga pantalla", "caption": "c", "layout": "short_cta", "visual_prompt": "v vertical"},
        ],
    }), encoding="utf-8")
    (sd / "short_source_map.json").write_text(json.dumps({"used_source_scenes": [{"scene_id": "scene-09"}]}), encoding="utf-8")
    return job


def test_qa_passes_clean_short(tmp_path: Path):
    from video_agent.shorts import qa
    job = _good_short_dir(tmp_path)
    out = qa.run_short_qa(job, "short-01", _cfg(), music_track="shorts_sleep_stress")
    assert out["verdict"] == "PASS", out


def test_qa_rejects_greeting(tmp_path: Path):
    from video_agent.shorts import qa, paths
    job = _good_short_dir(tmp_path)
    sp = paths.short_dir(job, "short-01") / "short_script.json"
    d = json.loads(sp.read_text())
    d["narration"] = "Hola, bienvenidos al canal. Hoy vamos a hablar de dormir."
    d["hook"] = "Hola a todos"
    sp.write_text(json.dumps(d), encoding="utf-8")
    out = qa.run_short_qa(job, "short-01", _cfg(), music_track="shorts_sleep_stress")
    assert out["verdict"] == "FAIL"
    assert any("greeting" in i or "saludo" in i for i in out["issues"])


def test_qa_rejects_long_disclaimer(tmp_path: Path):
    from video_agent.shorts import qa, paths
    job = _good_short_dir(tmp_path)
    sp = paths.short_dir(job, "short-01") / "short_script.json"
    d = json.loads(sp.read_text())
    d["narration"] = ("Marca una hora de cierre. Este contenido es informativo y no sustituye la opinión "
                      "de un profesional de salud; consulta siempre a tu médico antes de cualquier cambio en tu rutina.")
    sp.write_text(json.dumps(d), encoding="utf-8")
    out = qa.run_short_qa(job, "short-01", _cfg(), music_track="shorts_sleep_stress")
    assert out["verdict"] == "FAIL"
    assert any("disclaimer" in i for i in out["issues"])


def test_qa_rejects_medical_overclaim(tmp_path: Path):
    from video_agent.shorts import qa, paths
    job = _good_short_dir(tmp_path)
    sp = paths.short_dir(job, "short-01") / "short_script.json"
    d = json.loads(sp.read_text())
    d["narration"] = "Esta rutina cura el insomnio para siempre, garantizado."
    sp.write_text(json.dumps(d), encoding="utf-8")
    out = qa.run_short_qa(job, "short-01", _cfg(), music_track="shorts_sleep_stress")
    assert out["verdict"] == "FAIL"
    assert any("overclaim" in i or "medical" in i for i in out["issues"])


# --------------------------------------------------------------------------
# build_short orchestration (injected LLM/tts/mix/render)
# --------------------------------------------------------------------------

_GOOD_SCRIPT = {
    "short_id": "short-01", "source_long_job_id": "long-job", "short_format": "pain_to_tip",
    "target_duration_sec": 32, "hook": "¿Duermes pero te levantas cansado?",
    "narration": "¿Duermes pero te levantas cansado? Marca una hora de cierre y apaga la pantalla.\nNotarás la diferencia.",
    "beats": ["pain", "tip"], "cta": "Vídeo completo en el canal.", "qa": {"verdict": "PENDING_SHORTS_QA"},
}
_GOOD_SCENES = {
    "channel_id": "vida-plena-45", "short_id": "short-01", "total_duration_sec": 32,
    "scenes": [
        {"id": "s1", "duration_sec": 6.0, "on_screen_text": "Mente encendida", "caption": "c", "layout": "short_hook", "visual_prompt": "v vertical"},
        {"id": "s2", "duration_sec": 20.0, "on_screen_text": "Hora de cierre", "caption": "c", "layout": "short_tip", "visual_prompt": "v vertical"},
        {"id": "s3", "duration_sec": 6.0, "on_screen_text": "Apaga pantalla", "caption": "c", "layout": "short_cta", "visual_prompt": "v vertical"},
    ],
    "qa": {"verdict": "PENDING_SHORTS_QA"},
}


def _llm_fn_factory(script=_GOOD_SCRIPT, scenes=_GOOD_SCENES):
    def fn(kind, prompt):
        if kind == "script":
            return json.dumps(script)
        if kind == "scenes":
            return json.dumps(scenes)
        if kind == "seo":
            return json.dumps({"title": "Dormir mejor 45+", "description": "d", "hashtags": ["#shorts"],
                               "pinned_comment": "Mira el vídeo largo"})
        return "{}"
    return fn


def _stub_io(calls):
    def tts_fn(short_dir, short_scenes, channel_config):
        calls.append("tts"); (short_dir / "audio").mkdir(parents=True, exist_ok=True)
        (short_dir / "audio" / "short_narration.wav").write_bytes(b"w"); return short_dir / "audio" / "short_narration.wav"
    def mix_fn(short_dir, narration_wav, music_track, channel_config, duration_sec):
        calls.append("mix"); (short_dir / "audio" / "short_mix.m4a").write_bytes(b"m"); return short_dir / "audio" / "short_mix.m4a"
    def render_fn(short_dir, channel_config):
        calls.append("render"); (short_dir / "outputs").mkdir(parents=True, exist_ok=True)
        out = short_dir / "outputs" / "short.mp4"
        out.write_bytes(b"v"); return out
    def cover_fn(short_dir, channel_config):
        calls.append("cover"); (short_dir / "outputs").mkdir(parents=True, exist_ok=True)
        out = short_dir / "outputs" / "short_cover.jpg"
        out.write_bytes(b"j"); return out
    return dict(tts_fn=tts_fn, mix_fn=mix_fn, render_fn=render_fn, cover_fn=cover_fn)


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
    assert calls == ["tts", "mix", "render", "cover"]
    assert res["music_track"] == "shorts_sleep_stress"


def test_build_short_regenerates_then_needs_review_after_limit(tmp_path: Path):
    from video_agent.shorts import short_builder, paths
    job = _long_job(tmp_path)
    calls: list[str] = []
    attempts = {"n": 0}
    bad_script = {**_GOOD_SCRIPT, "hook": "Hola a todos", "narration": "Hola, bienvenidos. Hoy vamos a hablar."}

    def llm_fn(kind, prompt):
        if kind == "script":
            attempts["n"] += 1
            return json.dumps(bad_script)  # always greeting → always FAIL
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
    assert calls == []


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

def test_normalize_short_scenes_renames_scene_id_and_injects_narration():
    from video_agent.shorts import short_scene_builder
    scenes_doc = {
        "scenes": [
            {"scene_id": "s1", "duration_sec": 2.5, "on_screen_text": "Hook", "layout": "short_hook", "visual_prompt": "v"},
            {"scene_id": "s2", "duration_sec": 4.0, "on_screen_text": "Tip", "layout": "short_tip", "visual_prompt": "v"},
        ]
    }
    script = {"narration": "Primera idea clave. Segunda idea practica.", "hook": "Hook"}
    out = short_scene_builder.normalize_short_scenes(scenes_doc, script)
    scenes = out["scenes"]
    # scene_id → id for render/TTS
    assert all("id" in s for s in scenes)
    assert scenes[0]["id"] == "s1"
    # every scene has non-empty narration (TTS needs it)
    assert all(str(s.get("narration", "")).strip() for s in scenes)
    # narration distributed (not identical empty)
    assert scenes[0]["narration"] != scenes[1]["narration"]


def test_normalize_short_scenes_keeps_existing_narration():
    from video_agent.shorts import short_scene_builder
    scenes_doc = {"scenes": [{"scene_id": "s1", "narration": "Ya tengo voz.", "duration_sec": 3.0}]}
    out = short_scene_builder.normalize_short_scenes(scenes_doc, {"narration": "otra"})
    assert out["scenes"][0]["narration"] == "Ya tengo voz."
    assert out["scenes"][0]["id"] == "s1"


def test_normalize_short_scenes_seeds_full_render_contract():
    from video_agent.shorts import short_scene_builder
    out = short_scene_builder.normalize_short_scenes(
        {"scenes": [{"scene_id": "s1", "on_screen_text": "Hook", "duration_sec": 2.5}]},
        {"narration": "Una idea."},
    )
    sc = out["scenes"][0]
    for key in ("id", "narration", "on_screen_text", "caption", "visual_prompt", "layout",
                "layout_payload", "layout_reason", "motion", "asset_refs", "word_segments",
                "planner_warnings", "audio_offset_sec", "duration_sec"):
        assert key in sc, key
    assert isinstance(sc["asset_refs"], dict)
    assert out["total_duration_sec"] > 0


def test_normalize_short_scenes_maps_layout_to_render_enum():
    from video_agent.shorts import short_scene_builder
    out = short_scene_builder.normalize_short_scenes(
        {"scenes": [
            {"scene_id": "s1", "layout": "short_hook", "duration_sec": 2.0},
            {"scene_id": "s2", "layout": "short_cta", "duration_sec": 3.0},
            {"scene_id": "s3", "layout": "short_tip", "duration_sec": 3.0},
        ]},
        {"narration": "Uno. Dos. Tres."},
    )
    # Spec v6 §10: short_* is the first-class layout family. Long-form names
    # are only accepted via the legacy adapter (separate test).
    valid = {"short_hook", "short_pain", "short_tip", "short_checklist",
             "short_myth", "short_quote", "short_cta"}
    assert all(s["layout"] in valid for s in out["scenes"])
    assert out["scenes"][0]["layout"] == "short_hook"
    assert out["scenes"][1]["layout"] == "short_cta"


def test_normalize_short_scenes_maps_legacy_long_form_layouts():
    """Legacy adapter (spec v6 §10): old short artifacts using long-form
    layout names must round-trip to short_* equivalents."""
    from video_agent.shorts import short_scene_builder
    out = short_scene_builder.normalize_short_scenes(
        {"scenes": [
            {"id": "s1", "layout": "hook", "duration_sec": 2.0, "narration": "n"},
            {"id": "s2", "layout": "subtitle", "duration_sec": 3.0, "narration": "n"},
            {"id": "s3", "layout": "warning", "duration_sec": 3.0, "narration": "n"},
            {"id": "s4", "layout": "cta", "duration_sec": 3.0, "narration": "n"},
        ]},
        {"narration": "x"},
    )
    layouts = [s["layout"] for s in out["scenes"]]
    assert layouts == ["short_hook", "short_tip", "short_pain", "short_cta"]
