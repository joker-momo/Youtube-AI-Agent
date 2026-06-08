from pathlib import Path

import pytest

from video_agent.shorts import llm_history


def test_recorder_logs_prompt_and_response(tmp_path: Path):
    rec = llm_history.LLMHistoryRecorder(tmp_path / "json" / "llm_history.jsonl")
    fn = rec.wrap(lambda prompt: f"echo:{prompt}", "chatgpt")
    assert fn("hola") == "echo:hola"

    hist = llm_history.read_history(tmp_path / "json" / "llm_history.jsonl")
    assert len(hist) == 1
    assert hist[0]["provider"] == "chatgpt"
    assert hist[0]["prompt"] == "hola"
    assert hist[0]["response"] == "echo:hola"
    assert hist[0]["ok"] is True
    assert hist[0]["seq"] == 1


def test_recorder_supports_two_arg_call_shape(tmp_path: Path):
    rec = llm_history.LLMHistoryRecorder(tmp_path / "h.jsonl")
    fn = rec.wrap(lambda kind, prompt: "ok", "chatgpt")
    # mimic _invoke's legacy 2-arg fallback
    assert fn("scenes", "PROMPT") == "ok"
    hist = llm_history.read_history(tmp_path / "h.jsonl")
    assert hist[0]["kind"] == "scenes"
    assert hist[0]["prompt"] == "PROMPT"


def test_recorder_logs_failures_then_reraises(tmp_path: Path):
    rec = llm_history.LLMHistoryRecorder(tmp_path / "h.jsonl")
    calls = {"n": 0}

    def flaky(prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("QA rejected")
        return "second-ok"

    fn = rec.wrap(flaky, "chatgpt")
    with pytest.raises(RuntimeError):
        fn("attempt-1")
    assert fn("attempt-2") == "second-ok"

    hist = llm_history.read_history(tmp_path / "h.jsonl")
    assert len(hist) == 2  # failed attempt IS recorded
    assert hist[0]["ok"] is False
    assert "QA rejected" in hist[0]["error"]
    assert hist[0]["response"] is None
    assert hist[1]["ok"] is True
    assert [h["seq"] for h in hist] == [1, 2]


def test_render_markdown_includes_fails_and_summary(tmp_path: Path):
    rec = llm_history.LLMHistoryRecorder(tmp_path / "h.jsonl")
    rec.wrap(lambda p: "ok", "chatgpt")("write the script")

    def boom(p):
        raise RuntimeError("QA rejected scene 3")

    with pytest.raises(RuntimeError):
        rec.wrap(boom, "gemini", default_kind="qa_scenes")("check scenes")

    md = llm_history.render_markdown(
        llm_history.read_history(tmp_path / "h.jsonl"), short_id="short-09"
    )
    assert "# LLM Prompt History — short-09" in md
    assert "**Total calls:** 2" in md
    assert "**Failed:** 1" in md
    assert "❌ FAIL" in md
    assert "QA rejected scene 3" in md
    assert "write the script" in md  # full prompt preserved


def test_render_markdown_escapes_nested_fences(tmp_path: Path):
    rec = llm_history.LLMHistoryRecorder(tmp_path / "h.jsonl")
    rec.wrap(lambda p: "resp with ``` fence", "chatgpt")("prompt ``` inside")
    md = llm_history.render_markdown(llm_history.read_history(tmp_path / "h.jsonl"))
    # no raw triple-backtick from content should break the outer fence
    assert "``` inside" not in md
    assert "ʼʼʼ" in md


def test_recorder_separates_providers_in_order(tmp_path: Path):
    rec = llm_history.LLMHistoryRecorder(tmp_path / "h.jsonl")
    chat = rec.wrap(lambda p: "c", "chatgpt")
    gem = rec.wrap(lambda p: "g", "gemini", default_kind="qa")
    chat("script prompt")
    gem("qa scenes prompt")
    chat("scenes prompt RETRY FEEDBACK")
    hist = llm_history.read_history(tmp_path / "h.jsonl")
    assert [h["provider"] for h in hist] == ["chatgpt", "gemini", "chatgpt"]
    assert [h["seq"] for h in hist] == [1, 2, 3]
    assert hist[1]["kind"] == "qa"


def test_recorder_logs_deterministic_events(tmp_path: Path):
    rec = llm_history.LLMHistoryRecorder(tmp_path / "h.jsonl")

    rec.record_event("deterministic", "scene_validation", {"verdict": "FAIL", "issues": ["duration_cap"]})

    hist = llm_history.read_history(tmp_path / "h.jsonl")
    assert len(hist) == 1
    assert hist[0]["provider"] == "deterministic"
    assert hist[0]["kind"] == "scene_validation"
    assert hist[0]["ok"] is True
    assert hist[0]["payload"]["issues"] == ["duration_cap"]


def test_render_markdown_uses_payload_verdict_for_stage_events(tmp_path: Path):
    rec = llm_history.LLMHistoryRecorder(tmp_path / "h.jsonl")

    rec.record_event("deterministic", "stage_status", {"stage": "qa_script", "status": "completed", "verdict": "PASS"})
    rec.record_event(
        "deterministic",
        "stage_status",
        {"stage": "qa_scenes", "status": "failed", "verdict": "FAIL", "error": "Scene QA rejected layout."},
        ok=False,
    )

    md = llm_history.render_markdown(llm_history.read_history(tmp_path / "h.jsonl"))

    assert "qa_script" in md
    assert "✅ PASS" in md
    assert "qa_scenes" in md
    assert "❌ FAIL" in md
    assert "**Reason:** Scene QA rejected layout." in md
