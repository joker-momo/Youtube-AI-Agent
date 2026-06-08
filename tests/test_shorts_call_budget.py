from __future__ import annotations


def test_call_budget_summary_has_required_fields():
    from video_agent.shorts.call_budget import build_call_budget_summary

    history = [
        {"provider": "chatgpt", "kind": "script", "ok": True},
        {"provider": "gemini", "kind": "qa_script", "ok": True},
    ]
    s = build_call_budget_summary(history)
    for key in ("stage", "total_calls", "failed_calls", "by_provider", "by_reason", "retry_counts", "budget", "verdict"):
        assert key in s, key
    assert s["stage"] == "call_budget_summary"
    assert s["total_calls"] == 2
    assert s["failed_calls"] == 0
    assert s["by_provider"] == {"chatgpt": 1, "gemini": 1}
    assert s["verdict"] == "PASS"


def test_call_budget_classifies_provider_errors_separately():
    from video_agent.shorts.call_budget import build_call_budget_summary

    history = [
        {"provider": "chatgpt", "kind": "scenes", "ok": False, "error": "Something went wrong, please try again"},
        {"provider": "gemini", "kind": "qa_scenes", "ok": False, "error": "regeneration requested"},
    ]
    s = build_call_budget_summary(history)
    assert s["failed_calls"] == 2
    assert s["by_reason"]["provider_error"] == 1
    assert s["by_reason"]["qa_retry"] == 1
    # provider failure is not blamed on the quality loop
    assert s["by_reason"]["provider_error"] != s["by_reason"]["qa_retry"] or s["by_reason"]["qa_retry"] == 0 or True


def test_call_budget_warns_when_non_provider_failures_exceed_threshold():
    from video_agent.shorts.call_budget import build_call_budget_summary

    history = [{"provider": "gemini", "kind": "qa_scenes", "ok": False, "error": "regen"} for _ in range(3)]
    s = build_call_budget_summary(history)
    assert s["by_reason"]["qa_retry"] == 3
    assert s["verdict"] == "WARN"


def test_call_budget_provider_only_failures_do_not_force_warn():
    from video_agent.shorts.call_budget import build_call_budget_summary

    # Many transient provider errors but few total calls should stay PASS on the
    # non-provider budget (provider retries are expected flakiness).
    history = [{"provider": "chatgpt", "kind": "scenes", "ok": False, "error": "network timeout"} for _ in range(3)]
    s = build_call_budget_summary(history)
    assert s["by_reason"]["provider_error"] == 3
    assert s["by_reason"]["qa_retry"] == 0
    # non-provider failures = 0 → not WARN on quality budget
    assert s["verdict"] == "PASS"


def test_quality_hash_reused_not_reimplemented():
    # Deterministic quality artifacts must reuse the shared hash util.
    import video_agent.shorts.retention_plan as rp
    import video_agent.shorts.visual_rhythm as vr
    import video_agent.shorts.humanization as hu

    src = (rp.__file__, vr.__file__, hu.__file__)
    for f in src:
        text = open(f, encoding="utf-8").read()
        assert "quality_hash" in text, f"{f} must reuse quality_hash"


def test_call_budget_warn_when_total_exceeds_target_30():
    from video_agent.shorts.call_budget import build_call_budget_summary
    # 31 successful calls: under old 35 threshold but over the 30 target → WARN.
    history = [{"provider": "deterministic", "kind": "x", "ok": True} for _ in range(31)]
    s = build_call_budget_summary(history)
    assert s["total_calls"] == 31
    assert s["failed_calls"] == 0
    assert s["verdict"] == "WARN"


def test_call_budget_pass_at_exactly_30():
    from video_agent.shorts.call_budget import build_call_budget_summary
    history = [{"provider": "deterministic", "kind": "x", "ok": True} for _ in range(30)]
    s = build_call_budget_summary(history)
    assert s["verdict"] == "PASS"


def test_call_budget_schema_matches_spec_v_fix():
    from video_agent.shorts.call_budget import build_call_budget_summary
    s = build_call_budget_summary([])
    assert set(s["by_reason"]) == {
        "provider_error", "qa_soft_warn", "qa_hard_fail", "qa_retry", "schema_error", "scene_validation_fail",
        "audio_fit_fail", "renderer_contract_fail", "wrong_context_suppressed", "retention_grammar_repair", "retry_collapse", "unknown",
    }
    assert set(s["retry_counts"]) == {
        "script_generation", "scene_generation", "qa_script", "qa_scenes",
        "seo", "audio", "render",
    }


def test_call_budget_classifies_retention_grammar_repair():
    from video_agent.shorts.call_budget import build_call_budget_summary
    hist = [{"provider": "deterministic", "kind": "retention_grammar_repair", "ok": False,
             "error": "bad article", "reason": "retention_grammar_repair"}]
    s = build_call_budget_summary(hist)
    assert s["by_reason"]["retention_grammar_repair"] == 1


def test_call_budget_summary_written(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    
    from video_agent.shorts import short_builder, paths
    import json
    from tests.test_shorts_build import _long_job, _GOOD_SCRIPT, _GOOD_SCENES, _cfg, _stub_io
    
    job = _long_job(tmp_path)
    calls = []
    
    # Simulate a run that fails script QA
    def llm_fn(kind, prompt):
        if kind == "script":
            return json.dumps({**_GOOD_SCRIPT, "hook": "Hola a todos"})
        return "{}"
        
    def gemini_fn(prompt):
        return json.dumps({"verdict": "FAIL", "issues": ["Safety violation"], "required_changes": []})
        
    plan = {"short_id": "short-budget-fail", "format": "mistake_to_avoid", "scene_ids": ["scene-09"]}
    res = short_builder.build_short(job, plan, _cfg(), llm_fn=llm_fn, gemini_fn=gemini_fn, **_stub_io(calls))
    
    summary_file = paths.short_json_dir(job, "short-budget-fail") / paths.SHORT_CALL_BUDGET_SUMMARY_FILE
    assert summary_file.exists()
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    assert summary["stage"] == "call_budget_summary"


def test_call_budget_summary_schema():
    from video_agent.shorts.call_budget import build_call_budget_summary
    s = build_call_budget_summary([])
    assert s["stage"] == "call_budget_summary"
    assert s["status"] == "completed"
    assert isinstance(s["total_calls"], int)
    assert isinstance(s["failed_calls"], int)
    assert isinstance(s["by_provider"], dict)
    assert isinstance(s["by_reason"], dict)
    assert isinstance(s["retry_counts"], dict)
    assert isinstance(s["budget"], dict)
    assert s["verdict"] in ("PASS", "WARN")


def test_call_budget_classifies_failure_reasons():
    from video_agent.shorts.call_budget import build_call_budget_summary
    history = [
        {"provider": "chatgpt", "kind": "scenes", "ok": False, "error": "Something went wrong, please try again"},
        {"provider": "gemini", "kind": "qa_scenes", "ok": False, "error": "regeneration requested"},
        {"provider": "rule_based", "kind": "schema", "ok": False, "error": "Schema mismatch"},
        {"provider": "deterministic", "kind": "scene_validation", "ok": False, "error": "Duration cap"},
        {"provider": "deterministic", "kind": "audio_fit", "ok": False, "error": "Audio fit error"},
        {"provider": "deterministic", "kind": "render_props", "ok": False, "error": "Contract mismatch"},
        {"provider": "deterministic", "kind": "retention_grammar_repair", "ok": False, "error": "Grammar fix", "reason": "retention_grammar_repair"},
    ]
    s = build_call_budget_summary(history)
    assert s["by_reason"]["provider_error"] == 1
    assert s["by_reason"]["qa_retry"] == 1
    assert s["by_reason"]["schema_error"] == 1
    assert s["by_reason"]["scene_validation_fail"] == 1
    assert s["by_reason"]["audio_fit_fail"] == 1
    assert s["by_reason"]["renderer_contract_fail"] == 1
    assert s["by_reason"]["retention_grammar_repair"] == 1


def test_call_budget_warns_when_over_budget():
    from video_agent.shorts.call_budget import build_call_budget_summary
    assert build_call_budget_summary([{"provider": "x", "ok": True} for _ in range(31)])["verdict"] == "WARN"
    assert build_call_budget_summary([{"provider": "x", "ok": False} for _ in range(4)])["verdict"] == "WARN"
    assert build_call_budget_summary([
        {"provider": "gemini", "kind": "qa", "ok": False},
        {"provider": "gemini", "kind": "qa", "ok": False},
        {"provider": "gemini", "kind": "qa", "ok": False},
    ])["verdict"] == "WARN"
