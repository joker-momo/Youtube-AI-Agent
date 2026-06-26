"""Shorts Autopilot v5 — Phase 2: extractor, scorer, planner, music selector."""
from __future__ import annotations

import json
from pathlib import Path


def _write_long(tmp_path: Path, scenes, title="Cómo moverte después de los 45") -> Path:
    job = tmp_path / "long-job"
    job.mkdir()
    (job / "scenes.json").write_text(json.dumps({"scenes": scenes, "total_duration_sec": 600}), encoding="utf-8")
    (job / "script.json").write_text(json.dumps({"hook": "h", "sections": [], "narration": "n", "cta": "c"}), encoding="utf-8")
    (job / "seo.json").write_text(json.dumps({"title": title}), encoding="utf-8")
    (job / "video.mp4").write_bytes(b"x")
    return job


def _scene(i, narration, layout="short_tip", offset=0.0, dur=15.0, **kw):
    s = {
        "id": f"scene-{i:02d}",
        "duration_sec": dur,
        "narration": narration,
        "on_screen_text": kw.get("ost", "Texto"),
        "caption": kw.get("caption", "cap"),
        "visual_prompt": kw.get("visual", "woman walking in park, vertical"),
        "layout": layout,
        "audio_offset_sec": offset,
    }
    return s


# --------------------------------------------------------------------------
# extractor
# --------------------------------------------------------------------------

def test_extractor_builds_single_and_window_candidates(tmp_path: Path):
    from video_agent.shorts import extractor
    scenes = [_scene(i, f"Frase numero {i}", offset=(i - 1) * 15.0) for i in range(1, 5)]
    job = _write_long(tmp_path, scenes)
    cands = extractor.extract_candidates(job)
    # single scenes present
    assert any(c["scene_ids"] == ["scene-01"] for c in cands)
    # at least one multi-scene window
    assert any(len(c["scene_ids"]) >= 2 for c in cands)
    # timestamps derived from audio_offset_sec
    first = next(c for c in cands if c["scene_ids"] == ["scene-02"])
    assert first["source_start_sec"] == 15.0
    assert first["source_end_sec"] == 30.0


# --------------------------------------------------------------------------
# candidate scorer
# --------------------------------------------------------------------------

def test_scorer_rewards_pain_and_action(tmp_path: Path):
    from video_agent.shorts import candidate_scorer
    strong = {
        "scene_ids": ["scene-09"],
        "narration": "¿Te duele la rodilla al caminar? Evita las cuestas y haz pausas cortas para moverte sin dolor.",
        "layouts": ["short_pain"],
        "visual_prompt": "knee close up walking, vertical",
    }
    weak = {
        "scene_ids": ["scene-01"],
        "narration": "En este vídeo vamos a hablar de bienestar en general.",
        "layouts": ["short_tip"],
        "visual_prompt": "generic background",
    }
    s_strong = candidate_scorer.score_candidate(strong, {})
    s_weak = candidate_scorer.score_candidate(weak, {})
    assert s_strong["final_score"] > s_weak["final_score"]
    assert s_strong["final_score"] >= 65


def test_scorer_penalizes_requires_previous_context(tmp_path: Path):
    from video_agent.shorts import candidate_scorer
    base = {"scene_ids": ["s"], "narration": "Haz esta pausa corta para descansar mejor hoy.", "layouts": [], "visual_prompt": "v"}
    ctx = {"scene_ids": ["s"], "narration": "Como vimos antes en el punto anterior, sigue con el paso siguiente.", "layouts": [], "visual_prompt": "v"}
    assert candidate_scorer.score_candidate(base, {})["final_score"] > candidate_scorer.score_candidate(ctx, {})["final_score"]


def test_scorer_penalizes_disclaimer_heavy(tmp_path: Path):
    from video_agent.shorts import candidate_scorer
    disc = {
        "scene_ids": ["s"],
        "narration": "Este contenido es informativo y no sustituye la opinión de un profesional. Consulta siempre a tu médico antes de cualquier cambio.",
        "layouts": [],
        "visual_prompt": "v",
    }
    out = candidate_scorer.score_candidate(disc, {})
    assert "disclaimer_heavy" in out["penalties"]


def test_scorer_classify_thresholds():
    from video_agent.shorts import candidate_scorer
    assert candidate_scorer.classify(80) == "strong"
    assert candidate_scorer.classify(70) == "acceptable"
    assert candidate_scorer.classify(50) == "reject"


# --------------------------------------------------------------------------
# music selector
# --------------------------------------------------------------------------

def test_music_selector_maps_pillar_to_track():
    from video_agent.shorts import music_selector
    cfg = {}
    assert music_selector.select_music_track("movement", cfg) == "shorts_movement"
    assert music_selector.select_music_track("food", cfg) == "shorts_daily_habit"
    assert music_selector.select_music_track("sleep", cfg) == "shorts_sleep_stress"
    assert music_selector.select_music_track("stress", cfg) == "shorts_sleep_stress"
    assert music_selector.select_music_track("energy", cfg) == "shorts_sleep_stress"
    assert music_selector.select_music_track("menopause", cfg) == "shorts_sleep_stress"
    assert music_selector.select_music_track("sleep_deep", cfg) == "shorts_deep_calm"
    assert music_selector.select_music_track("reflective", cfg) == "shorts_deep_calm"
    assert music_selector.select_music_track("night", cfg) == "shorts_deep_calm"
    assert music_selector.select_music_track("unknown_pillar", cfg) == "shorts_sleep_stress"


# --------------------------------------------------------------------------
# planner
# --------------------------------------------------------------------------

def _cfg():
    return {
        "shorts": {
            "auto_generate": {"default_count": 3, "max_count": 5, "allow_fewer_if_candidates_are_weak": True},
            "content": {"default_formats_per_long": ["pain_to_tip", "mistake_to_avoid", "mini_checklist"]},
            "tts": {"provider": "kokoro", "voice_id": "ef_dora", "speed": 1.07, "pace_wpm": 130},
        }
    }


def test_planner_detects_pillar_movement(tmp_path: Path):
    from video_agent.shorts import planner
    scenes = [_scene(i, "Camina y muévete suave para tus rodillas, evita el dolor.", offset=i * 15.0) for i in range(1, 8)]
    job = _write_long(tmp_path, scenes, title="Cómo moverte y caminar después de los 45")
    plan = planner.plan_shorts_from_long_video(job, _cfg())
    assert plan["detected_pillar"] == "movement"
    assert plan["music_enabled"] is True


def test_planner_picks_up_to_three_strong_shorts(tmp_path: Path):
    from video_agent.shorts import planner
    scenes = [
        _scene(i, f"¿Te cuesta dormir después de los 45? Evita la pantalla y haz esta pausa corta {i} para descansar.", layout="short_pain", offset=i * 15.0)
        for i in range(1, 9)
    ]
    job = _write_long(tmp_path, scenes, title="Dormir mejor después de los 45")
    plan = planner.plan_shorts_from_long_video(job, _cfg())
    sel = plan["selected_shorts"]
    assert 1 <= len(sel) <= 3
    assert sel[0]["short_id"].startswith("short-01")
    assert sel[0]["format"] == "pain_to_tip"
    assert sel[0]["voice_preset"]["voice_id"] == "ef_dora" or plan["voice_preset"]["voice_id"] == "ef_dora"
    assert sel[0]["music_track"] == "shorts_sleep_stress"


def test_planner_assigns_distinct_formats_in_order(tmp_path: Path):
    from video_agent.shorts import planner
    scenes = [
        _scene(i, f"¿Te duele moverte? Evita esto {i} y haz esta pausa corta para caminar sin dolor.", layout="short_pain", offset=i * 15.0)
        for i in range(1, 12)
    ]
    job = _write_long(tmp_path, scenes, title="Movimiento después de los 45")
    plan = planner.plan_shorts_from_long_video(job, _cfg())
    formats = [s["format"] for s in plan["selected_shorts"]]
    assert formats[: len(formats)] == ["pain_to_tip", "mistake_to_avoid", "mini_checklist"][: len(formats)]


def test_prompt_updates():
    from video_agent.shorts import prompts
    p_scene = prompts.short_scene_prompt_v6({}, {}, {})
    assert "SCENE NARRATION WORD CAPS" in p_scene
    p_qa = prompts.gemini_scenes_qa_prompt({}, {}, {})
    assert "product_scores" in p_qa
