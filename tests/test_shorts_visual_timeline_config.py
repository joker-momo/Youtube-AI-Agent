"""Tests for Shorts visual timeline constants and configuration defaults.

Guards two things:
1. The single authoritative ``shorts.visual_timeline`` block keeps the
   enforced-mode renderer contract (a duplicate block once silently
   downgraded it to report_only via PyYAML last-key-wins).
2. channel.yaml contains no duplicate mapping keys at all — PyYAML accepts
   them silently, so a duplicate block shadows the earlier one without any
   error (the exact failure mode behind bridge task
   20260702-161938-fix-duplicate-shorts-visual-timeline-config).
"""

from __future__ import annotations

import yaml

from video_agent.contracts import repo_root
from video_agent.shorts.paths import (
    SHORT_COMPILED_ASSET_SCHEDULE_FILE,
    SHORT_RENDER_CONTINUITY_QA_FILE,
    SHORT_VISUAL_SPAN_QA_FILE,
    SHORT_VISUAL_SPANS_FILE,
)

_CFG_PATH = "configs/vida-plena-45/channel.yaml"


def _load_vida_plena_config() -> dict:
    with open(repo_root() / _CFG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


class _DupKeyError(Exception):
    pass


class _NoDupLoader(yaml.SafeLoader):
    """SafeLoader that raises on duplicate mapping keys instead of last-wins."""


def _no_dup_mapping(loader: _NoDupLoader, node: yaml.MappingNode, deep: bool = False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise _DupKeyError(
                f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}"
            )
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_NoDupLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup_mapping
)


def test_channel_yaml_has_no_duplicate_mapping_keys() -> None:
    """PyYAML silently keeps only the LAST duplicate key, so a repeated block
    (e.g. a second shorts.visual_timeline) shadows the first without any
    warning. Fail loudly instead."""
    text = (repo_root() / _CFG_PATH).read_text(encoding="utf-8")
    yaml.load(text, Loader=_NoDupLoader)  # raises _DupKeyError on duplicates


def test_paths_constants() -> None:
    """Assert that the paths constants resolve to the canonical filenames."""
    assert SHORT_VISUAL_SPANS_FILE == "visual_spans.json"
    assert SHORT_COMPILED_ASSET_SCHEDULE_FILE == "compiled_asset_schedule.json"
    assert SHORT_VISUAL_SPAN_QA_FILE == "visual_span_qa.json"
    assert SHORT_RENDER_CONTINUITY_QA_FILE == "render_continuity_qa.json"


def test_visual_timeline_config_enforced_contract() -> None:
    """The single visual_timeline block must keep the enforced renderer
    contract, including the keys the duplicate block used to shadow."""
    cfg = _load_vida_plena_config()
    visual_timeline = cfg.get("shorts", {}).get("visual_timeline", {})

    # Rollout and invalid-schedule defaults — enforced is the renderer
    # contract (spec v4.0.3); report_only here means the compiled schedule
    # silently stops being authoritative.
    assert visual_timeline.get("enabled") is True
    assert visual_timeline.get("mode") == "enforced"
    assert visual_timeline.get("schedule_schema_version") == 2
    assert visual_timeline.get("on_invalid_schedule") == "fail"

    # Rollback configuration
    rollback = visual_timeline.get("rollback", {})
    assert rollback.get("allow_legacy_renderer") is False

    # Span planning limits
    span_planning = visual_timeline.get("span_planning", {})
    assert span_planning.get("isolate_hook") is True
    assert span_planning.get("isolate_cta") is True
    assert span_planning.get("isolate_graphic_scenes") is True
    assert span_planning.get("max_scenes_per_span") == 3
    assert span_planning.get("max_span_sec") == 10.0

    # Continuous clip settings — require_native_video_for_multi_scene was one
    # of the keys the duplicate block silently dropped.
    continuous_clip = visual_timeline.get("continuous_clip", {})
    assert continuous_clip.get("enabled") is True
    assert continuous_clip.get("playback_rate") == 1.0
    assert continuous_clip.get("allow_loop") is False
    assert continuous_clip.get("require_native_video_for_multi_scene") is True
    assert continuous_clip.get("image_max_hold_sec") == 2.5
    assert continuous_clip.get("near_static_max_hold_sec") == 3.5
    assert continuous_clip.get("low_motion_max_hold_sec") == 5.0
    assert continuous_clip.get("normal_motion_max_hold_sec") == 8.0

    # Candidate budget settings (merged in from the removed duplicate block)
    candidate_budget = visual_timeline.get("candidate_budget", {})
    assert candidate_budget.get("queries_per_span") == 4
    assert candidate_budget.get("results_per_query") == 8
    assert candidate_budget.get("metadata_shortlist") == 10
    assert candidate_budget.get("preview_downloads") == 5
    assert candidate_budget.get("final_candidates_retained") == 2
    assert candidate_budget.get("max_plan_variants") == 3

    # QA settings — boundary check + continuity fixture were shadowed too.
    qa = visual_timeline.get("qa", {})
    assert qa.get("local_only") is True
    assert qa.get("fail_closed_for_critical") is True
    assert qa.get("require_exact_continuity_fixture") is True
    assert qa.get("production_boundary_check_enabled") is True
    assert qa.get("semantic_model_enabled") is False
    assert qa.get("object_detector_enabled") is False

    # Public assets namespace isolation
    assert visual_timeline.get("public_assets", {}).get("unique_short_namespace") is True
