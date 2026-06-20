from __future__ import annotations

from video_agent.shorts.visual_acquisition import (
    build_visual_acquisition_context,
    compile_span_search_queries,
    duration_bucket,
)
from video_agent.shorts.visual_vocabulary import normalize_visual_tokens


def test_builds_context_from_structured_span_fields_without_narration() -> None:
    span = {
        "id": "vs02",
        "scene_ids": ["s02", "s03"],
        "visual_intent": "mature adult taking a gentle recovery walk outdoors",
    }
    scenes = [
        {
            "id": "s02",
            "duration_sec": 3.4,
            "narration": "This long narration must not be concatenated into provider search.",
            "required_subject_tags": ["adult_45_plus"],
            "required_action_tags": ["slow walk"],
            "required_environment_tags": ["park"],
            "required_evidence_tags": ["low_intensity_movement"],
            "forbidden_evidence_tags": ["visible_injury"],
            "first_frame_intent": "clear mature adult immediately visible",
            "crop_target": "full person and feet",
            "visual_importance": "high",
            "visual_search_queries": ["older adult slow walk park"],
        },
        {
            "id": "s03",
            "duration_sec": 4.0,
            "narration": "Another sentence that should stay out of search queries.",
            "required_subject_tags": ["mature adult"],
            "required_action_tags": ["gentle_walking"],
            "required_environment_tags": ["outdoor_path"],
        },
    ]

    context = build_visual_acquisition_context(
        visual_span=span,
        member_scenes=scenes,
        channel_config={
            "shorts": {"visual_quality_flow": {"acquisition": {"trim_margin_sec": 1.0}}}
        },
    )

    assert context["schema_version"] == 1
    assert context["contract_revision"] == "4.0.3"
    assert context["spans"][0]["visual_span_id"] == "vs02"
    assert context["spans"][0]["planned_duration_sec"] == 7.4
    assert context["spans"][0]["duration_bucket"] == "5_to_8_sec"
    assert context["spans"][0]["duration_source"] == "scene_plan"
    assert context["spans"][0]["required_action_tags"] == ["gentle_walking"]
    assert context["spans"][0]["forbidden_evidence_tags"] == ["visible_injury"]
    queries = compile_span_search_queries(
        context["spans"][0], locale="es-ES", provider="pexels_video"
    )
    flattened = " ".join(q for values in queries.values() for q in values)
    assert "long narration" not in flattened
    assert "mature adult gentle walking outdoor path" in queries["primary"][0]
    assert len([q for values in queries.values() for q in values]) <= 3


def test_duration_bucket_boundaries_are_stable() -> None:
    assert duration_bucket(2.99) == "0_to_3_sec"
    assert duration_bucket(3.0) == "3_to_5_sec"
    assert duration_bucket(7.99) == "5_to_8_sec"
    assert duration_bucket(12.0) == "12_plus_sec"


def test_visual_vocabulary_normalizes_aliases_and_records_unknowns() -> None:
    result = normalize_visual_tokens(
        ["slow walk", "custom mobility"],
        category="actions",
        config={},
    )

    assert result["tokens"] == ["gentle_walking"]
    assert result["unknown"] == ["custom mobility"]
    assert result["warnings"] == ["unknown_actions:custom mobility"]
