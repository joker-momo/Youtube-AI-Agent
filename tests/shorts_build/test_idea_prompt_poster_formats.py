"""Idea generation must target the 7 poster formats (operator decision
2026-07-10): every Short is a static infographic now, so ideas are conceived
per poster layout — not the legacy narrated taxonomy — and a batch must cover
diverse layouts so one long video yields a varied Shorts feed.
"""
from video_agent.shorts.idea_prompts import short_ideas_prompt
from video_agent.shorts.infographic.schema import POSTER_FORMATS

SOURCE = {"full_narration": "scene-01: el café...", "source_long_job_id": "job-1",
          "source_title": "Médico revela café"}


def test_idea_prompt_lists_exactly_the_poster_formats():
    p = short_ideas_prompt({}, SOURCE)
    for fmt in POSTER_FORMATS:
        assert fmt in p, f"missing poster format in idea prompt: {fmt}"
    # Legacy narrated taxonomy must be gone.
    for legacy in ("mistake_list", "warning_signs", "top_tips", "pain_to_tip",
                   "problem_solution", "myth_truth"):
        assert legacy not in p, f"legacy idea format still offered: {legacy}"


def test_idea_prompt_enforces_format_diversity():
    p = short_ideas_prompt({}, SOURCE)
    lowered = p.lower()
    assert "at least 4 distinct" in lowered
    assert "more than twice" in lowered or "max 2" in lowered


def test_idea_prompt_keeps_grounding_and_shape_contract():
    p = short_ideas_prompt({}, SOURCE)
    assert "source_scene_ids" in p
    assert "idea-01" in p
    assert "poster" in p.lower()
