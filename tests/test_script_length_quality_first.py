"""Quality-first script length contract.

Direction (2026-06-29): the long-form script is the spoken narration. It has a
hard MINIMUM (~11 min) and NO maximum, so ChatGPT develops every idea fully. The
scenes stage must PRESERVE that narration (split into more scenes) instead of
condensing it to a fixed duration. See bug-402.
"""

from __future__ import annotations

from video_agent.operator_prompts import (
    _chatgpt_scenes_batch_prompt,
    _chatgpt_scenes_plan_prompt,
    _chatgpt_scenes_prompt,
    _chatgpt_script_prompt,
)

CFG = {
    "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+"},
    "audience": {"language": "es-ES", "age_range": [45, 75]},
    "content_format": {
        "target_duration_sec": 840,
        "duration_sec_min": 660,  # 11 min floor
        "scenes_count_min": 40,
        "scenes_count_max": 55,
    },
    "tts": {"pace_wpm": 120},
}

SCRIPT = {
    "channel_id": "vida-plena-45",
    "job_id": "j-1",
    "hook": "hook",
    "sections": [],
    "narration": "n",
    "cta": "cta",
}

# floor = 660/60 * 120 = 1320 spoken words
FLOOR_WORDS = round(660 / 60 * 120)


def test_script_prompt_has_minimum_floor_no_maximum():
    prompt = _chatgpt_script_prompt(CFG, {"topic": "el mejor pan"})
    assert str(FLOOR_WORDS) in prompt  # 1320 floor present
    assert "AT LEAST" in prompt
    assert "NO upper limit" in prompt
    # The stale inflated/ceiling contract must be gone.
    assert "reduce" not in prompt.lower() or "never" in prompt.lower()
    assert "QUALITY OVER BREVITY" in prompt


def test_script_prompt_no_stale_multiplier_ceiling_number():
    # Old behaviour asked ~2400-2900 words (target*1.43). The new floor is 1320
    # with no ceiling; the inflated draft numbers must not appear.
    prompt = _chatgpt_script_prompt(CFG, {"topic": "el mejor pan"})
    for stale in ("2402", "2642", "2902", "3193"):
        assert stale not in prompt


def test_scenes_plan_scene_count_scales_with_script_length():
    long_script = {**SCRIPT, "narration": " ".join(["palabra"] * 3000)}
    short_script = {**SCRIPT, "narration": "una sola palabra"}
    long_prompt = _chatgpt_scenes_plan_prompt(CFG, long_script)
    short_prompt = _chatgpt_scenes_plan_prompt(CFG, short_script)
    # 3000 words / 45 per scene -> 67 scenes (ceil)
    assert '"target_scene_count": 67' in long_prompt
    # short script falls back to the floor (>= scenes_count_min 40)
    assert '"target_scene_count": 40' in short_prompt


def test_scenes_plan_demands_full_coverage():
    prompt = _chatgpt_scenes_plan_prompt(CFG, SCRIPT)
    low = prompt.lower()
    assert "cover the entire script" in low
    assert "do not compress" in low or "do not compress, summarize, or drop" in low


def test_scenes_batch_prompt_preserves_content():
    plan = {"data": {"batches": [{"batch_index": 1, "scene_start": "scene-01", "scene_end": "scene-06"}]}}
    batch = {"batch_index": 1, "batch_total": 1, "scene_start": "scene-01", "scene_end": "scene-06"}
    prompt = _chatgpt_scenes_batch_prompt(CFG, SCRIPT, plan, batch)
    assert "Do NOT summarize" in prompt
    assert "split" in prompt.lower()


def test_monolithic_scenes_prompt_adds_scenes_not_condense():
    prompt = _chatgpt_scenes_prompt(CFG, SCRIPT)
    assert "reduce narration text length per scene" not in prompt
    assert "ADD" in prompt and "scenes" in prompt
    assert "PRESERVE CONTENT" in prompt
