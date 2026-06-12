from __future__ import annotations

from .conftest import *  # noqa: F401,F403

def test_defensive_product_scores_validation():
    from video_agent.shorts.qa import normalize_gemini_scenes_qa
    
    # 1. All scores good (average >= 8.9, all >= 9.0 / saveability >= 8.5)
    parsed_good = {
        "verdict": "FAIL", # should get updated to PASS if only warnings/scores were failing (but wait, no issues are present here)
        "issues": [],
        "required_changes": [],
        "product_scores": {
            "audience_fit_45_plus": "9/10",
            "hook_strength": 9,
            "visual_specificity": 9.0,
            "clarity": "9",
            "retention_pacing": 9,
            "natural_spanish": "9",
            "saveability": 8.5
        }
    }
    res_good = normalize_gemini_scenes_qa(parsed_good)
    assert res_good["verdict"] == "PASS" # since parsed verdict was FAIL but no issues exist and scores are perfect
    assert not any(i["type"] == "product_quality_score_low" for i in res_good["issues"])

    # 2. Missing score key
    parsed_missing = {
        "verdict": "PASS",
        "issues": [],
        "required_changes": [],
        "product_scores": {
            "audience_fit_45_plus": 9,
            "hook_strength": 9,
        }
    }
    res_missing = normalize_gemini_scenes_qa(parsed_missing)
    assert res_missing["verdict"] == "FAIL"
    assert any(i["type"] == "product_quality_scores_missing" for i in res_missing["issues"])

    # 3. Individual score > 6.0 but < 9.0 (downgraded to warning, no FAIL)
    parsed_low = {
        "verdict": "PASS",
        "issues": [],
        "required_changes": [],
        "product_scores": {
            "audience_fit_45_plus": 9,
            "hook_strength": 8, # < 9.0 but > 6.0
            "visual_specificity": 9,
            "clarity": 9,
            "retention_pacing": 9,
            "natural_spanish": 9,
            "saveability": 8.5
        }
    }
    res_low = normalize_gemini_scenes_qa(parsed_low)
    assert res_low["verdict"] == "PASS"
    assert not any(i["type"] == "product_quality_score_low" for i in res_low["issues"])

    # 4. Average score too low (< 8.9) but all > 6.0 (downgraded to warning, no FAIL)
    parsed_low_avg = {
        "verdict": "PASS",
        "issues": [],
        "required_changes": [],
        "product_scores": {
            "audience_fit_45_plus": 8.5,
            "hook_strength": 8.5,
            "visual_specificity": 8.5,
            "clarity": 8.5,
            "retention_pacing": 8.5,
            "natural_spanish": 8.5,
            "saveability": 8.5 # all 8.5s -> avg 8.5 < 8.9
        }
    }
    res_low_avg = normalize_gemini_scenes_qa(parsed_low_avg)
    assert res_low_avg["verdict"] == "PASS"
    assert not any(i["type"] == "product_quality_average_low" for i in res_low_avg["issues"])

    # 5. Individual score <= 6.0 (hard failure)
    parsed_very_low = {
        "verdict": "PASS",
        "issues": [],
        "required_changes": [],
        "product_scores": {
            "audience_fit_45_plus": 9,
            "hook_strength": 5, # <= 6.0
            "visual_specificity": 9,
            "clarity": 9,
            "retention_pacing": 9,
            "natural_spanish": 9,
            "saveability": 8.5
        }
    }
    res_very_low = normalize_gemini_scenes_qa(parsed_very_low)
    assert res_very_low["verdict"] == "FAIL"
    assert any(i["type"] == "product_quality_score_low" for i in res_very_low["issues"])




def test_soft_warning_does_not_trigger_excess_retry(tmp_path: Path):
    """Fix C4: deterministic scene validation PASS + LLM QA WARN must not loop."""
    from video_agent.shorts import short_builder
    job = _long_job(tmp_path)
    calls: list[str] = []
    qa_calls = {"n": 0}

    def gemini_fn(prompt: str, **kwargs):
        if "Scenes QA reviewer" in prompt:
            qa_calls["n"] += 1
            return json.dumps({
                "verdict": "WARN",
                "issues": [],
                "required_changes": [],
                "warnings": ["Mild pacing preference, not blocking."],
                "product_scores": _scene_qa_scores(),
            })
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []})

    plan = {"short_id": "short-warn-noretry", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    res = short_builder.build_short(job, plan, _cfg(), llm_fn=_llm_fn_factory(), gemini_fn=gemini_fn, **_stub_io(calls))

    assert res["status"] == "rendered"
    assert qa_calls["n"] == 1, "WARN scene QA must not trigger a regeneration loop"


def test_short_scene_retry_cap_on_soft_issues(tmp_path: Path):
    """Fix C4/C6#3: repeated soft QA warnings must not exceed 2 scene attempts."""
    from video_agent.shorts import short_builder
    job = _long_job(tmp_path)
    calls: list[str] = []
    qa_calls = {"n": 0}

    def gemini_fn(prompt: str, **kwargs):
        if "Scenes QA reviewer" in prompt:
            qa_calls["n"] += 1
            return json.dumps({
                "verdict": "WARN",
                "issues": [],
                "required_changes": [],
                "warnings": ["Soft preference."],
                "product_scores": _scene_qa_scores(),
            })
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []})

    plan = {"short_id": "short-cap", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    res = short_builder.build_short(job, plan, _cfg(), llm_fn=_llm_fn_factory(), gemini_fn=gemini_fn, **_stub_io(calls))

    assert res["status"] == "rendered"
    assert qa_calls["n"] <= 2


def test_high_score_qa_fail_without_hard_issue_forces_warn(tmp_path: Path):
    """Spec: a Gemini scene-QA FAIL with high product scores and only soft
    suggestions must NOT trigger a regeneration storm — force WARN + render."""
    from video_agent.shorts import short_builder
    job = _long_job(tmp_path)
    calls: list[str] = []
    qa_calls = {"n": 0}

    def gemini_fn(prompt: str, **kwargs):
        if "Scenes QA reviewer" in prompt:
            qa_calls["n"] += 1
            return json.dumps({
                "verdict": "FAIL",
                "issues": [{"type": "hook_polish", "severity": "warning",
                            "detail": "Hook could be a touch sharper."}],
                "required_changes": [],
                "warnings": ["Consider a sharper hook."],
                "product_scores": _scene_qa_scores(),  # all >= 9
            })
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []})

    plan = {"short_id": "short-hiscore-fail", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    res = short_builder.build_short(job, plan, _cfg(), llm_fn=_llm_fn_factory(), gemini_fn=gemini_fn, **_stub_io(calls))

    assert res["status"] == "rendered", res["status"]
    assert qa_calls["n"] <= 2, qa_calls["n"]  # no regeneration storm


def test_retry_hash_collapse_stops_loop(tmp_path: Path):
    """Spec §8: if the scene generator keeps emitting the same normalized output
    while QA keeps failing, stop the loop instead of burning the whole budget."""
    from video_agent.shorts import short_builder
    job = _long_job(tmp_path)
    calls: list[str] = []
    scene_gen = {"n": 0}
    fixed_scenes = {**_GOOD_SCENES, "short_id": "short-collapse"}

    def llm_fn(kind, prompt):
        if kind == "script":
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            scene_gen["n"] += 1
            return json.dumps(fixed_scenes)  # identical every time
        return json.dumps({"title": "t", "description": "d", "hashtags": [], "pinned_comment": "p"})

    def gemini_fn(prompt: str, **kwargs):
        if "Scenes QA reviewer" in prompt:
            return json.dumps({
                "verdict": "FAIL",
                "issues": [{"type": "retention_pacing", "severity": "repairable_error",
                            "detail": "Merge repeated scenes."}],
                "required_changes": ["Merge repeated scenes."],
                "product_scores": _scene_qa_scores(),
            })
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []})

    plan = {"short_id": "short-collapse", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    short_builder.build_short(job, plan, _cfg(), llm_fn=llm_fn, gemini_fn=gemini_fn, **_stub_io(calls))

    # Identical output detected → loop collapses early instead of exhausting budget.
    assert scene_gen["n"] <= 2, scene_gen["n"]


def test_constants_exist():
    import video_agent.shorts.short_builder as sb
    assert sb.MAX_QA_RETRIES_PER_STAGE == 1
    assert sb.MAX_SCENE_REGEN_ATTEMPTS == 2
    assert sb.MAX_SCRIPT_REGEN_ATTEMPTS == 1
    assert sb.MAX_PROVIDER_RETRIES_PER_CALL == 3


def test_check_and_apply_auto_pass_forces_warn():
    from video_agent.shorts.short_builder import check_and_apply_auto_pass
    qa_result = {
        "verdict": "FAIL",
        "issues": [{"type": "product_quality_score_low", "detail": "retention pacing is 8.0", "severity": "minor"}],
        "product_scores": {
            "hook_strength": 9.0,
            "clarity": 9.0,
            "retention_pacing": 8.0,
            "visual_specificity": 9.0,
            "audience_fit_45_plus": 9.0,
            "natural_spanish": 9.0,
            "saveability": 9.0,
        }
    }
    assert check_and_apply_auto_pass(qa_result) is True
    assert qa_result["verdict"] == "WARN"


def test_script_retry_hash_collapse_stops_loop(tmp_path: Path):
    """Spec §8: if the script generator keeps emitting the same normalized output
    while QA keeps failing, stop the loop instead of burning the whole budget."""
    from video_agent.shorts import short_builder
    job = _long_job(tmp_path)
    calls: list[str] = []
    script_gen = {"n": 0}
    fixed_script = dict(_GOOD_SCRIPT)

    def llm_fn(kind, prompt):
        if kind == "script":
            script_gen["n"] += 1
            return json.dumps(fixed_script)  # identical every time
        if kind == "scenes":
            return json.dumps(_GOOD_SCENES)
        return json.dumps({"title": "t", "description": "d", "hashtags": [], "pinned_comment": "p"})

    def gemini_fn(prompt: str, **kwargs):
        if "Script QA reviewer" in prompt:
            return json.dumps({
                "verdict": "FAIL",
                "issues": [{"type": "safety", "severity": "major",
                            "detail": "Safety issue detail"}],
                "required_changes": ["Remove unsafe claim"],
            })
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []})

    plan = {"short_id": "short-script-collapse", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
            
    # Should stop after the second attempt due to collapse detection and fail
    res = short_builder.build_short(job, plan, _cfg(), llm_fn=llm_fn, gemini_fn=gemini_fn, **_stub_io(calls))
    assert res["status"] == "needs_review", res["status"]
    assert script_gen["n"] <= 2, script_gen["n"]


def test_provider_error_does_not_count_as_qa_retry(tmp_path: Path):
    """Spec: provider errors must be retried up to 3 times, and provider retries
    do NOT count as QA regeneration attempts."""
    from video_agent.shorts import short_builder
    job = _long_job(tmp_path)
    calls: list[str] = []
    
    script_gen = {"n": 0}
    
    def llm_fn(kind, prompt):
        if kind == "script":
            script_gen["n"] += 1
            if script_gen["n"] == 1:
                # First attempt returns provider error text
                return "Something went wrong, please contact us."
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            return json.dumps(_GOOD_SCENES)
        return json.dumps({"title": "t", "description": "d", "hashtags": [], "pinned_comment": "p"})

    plan = {"short_id": "short-provider-fail", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
            
    res = short_builder.build_short(job, plan, _cfg(), llm_fn=llm_fn, gemini_fn=None, **_stub_io(calls))
    assert res["status"] == "rendered", res["status"]
    # Total script QA attempts should be 1, because the provider error was retried internally
    assert res["regeneration_attempts"] == 0


def test_qa_decision_summary_written_on_warn_continuation(tmp_path: Path):
    from video_agent.shorts import short_builder, paths
    import json

    job = _long_job(tmp_path)
    calls: list[str] = []
    qa_attempts = {"n": 0}
    scenes_calls = {"n": 0}

    def llm_fn(kind, prompt):
        if kind == "script":
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            scenes_calls["n"] += 1
            if scenes_calls["n"] == 1:
                return json.dumps(_GOOD_SCENES)
            else:
                scenes_alt = dict(_GOOD_SCENES)
                scenes_alt["scenes"] = [dict(s) for s in _GOOD_SCENES["scenes"]]
                scenes_alt["scenes"][0]["motion"] = "push_in"
                scenes_alt["scenes"][0]["caption"] = "alternative caption"
                return json.dumps(scenes_alt)
        if kind == "seo":
            return json.dumps({"title": "d", "description": "d", "hashtags": []})
        return "{}"

    def gemini_fn(prompt: str, **kwargs):
        if "Scenes QA reviewer" in prompt:
            qa_attempts["n"] += 1
            if qa_attempts["n"] == 1:
                # Attempt 1: Hard blocker to force retry/regeneration
                return json.dumps({
                    "verdict": "FAIL",
                    "issues": [{"type": "medical_overclaim", "severity": "major", "detail": "medical overclaim"}],
                    "required_changes": ["Remove medical overclaims"],
                    "warnings": [],
                    "product_scores": {
                        "audience_fit_45_plus": 8,
                        "hook_strength": 8,
                        "visual_specificity": 8,
                        "clarity": 8,
                        "retention_pacing": 8,
                        "natural_spanish": 8,
                        "saveability": 8,
                    },
                })
            else:
                # Attempt 2: Soft warnings only to verify warn continuation
                return json.dumps({
                    "verdict": "FAIL",
                    "issues": [{"type": "weak_hook_motion", "severity": "minor", "detail": "weak hook motion"}],
                    "required_changes": [],
                    "warnings": ["weak hook motion"],
                    "product_scores": {
                        "audience_fit_45_plus": 10,
                        "hook_strength": 10,
                        "visual_specificity": 10,
                        "clarity": 10,
                        "retention_pacing": 10,
                        "natural_spanish": 10,
                        "saveability": 10,
                    },
                })
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": []})

    plan = {"short_id": "short-qa-dec-summary", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "x"}

    cfg_override = _cfg()
    cfg_override["shorts"]["autopilot"]["max_regeneration_attempts"] = 1

    res = short_builder.build_short(
        job,
        plan,
        cfg_override,
        llm_fn=llm_fn,
        gemini_fn=gemini_fn,
        **_stub_io(calls),
    )

    sd = paths.short_dir(job, "short-qa-dec-summary")
    summary_path = sd / "json" / paths.SHORT_QA_DECISION_SUMMARY_FILE
    assert summary_path.exists()
    
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["stage"] == "qa_scenes"
    assert summary["decision"] == "continued_with_warn"
    assert summary["renderable"] is True
    assert summary["continued_to_render"] is True
    assert summary["attempts_used"] == 2
    assert len(summary["remaining_warnings"]) > 0
    assert len(summary["remaining_blockers"]) == 0

    assert res["status"] in ("ready_for_render", "rendered")


def test_script_hard_fail_terminal_sets_explicit_failure_and_decision(tmp_path: Path):
    """Script QA that never passes after max attempts must end with an explicit
    structured hard-blocker decision — never a bare needs_review that forces the
    UI to print the generic "max regeneration attempts" message."""
    from video_agent.shorts import short_builder, paths

    job = _long_job(tmp_path)
    calls: list[str] = []
    attempts = {"n": 0}
    bad_script = {**_GOOD_SCRIPT, "hook": "Hola a todos", "narration": "Hola, bienvenidos. Hoy vamos a hablar."}

    def llm_fn(kind, prompt):
        if kind == "script":
            attempts["n"] += 1
            return json.dumps({**bad_script, "hook": f"Hola a todos {attempts['n']}"})
        if kind == "scenes":
            return json.dumps(_GOOD_SCENES)
        return json.dumps({"title": "t", "description": "d", "hashtags": [], "pinned_comment": "p"})

    plan = {"short_id": "short-script-hard", "format": "mistake_to_avoid", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress", "narration_seed": "x"}
    res = short_builder.build_short(job, plan, _cfg(), llm_fn=llm_fn, **_stub_io(calls))

    assert res["status"] == "needs_review"
    assert res["qa_verdict"] == "FAIL"
    assert res["failure_stage"] == "qa_script"
    assert res.get("failure_reason")
    assert "render" not in calls

    sd = paths.short_dir(job, "short-script-hard")
    summary_path = sd / "json" / paths.SHORT_QA_DECISION_SUMMARY_FILE
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["decision"] == "failed_hard_blocker"
    assert summary["renderable"] is False
    assert summary["continued_to_render"] is False
    assert len(summary["remaining_blockers"]) > 0


def test_qa_decision_summary_written_on_hard_failure(tmp_path: Path):
    from video_agent.shorts import short_builder, paths
    import json

    job = _long_job(tmp_path)
    calls: list[str] = []
    qa_attempts = {"n": 0}
    scenes_calls = {"n": 0}

    def llm_fn(kind, prompt):
        if kind == "script":
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            scenes_calls["n"] += 1
            if scenes_calls["n"] == 1:
                return json.dumps(_GOOD_SCENES)
            else:
                scenes_alt = dict(_GOOD_SCENES)
                scenes_alt["scenes"] = [dict(s) for s in _GOOD_SCENES["scenes"]]
                scenes_alt["scenes"][0]["motion"] = "push_in"
                scenes_alt["scenes"][0]["caption"] = "alternative caption"
                return json.dumps(scenes_alt)
        if kind == "seo":
            return json.dumps({"title": "d", "description": "d", "hashtags": []})
        return "{}"

    # Return hard blocker (e.g. medical disclaimer overclaim / safety) in both attempts
    def gemini_fn(prompt: str, **kwargs):
        if "Scenes QA reviewer" in prompt:
            qa_attempts["n"] += 1
            return json.dumps({
                "verdict": "FAIL",
                "issues": [{"type": "medical_overclaim", "severity": "major", "detail": "medical overclaim"}],
                "required_changes": ["Remove medical overclaims"],
                "warnings": [],
                "product_scores": {
                    "audience_fit_45_plus": 8,
                    "hook_strength": 8,
                    "visual_specificity": 8,
                    "clarity": 8,
                    "retention_pacing": 8,
                    "natural_spanish": 8,
                    "saveability": 8,
                },
            })
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": []})

    plan = {"short_id": "short-qa-dec-hard-fail", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "x"}

    cfg_override = _cfg()
    cfg_override["shorts"]["autopilot"]["max_regeneration_attempts"] = 1

    res = short_builder.build_short(
        job,
        plan,
        cfg_override,
        llm_fn=llm_fn,
        gemini_fn=gemini_fn,
        **_stub_io(calls),
    )

    sd = paths.short_dir(job, "short-qa-dec-hard-fail")
    summary_path = sd / "json" / paths.SHORT_QA_DECISION_SUMMARY_FILE
    assert summary_path.exists()
    
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["stage"] == "qa_scenes"
    assert summary["decision"] == "failed_hard_blocker"
    assert summary["renderable"] is False
    assert summary["continued_to_render"] is False
    assert summary["attempts_used"] == 2
    assert len(summary["remaining_blockers"]) > 0

    assert res["status"] == "needs_review"
    assert res["qa_verdict"] == "FAIL"








def test_soft_qa_warning_does_not_regenerate(tmp_path: Path):
    """Spec §11.1 — deterministic PASS + LLM QA warning only → no regeneration."""
    from video_agent.shorts import short_builder
    job = _long_job(tmp_path)
    calls: list[str] = []
    qa_calls = {"n": 0}

    def gemini_fn(prompt: str, **kwargs):
        if "Scenes QA reviewer" in prompt:
            qa_calls["n"] += 1
            return json.dumps({
                "verdict": "WARN", "issues": [], "required_changes": [],
                "warnings": ["Hook could be sharper."], "product_scores": _scene_qa_scores(),
            })
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []})

    plan = {"short_id": "short-softwarn", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    res = short_builder.build_short(job, plan, _cfg(), llm_fn=_llm_fn_factory(), gemini_fn=gemini_fn, **_stub_io(calls))
    assert res["status"] == "rendered"
    assert qa_calls["n"] == 1


def test_hard_schema_fail_regenerates_once(tmp_path: Path):
    """Spec §11.2 — invalid scenes JSON regenerates scenes (at least once)."""
    from video_agent.shorts import short_builder
    job = _long_job(tmp_path)
    calls: list[str] = []
    scene_gen = {"n": 0}

    def llm_fn(kind, prompt):
        if kind == "script":
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            scene_gen["n"] += 1
            if scene_gen["n"] == 1:
                return "{not valid json"  # hard schema fail
            return json.dumps({**_GOOD_SCENES, "short_id": "short-schema"})
        return json.dumps({"title": "t", "description": "d", "hashtags": [], "pinned_comment": "p"})

    plan = {"short_id": "short-schema", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    short_builder.build_short(job, plan, _cfg(), llm_fn=llm_fn, gemini_fn=lambda p, **k: json.dumps(
        {"verdict": "PASS", "issues": [], "required_changes": [], "warnings": [], "product_scores": _scene_qa_scores()}),
        **_stub_io(calls))
    assert scene_gen["n"] >= 2  # regenerated after the invalid JSON


def test_retry_scope_scenes_only(tmp_path: Path):
    """Spec §11.4 — a scene-QA hard fail must not regenerate the script."""
    from video_agent.shorts import short_builder
    job = _long_job(tmp_path)
    calls: list[str] = []
    script_gen = {"n": 0}

    def llm_fn(kind, prompt):
        if kind == "script":
            script_gen["n"] += 1
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            return json.dumps({**_GOOD_SCENES, "short_id": "short-scope"})
        return json.dumps({"title": "t", "description": "d", "hashtags": [], "pinned_comment": "p"})

    qa = {"n": 0}
    def gemini_fn(prompt: str, **kwargs):
        if "Scenes QA reviewer" in prompt:
            qa["n"] += 1
            # one hard fail, then pass — exercises scene-only retry
            if qa["n"] == 1:
                low = {k: 5 for k in _scene_qa_scores()}
                return json.dumps({"verdict": "FAIL", "issues": [{"type": "retention_pacing",
                    "severity": "repairable_error", "detail": "weak"}],
                    "required_changes": ["Improve pacing."], "product_scores": low})
            return json.dumps({"verdict": "PASS", "issues": [], "required_changes": [],
                               "warnings": [], "product_scores": _scene_qa_scores()})
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []})

    plan = {"short_id": "short-scope", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    short_builder.build_short(job, plan, _cfg(), llm_fn=llm_fn, gemini_fn=gemini_fn, **_stub_io(calls))
    assert script_gen["n"] == 1  # script generated once; scene retry did not touch it
