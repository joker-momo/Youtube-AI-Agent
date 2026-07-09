"""Thumbnails must show the video's DISTINCTIVE subject, not a generic category.

Real incident (Mundial job): a video about enjoying World Cup night matches got
a thumbnail with only generic sleep props (bed, clock, tea) because the topic
classified as "sleep" — zero football/Mundial cue, so it read like any generic
sleep video. The thumbnail prompt must require the specific distinctive subject
named in the title, combined with the category prop.
"""

from __future__ import annotations

from video_agent.thumbnail_planner import build_thumbnail_prompt


def test_prompt_requires_distinctive_topic_subject_and_forbids_generic():
    plan = {
        "variant_title": "Dormir mejor durante los partidos nocturnos del Mundial",
        "thumbnail_text": "5 GESTOS CLAVE",
        "main_prop": "a bedside clock",
    }
    prompt = build_thumbnail_prompt(plan).lower()
    assert "distinctive" in prompt
    assert "generic" in prompt  # explicitly forbids a generic category scene
