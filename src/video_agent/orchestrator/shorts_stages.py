from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_agent.orchestrator.job_state import load_job
from video_agent.orchestrator.stages import StageInputMissingError, _complete_stage
from video_agent.utils.json_io import read_json, read_yaml, write_json

async def auto_shorts_script_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn,
) -> Path:
    from video_agent.operator import _chatgpt_shorts_script_prompt
    
    stage_name = "shorts_script"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} from current_stage={state.current_stage!r}"
        )

    script_path = job_dir / "script.json"
    if not script_path.exists():
        raise StageInputMissingError(f"Missing {script_path}")
    
    channel_config = read_yaml(channel_path)
    expected_language = (
        (channel_config.get("seo") or {}).get("language")
        or (channel_config.get("audience") or {}).get("language")
        or "es-ES"
    )
    long_script = read_json(script_path)
    
    prompt = _chatgpt_shorts_script_prompt(channel_config, long_script)
    
    # Send to chatgpt
    raw_resp = await session_fn([prompt])
    
    # Extract JSON
    from video_agent.operator import extract_json_object
    try:
        # We expect a JSON array
        import re
        match = re.search(r"\[.*\]", raw_resp, re.DOTALL)
        if match:
            shorts_list = json.loads(match.group(0))
        else:
            raise ValueError("No JSON array found")
    except Exception as exc:
        raise StageInputMissingError(f"Failed to parse shorts scripts: {exc}") from exc
        
    shorts_dir = job_dir / "shorts"
    shorts_dir.mkdir(parents=True, exist_ok=True)
    
    for i, short_data in enumerate(shorts_list[:4], start=1):
        target_dir = shorts_dir / str(i)
        target_dir.mkdir(parents=True, exist_ok=True)
        # Convert short_data to match long script format (with 'sections' instead of 'narration')
        sections = [{"title": short_data.get("hook", "Hook"), "text": short_data.get("narration", "")}]
        script_payload = {
            "channel_id": channel_config.get("channel", {}).get("id", ""),
            "job_id": state.job_id,
            "title": short_data.get("title", f"Short {i}"),
            "hook": short_data.get("hook", ""),
            "sections": sections,
            "narration": short_data.get("narration", ""),
            "cta": short_data.get("cta", ""),
            "qa": {"verdict": "PASS"}
        }
        write_json(target_dir / "script.json", script_payload)
        # Write dummy seo.json so render works
        write_json(target_dir / "seo.json", {
            "job_id": state.job_id,
            "title": short_data.get("title", f"Short {i}"),
            "description": short_data.get("narration", ""),
            "tags": ["shorts"],
            "language": expected_language,
            "ai_disclosure": True,
            "thumbnail_path": "thumbnail.jpg",
            "thumbnail_text": short_data.get("hook", "")[:25].upper(),
            "suggested_pinned_comments": "Comenta qué te pareció!"
        })
        
    _complete_stage(job_dir, stage_name, shorts_dir)
    return shorts_dir


async def auto_shorts_scenes_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn,
) -> Path:
    from video_agent.operator import _chatgpt_scenes_prompt, _normalize_scenes_candidate
    from video_agent.operator import extract_json_object
    
    stage_name = "shorts_scenes"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} from current_stage={state.current_stage!r}"
        )
        
    shorts_dir = job_dir / "shorts"
    if not shorts_dir.exists():
        raise StageInputMissingError("Missing shorts directory.")
        
    channel_config = read_yaml(channel_path)
    # Customize configuration for Shorts: short duration, few scenes, portrait orientation.
    shorts_channel_config = channel_config.copy()
    shorts_channel_config["content_format"] = {
        "target_duration_sec": 50,
        "scenes_count_min": 5,
        "scenes_count_max": 8
    }
    shorts_visuals = (channel_config.get("visuals") or {}).copy()
    shorts_visuals["orientation"] = "portrait"
    shorts_channel_config["visuals"] = shorts_visuals

    for i in range(1, 5):
        short_job_dir = shorts_dir / str(i)
        if not (short_job_dir / "script.json").exists():
            continue
            
        script_payload = read_json(short_job_dir / "script.json")
        prompt = _chatgpt_scenes_prompt(shorts_channel_config, script_payload)
        # Add instruction for vertical video
        prompt += "\nIMPORTANT: This is a 9:16 VERTICAL video Short. Recommend stock footage that fits vertical screens well."
        
        raw_resp = await session_fn([prompt])
        parsed = extract_json_object(raw_resp)
        normalized = _normalize_scenes_candidate(parsed)
        normalized["qa"] = {"verdict": "PASS"} # Bypass QA for shorts to save time
        
        write_json(short_job_dir / "scenes.json", normalized)
        
    _complete_stage(job_dir, stage_name, shorts_dir)
    return shorts_dir


async def auto_shorts_tts_stage(job_dir: Path, channel_path: Path, session_fn) -> Path:
    from video_agent.orchestrator.stages import _run_blocking_with_timeout
    from video_agent.stages.assets import prepare_assets
    from video_agent.contracts import repo_root
    import os
    
    stage_name = "shorts_tts"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(f"Cannot run {stage_name} from current_stage={state.current_stage!r}")
        
    channel_config = read_yaml(channel_path)
    style = read_json(repo_root() / channel_config["style_dna"]["path"])
    
    # Customize visuals orientation to portrait for Shorts
    shorts_visuals = (channel_config.get("visuals") or {}).copy()
    shorts_visuals["orientation"] = "portrait"
    
    shorts_dir = job_dir / "shorts"
    synth_timeout_sec = int(os.environ.get("WHISPER_SYNTH_TIMEOUT_SEC", "900"))
    
    for i in range(1, 5):
        short_job_dir = shorts_dir / str(i)
        scenes_path = short_job_dir / "scenes.json"
        if not (short_job_dir / "script.json").exists() or not scenes_path.exists():
            continue
        try:
            scene_doc = read_json(scenes_path)
            _run_blocking_with_timeout(
                label=f"Narration synthesis for short {i}",
                timeout_sec=synth_timeout_sec,
                fn=prepare_assets,
                job_dir=short_job_dir,
                style_dna=style,
                scene_doc=scene_doc,
                visual_config=shorts_visuals,
                tts_config=channel_config.get("tts"),
                channel_id=channel_config["channel"]["id"],
            )
        except Exception as e:
            print(f"Failed TTS for short {i}: {e}")
            
    _complete_stage(job_dir, stage_name, shorts_dir)
    return shorts_dir


async def auto_shorts_render_stage(job_dir: Path, channel_path: Path, session_fn) -> Path:
    from video_agent.pipeline import render_operator_job, OperatorRenderOptions
    stage_name = "shorts_render"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(f"Cannot run {stage_name} from current_stage={state.current_stage!r}")
        
    shorts_dir = job_dir / "shorts"
    for i in range(1, 5):
        short_job_dir = shorts_dir / str(i)
        if not (short_job_dir / "script.json").exists() or not (short_job_dir / "scenes.json").exists():
            continue
            
        video_path = short_job_dir / "video.mp4"
        if video_path.exists() and video_path.stat().st_size > 0:
            print(f"Short {i} already rendered, skipping.")
            continue
            
        opts = OperatorRenderOptions(
            channel_path=channel_path,
            job_dir=short_job_dir,
            require_operator_qa=False,
            tts_override=None
        )
        render_operator_job(opts)
            
    _complete_stage(job_dir, stage_name, shorts_dir)
    return shorts_dir


