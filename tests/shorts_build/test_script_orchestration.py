from __future__ import annotations

import json
from pathlib import Path
from video_agent.shorts import short_builder, paths
from video_agent.shorts.short_builder import BuildContext, StageSignal

from unittest.mock import MagicMock

def _stub_ctx(tmp_path: Path, script_attempts: int, previous_script: dict | None) -> MagicMock:
    job = tmp_path / "long" / "job_id"
    job.mkdir(parents=True, exist_ok=True)
    sd = job / "shorts" / "short-test"
    jd = sd / "json"
    jd.mkdir(parents=True, exist_ok=True)
    
    if previous_script:
        (jd / "short_script.json").write_text(json.dumps(previous_script))
        
    class DummyRecorder:
        def record_event(self, *args, **kwargs):
            pass

    ctx = MagicMock()
    ctx.short_plan = {"short_id": "short-test", "format": "pain_to_tip"}
    ctx.plan_for_prompt = ctx.short_plan
    ctx.long_job_dir = job
    ctx.json_dir = jd
    ctx.channel_config = {}
    ctx.update_stage = lambda *args, **kwargs: None
    ctx.check_stop = lambda *args, **kwargs: None
    ctx.recorder = DummyRecorder()
    ctx.llm_fn = lambda *args, **kwargs: ""
    ctx.gemini_fn = lambda *args, **kwargs: ""
    ctx.music_track = "track"
    ctx.max_regen = 2
    ctx.long_video_url = ""
    ctx.source_artifacts = {}
    ctx.status = {"status": "in_progress", "stages": []}
    
    ctx.extras = {
        "script_attempts": script_attempts,
        "script_feedback": "",
        "prev_script_hash": None,
        "retention_plan": {},
        "script_retry_memory": {"active_issues": {}, "suppressed_issues": {}},
        "script_memory_file": jd / "script_retry_memory.json"
    }
    if previous_script:
        ctx.extras["short_script"] = previous_script
        
    return ctx

def test_stage_script_rejects_partial_retry_without_overwriting_previous_script(tmp_path: Path, monkeypatch):
    from video_agent.shorts import short_script_builder
    
    previous_script = {
        "beats": [
            {"time_sec": "0-3", "purpose": "hook", "narration": "¿Te pasa esto?"},
            {"time_sec": "3-8", "purpose": "setup"},
            {"time_sec": "8-15", "purpose": "payoff"},
            {"time_sec": "15-20", "purpose": "payoff"},
            {"time_sec": "20-25", "purpose": "cta"}
        ],
        "cta": "Vídeo completo en el canal."
    }
    
    partial_candidate = {
        "beats": [
            {"time_sec": "8-13", "purpose": "payoff"}
        ]
    }
    
    # Mock builder to return the partial candidate
    monkeypatch.setattr(
        short_script_builder, 
        "build_short_script", 
        lambda *args, **kwargs: partial_candidate
    )
    
    ctx = _stub_ctx(tmp_path, script_attempts=2, previous_script=previous_script)
    result = short_builder._stage_script(ctx)
    
    assert result.signal == StageSignal.RESTART_SCRIPT
    # Ensure file on disk was NOT overwritten by partial candidate
    disk_script = json.loads((ctx.json_dir / "short_script.json").read_text())
    assert disk_script == previous_script
    assert "CRITICAL SYSTEM REJECTION" in ctx.extras["script_feedback"]

def test_stage_script_rejects_partial_first_attempt_before_write(tmp_path: Path, monkeypatch):
    from video_agent.shorts import short_script_builder
    
    partial_candidate = {
        "beats": [
            {"time_sec": "8-13", "purpose": "payoff"}
        ]
    }
    
    monkeypatch.setattr(
        short_script_builder, 
        "build_short_script", 
        lambda *args, **kwargs: partial_candidate
    )
    
    ctx = _stub_ctx(tmp_path, script_attempts=1, previous_script=None)
    result = short_builder._stage_script(ctx)
    
    assert result.signal == StageSignal.RESTART_SCRIPT
    # Should not have written the script
    assert not (ctx.json_dir / "short_script.json").exists()
    assert "CRITICAL SYSTEM REJECTION" in ctx.extras["script_feedback"]
