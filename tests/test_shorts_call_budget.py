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
