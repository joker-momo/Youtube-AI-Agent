from __future__ import annotations

from video_agent.shorts.visual_acquisition import (
    build_visual_acquisition_context,
    compile_span_search_queries,
    duration_bucket,
    resolve_visual_quality_flow_config,
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


def test_channel_avoid_visuals_inject_into_forbidden_subject_tags() -> None:
    # A wellness channel for capable 45+ adults must never pull medical/disability
    # footage (e.g. "the chair" intent matched wheelchair clips). Channel-level
    # avoid_visuals must be injected into forbidden_subject_tags so Grounding DINO
    # grounds them as forbidden and the span rejects such clips.
    context = build_visual_acquisition_context(
        visual_span={"id": "vs01", "scene_ids": ["s01"], "visual_intent": "the chair"},
        member_scenes=[{"id": "s01", "duration_sec": 2.5}],
        channel_config={
            "shorts": {
                "visual_quality_flow": {
                    "acquisition": {"trim_margin_sec": 1.0},
                    "avoid_visuals": ["wheelchair", "hospital bed"],
                }
            }
        },
    )
    forbidden = context["spans"][0]["forbidden_subject_tags"]
    assert "wheelchair" in forbidden
    assert "hospital bed" in forbidden


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


def test_visual_quality_flow_config_preserves_semantic_local_qa_fields() -> None:
    cfg = resolve_visual_quality_flow_config(
        {
            "shorts": {
                "visual_quality_flow": {
                    "local_qa": {
                        "enabled": True,
                        "semantic_adapter": "full",
                        "detector_adapter": "grounding_dino",
                        "device": "cpu",
                        "semantic_max_frames": 2,
                        "semantic_models": {"siglip": "custom/siglip"},
                        "semantic_thresholds": {"siglip_reject": 0.05},
                    }
                }
            }
        }
    )

    local_qa = cfg["local_qa"]
    assert local_qa["semantic_adapter"] == "full"
    assert local_qa["detector_adapter"] == "grounding_dino"
    assert local_qa["device"] == "cpu"
    assert local_qa["semantic_max_frames"] == 2
    assert local_qa["semantic_models"]["siglip"] == "custom/siglip"
    assert local_qa["semantic_thresholds"]["siglip_reject"] == 0.05
