"""Spec v6 — AI role contract + temporary conversations."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# §2 AI provider contract: which fn uses which provider
# ---------------------------------------------------------------------------

def test_llm_role_contract_uses_chatgpt_for_planner_script_and_scenes():
    """Spec v6 §2.2/§2.3/§2.4: planner, script builder, scene builder must use
    ChatGPT as their LLM provider. Recorded via PROVIDER = 'chatgpt'."""
    from video_agent.shorts import planner, short_script_builder, short_scene_builder
    assert planner.PROVIDER == "chatgpt", planner.PROVIDER
    assert short_script_builder.PROVIDER == "chatgpt", short_script_builder.PROVIDER
    assert short_scene_builder.PROVIDER == "chatgpt", short_scene_builder.PROVIDER


def test_llm_role_contract_uses_gemini_for_qa():
    """Spec v6 §2.5: qa.py runs Gemini as the final verdict gate."""
    from video_agent.shorts import qa
    assert qa.LLM_PROVIDER == "gemini", qa.LLM_PROVIDER


def test_short_llm_calls_use_temporary_conversations():
    """Spec v6 §3: every LLM call must use a temp conversation. The shorts
    package exposes a helper that flags this; sender contract = (kind, prompt)
    routed to the right provider's temp send."""
    from video_agent.shorts import llm
    assert llm.uses_temporary_conversations() is True


# ---------------------------------------------------------------------------
# §2.2 planner constraint: only choose from provided candidates
# ---------------------------------------------------------------------------

def _long_job(tmp_path: Path) -> Path:
    job = tmp_path / "long-job"; job.mkdir()
    scenes = [{"id": f"scene-{i:02d}", "duration_sec": 10.0, "narration": f"frase {i}",
               "visual_prompt": "v", "layout": "subtitle", "audio_offset_sec": i * 10.0}
              for i in range(1, 8)]
    (job / "scenes.json").write_text(json.dumps({"scenes": scenes, "total_duration_sec": 70}), encoding="utf-8")
    (job / "script.json").write_text(json.dumps({"hook": "h", "narration": "n", "sections": [], "cta": "c"}), encoding="utf-8")
    (job / "seo.json").write_text(json.dumps({"title": "Dormir mejor"}), encoding="utf-8")
    (job / "whisper_timestamps.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")
    (job / "video.mp4").write_bytes(b"x")
    return job


def _cfg():
    return {
        "channel": {"id": "vida-plena-45"},
        "shorts": {
            "auto_generate": {"default_count": 3, "max_count": 5, "allow_fewer_if_candidates_are_weak": True},
            "content": {"default_formats_per_long": ["pain_to_tip", "mistake_to_avoid", "mini_checklist"]},
            "tts": {"provider": "kokoro", "voice_id": "ef_dora", "speed": 1.07},
            "music": {"enabled": True},
            "cover": {"enabled": True},
        },
        "music_library": {"tracks": {"shorts_sleep_stress": {"file": "x", "use_for": ["sleep"]}}},
    }


def test_planner_chatgpt_can_only_select_from_candidates(tmp_path: Path):
    """Planner must reject any selected_short whose candidate_id is not in the
    extractor's candidate set, even if ChatGPT invents one."""
    from video_agent.shorts import planner

    def bad_llm_fn(prompt: str) -> str:
        return json.dumps({
            "selected_shorts": [
                {"short_id": "short-01", "candidate_id": "FAKE-NOT-IN-LIST",
                 "format": "pain_to_tip", "source_scene_ids": ["scene-99"]}
            ],
            "detected_pillar": "sleep", "warnings": [],
        })

    plan = planner.plan_shorts_from_long_video(
        _long_job(tmp_path), _cfg(), llm_fn=bad_llm_fn,
    )
    assert plan["selected_shorts"] == [] or all(
        s["candidate_id"] != "FAKE-NOT-IN-LIST" for s in plan["selected_shorts"]
    )
    assert any("invalid_candidate_id" in w for w in plan.get("warnings", []))


def test_planner_chatgpt_picks_from_real_candidates(tmp_path: Path):
    """Happy path: planner uses ChatGPT pick when candidate_id matches."""
    from video_agent.shorts import planner, extractor

    job = _long_job(tmp_path)
    cands = extractor.extract_candidates(job)
    assert cands, "extractor must produce candidates"
    good_id = cands[0]["candidate_id"]

    def good_llm_fn(prompt: str) -> str:
        return json.dumps({
            "selected_shorts": [
                {"short_id": "short-01", "candidate_id": good_id,
                 "format": "pain_to_tip",
                 "source_scene_ids": cands[0]["scene_ids"],
                 "hook_angle": "x", "viewer_pain": "y", "practical_payoff": "z",
                 "music_track": "shorts_sleep_stress",
                 "reason": "strong hook"},
            ],
            "detected_pillar": "sleep", "warnings": [],
        })

    plan = planner.plan_shorts_from_long_video(job, _cfg(), llm_fn=good_llm_fn)
    assert plan["selected_shorts"]
    assert plan["selected_shorts"][0]["candidate_id"] == good_id


# ---------------------------------------------------------------------------
# §2.4 scene builder uses LLM for layouts, NOT a deterministic table
# ---------------------------------------------------------------------------

def test_short_scene_builder_uses_chatgpt_to_choose_layouts(tmp_path: Path):
    """ChatGPT chooses each scene's layout. Builder must NOT hard-code the
    layout sequence — whatever layouts the LLM emits should pass through."""
    from video_agent.shorts import short_scene_builder
    from video_agent.shorts.paths import short_dir

    captured = {}

    def chatgpt_fn(prompt: str) -> str:
        captured["prompt"] = prompt
        return json.dumps({
            "scenes": [
                {"id": "s1", "layout": "short_quote", "duration_sec": 3.0,
                 "narration": "Una frase", "on_screen_text": "MENOS CARGA",
                 "visual_prompt": "vertical close-up",
                 "layout_payload": {"title": "MENOS CARGA"}, "source_scene_ids": []},
                {"id": "s2", "layout": "short_myth", "duration_sec": 4.0,
                 "narration": "Otra", "on_screen_text": "NO ES MÁS",
                 "visual_prompt": "v", "layout_payload": {"title": "NO ES MÁS"},
                 "source_scene_ids": []},
            ],
            "qa": {"verdict": "PENDING_SHORTS_QA"},
        })

    job = tmp_path / "long-job"; job.mkdir()
    short_dir(job, "short-01").mkdir(parents=True, exist_ok=True)
    out = short_scene_builder.build_short_scenes(
        long_job_dir=job,
        short_plan={"short_id": "short-01"},
        short_script={"hook": "h", "narration": "n"},
        channel_config={"channel": {"id": "x"}, "shorts": {}},
        llm_fn=chatgpt_fn,
    )
    layouts = [s["layout"] for s in out["scenes"]]
    assert layouts[0] == "short_quote", layouts
    assert layouts[1] == "short_myth", layouts


def test_layout_sequences_are_guidelines_not_hard_coded_assignment():
    """short_scene_builder.normalize_short_scenes must NOT reassign LLM
    layouts to a fixed sequence; it only fills missing ones with a fallback."""
    from video_agent.shorts import short_scene_builder
    out = short_scene_builder.normalize_short_scenes(
        {"scenes": [
            {"id": "a", "layout": "short_quote", "duration_sec": 2.0, "narration": "n"},
            {"id": "b", "layout": "short_myth", "duration_sec": 2.0, "narration": "n"},
            {"id": "c", "layout": "short_checklist", "duration_sec": 2.0, "narration": "n"},
        ]},
        {"narration": "x"},
    )
    assert [s["layout"] for s in out["scenes"]] == [
        "short_quote", "short_myth", "short_checklist"
    ]


# ---------------------------------------------------------------------------
# §2.5 Gemini QA — dual-gate: rule-based pre-filter, Gemini final verdict
# ---------------------------------------------------------------------------

def _make_short_dir(tmp_path: Path) -> Path:
    from video_agent.shorts import paths
    job = tmp_path / "long-job"; job.mkdir()
    sd = paths.short_dir(job, "short-01"); sd.mkdir(parents=True)
    (sd / "short_script.json").write_text(json.dumps({
        "short_id": "short-01", "hook": "¿Duermes pero te levantas cansado?",
        "narration": "¿Duermes pero te levantas cansado? Marca una hora de cierre.\nApaga la pantalla.",
        "cta": "Vídeo completo en el canal.", "target_duration_sec": 32,
    }), encoding="utf-8")
    (sd / "short_scenes.json").write_text(json.dumps({
        "short_id": "short-01", "total_duration_sec": 32,
        "scenes": [
            {"id": "s1", "duration_sec": 2.5, "on_screen_text": "MENTE ENCENDIDA",
             "caption": "c", "layout": "short_hook", "visual_prompt": "v"},
            {"id": "s2", "duration_sec": 4.0, "on_screen_text": "HORA DE CIERRE",
             "caption": "c", "layout": "short_tip", "visual_prompt": "v"},
            {"id": "s3", "duration_sec": 4.0, "on_screen_text": "VÍDEO COMPLETO",
             "caption": "c", "layout": "short_cta", "visual_prompt": "v"},
        ],
    }), encoding="utf-8")
    (sd / "short_source_map.json").write_text(json.dumps({
        "used_source_scenes": [{"scene_id": "scene-09"}]
    }), encoding="utf-8")
    return job


def test_gemini_qa_passes_clean_short(tmp_path: Path):
    """Dual gate: rule pre-filter passes → Gemini returns PASS → final PASS."""
    from video_agent.shorts import qa
    job = _make_short_dir(tmp_path)

    def gemini_fn(prompt: str) -> str:
        return json.dumps({
            "verdict": "PASS", "issues": [], "required_changes": [], "warnings": [],
            "scores": {"hook": 90, "payoff": 85, "funnel": 80, "source_fidelity": 90,
                       "safety": 95, "mobile_readability": 90, "layout": 90},
        })

    out = qa.run_short_qa(job, "short-01", {"channel": {}, "shorts": {}},
                          music_track="shorts_sleep_stress", gemini_fn=gemini_fn)
    assert out["verdict"] == "PASS"
    assert out.get("provider") == "gemini"


def test_gemini_qa_rejects_bad_layout_choices(tmp_path: Path):
    """When Gemini says FAIL for bad layout, final verdict = FAIL with
    Gemini's required_changes preserved."""
    from video_agent.shorts import qa
    job = _make_short_dir(tmp_path)

    def gemini_fn(prompt: str) -> str:
        return json.dumps({
            "verdict": "FAIL",
            "issues": ["scene-02 layout=short_cta but no CTA payoff"],
            "required_changes": ["use short_tip for scene-02"],
            "warnings": [],
            "scores": {"layout": 40},
        })

    out = qa.run_short_qa(job, "short-01", {"channel": {}, "shorts": {}},
                          music_track="shorts_sleep_stress", gemini_fn=gemini_fn)
    assert out["verdict"] == "FAIL"
    assert any("short_cta" in c or "short_tip" in c for c in out["required_changes"])


def test_dual_gate_rule_fail_skips_gemini(tmp_path: Path):
    """Rule pre-filter catches greeting → return FAIL without consulting
    Gemini (saves an LLM call, spec v6 §13 doesn't forbid)."""
    from video_agent.shorts import qa, paths
    job = _make_short_dir(tmp_path)
    sp = paths.short_dir(job, "short-01") / "short_script.json"
    d = json.loads(sp.read_text())
    d["narration"] = "Hola, bienvenidos al canal."; d["hook"] = "Hola a todos"
    sp.write_text(json.dumps(d), encoding="utf-8")

    gemini_calls = []

    def gemini_fn(prompt: str) -> str:
        gemini_calls.append(1)
        return json.dumps({"verdict": "PASS"})

    out = qa.run_short_qa(job, "short-01", {"channel": {}, "shorts": {}},
                          music_track="shorts_sleep_stress", gemini_fn=gemini_fn)
    assert out["verdict"] == "FAIL"
    assert gemini_calls == []  # rule pre-filter short-circuited
    assert any("greeting" in i for i in out["issues"])


# ---------------------------------------------------------------------------
# §10 short layouts first-class + legacy adapter
# ---------------------------------------------------------------------------

def test_short_scene_schema_accepts_short_layouts():
    """render-props schema must accept short_* layout names."""
    import json as _json
    from pathlib import Path as _P
    s = _json.loads(_P("schemas/render-props.schema.json").read_text())
    enum = s["properties"]["scenes"]["items"]["properties"]["layout"]["enum"]
    for L in ("short_hook", "short_pain", "short_tip", "short_checklist",
              "short_myth", "short_quote", "short_cta"):
        assert L in enum, L


def test_legacy_short_layouts_map_to_short_layouts_with_warning():
    """Spec v6 §10 backward-compat adapter."""
    from video_agent.shorts.short_scene_builder import _map_layout, _LEGACY_TO_SHORT
    for legacy, short in _LEGACY_TO_SHORT.items():
        assert _map_layout(legacy) == short, (legacy, short)


# ---------------------------------------------------------------------------
# §12 Cover primary = ChatGPT-generated thumbnail; ffmpeg frame fallback only
# ---------------------------------------------------------------------------

def test_short_cover_uses_chatgpt_thumbnail_primary_ffmpeg_fallback():
    """renderer.render_short_cover must reuse the ChatGPT-generated
    thumbnail.jpg as primary cover; ffmpeg frame extract is fallback only."""
    import inspect
    from video_agent.shorts import renderer, paths
    src = inspect.getsource(renderer.render_short_cover)
    assert "SHORT_THUMBNAIL_FILE" in src, "primary cover must reuse ChatGPT thumbnail.jpg"
    assert "fallback" in src.lower() or "ffmpeg" in src.lower(), "ffmpeg path must be fallback only"
