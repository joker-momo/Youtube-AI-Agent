"""bug-485: a placeholder BACKGROUND must not block the render when the
enforced visual schedule covers the scene with a real asset track.

Mirrors the real failed run (short 154336): s02 background fell back to a
generated placeholder, but schedule track vt02 (native pexels) covered s02+s03
— schedule QA PASS, mode enforced — yet render QA raised PLACEHOLDER_USED.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_agent.pipeline import (
    _span_covered_scene_ids,
    _validate_visual_review,
    _write_visual_review,
)


def _write_schedule(job_dir: Path, *, mode: str = "enforced", verdict: str = "PASS") -> None:
    (job_dir / "json").mkdir(parents=True, exist_ok=True)
    (job_dir / "json" / "compiled_asset_schedule.json").write_text(json.dumps({
        "tracks": [
            {"track_type": "background_media", "visual_span_id": "vs02",
             "scene_ids": ["s02", "s03"], "asset_id": "pexels-8845162",
             "provider": "pexels", "render_media_kind": "video"},
            {"track_type": "background_media", "visual_span_id": "vs01",
             "scene_ids": ["s01"], "asset_id": "ai_job_s01",
             "provider": "ai_generated", "render_media_kind": "video"},
            # graphic-fallback track must NOT grant coverage
            {"track_type": "background_media", "visual_span_id": "vs09",
             "scene_ids": ["s09"], "asset_id": "graphic_job_s09",
             "provider": "graphic_fallback", "render_media_kind": "video"},
        ]
    }))
    (job_dir / "json" / "compiled_asset_schedule_qa.json").write_text(
        json.dumps({"verdict": verdict, "mode": mode})
    )


def _assets_and_scenes(source_s02: str = "generated_placeholder"):
    assets = {"scenes": [
        {"scene_id": "s01", "source": "ai_generated", "provider": "ai_generated",
         "provider_asset_id": "x1", "asset_selection": {}},
        {"scene_id": "s02", "source": source_s02, "provider": "graphic_fallback",
         "provider_asset_id": "g2", "asset_selection": {}},
    ]}
    scene_doc = {"scenes": [
        {"id": "s01", "layout": "short_hook", "visual_prompt": "a"},
        {"id": "s02", "layout": "short_pain", "visual_prompt": "b"},
    ]}
    return assets, scene_doc


def test_span_covered_ids_from_enforced_pass_schedule(tmp_path: Path):
    _write_schedule(tmp_path)
    ids = _span_covered_scene_ids(tmp_path)
    assert ids == {"s01", "s02", "s03"}  # graphic_fallback track s09 excluded


def test_span_covered_ids_empty_without_schedule(tmp_path: Path):
    assert _span_covered_scene_ids(tmp_path) == set()


def test_span_covered_ids_empty_when_report_only_or_fail(tmp_path: Path):
    _write_schedule(tmp_path, mode="report_only")
    assert _span_covered_scene_ids(tmp_path) == set()
    _write_schedule(tmp_path, mode="enforced", verdict="FAIL")
    assert _span_covered_scene_ids(tmp_path) == set()


def test_placeholder_downgraded_to_warning_when_span_covered(tmp_path: Path):
    _write_schedule(tmp_path)
    assets, scene_doc = _assets_and_scenes()
    review = _write_visual_review(tmp_path, "job", assets, scene_doc)
    s02 = next(s for s in review["scenes"] if s["scene_id"] == "s02")
    types = {i["type"]: i["severity"] for i in s02["qa"]["issues"]}
    assert "PLACEHOLDER_USED" not in types
    assert types.get("PLACEHOLDER_BACKGROUND_COVERED_BY_SPAN") == "warning"
    # The gate no longer blocks the render.
    _validate_visual_review(review, render=True)


def test_placeholder_still_blocks_without_schedule(tmp_path: Path):
    """Long-form (no schedule) keeps the hard gate unchanged."""
    assets, scene_doc = _assets_and_scenes()
    review = _write_visual_review(tmp_path, "job", assets, scene_doc)
    s02 = next(s for s in review["scenes"] if s["scene_id"] == "s02")
    types = {i["type"]: i["severity"] for i in s02["qa"]["issues"]}
    assert types.get("PLACEHOLDER_USED") == "error"
    with pytest.raises(Exception, match="PLACEHOLDER_USED"):
        _validate_visual_review(review, render=True)


def test_placeholder_still_blocks_for_uncovered_scene(tmp_path: Path):
    """A placeholder on a scene the schedule does NOT cover keeps blocking."""
    _write_schedule(tmp_path)
    assets, scene_doc = _assets_and_scenes()
    assets["scenes"][1]["scene_id"] = "s07"
    scene_doc["scenes"][1]["id"] = "s07"
    review = _write_visual_review(tmp_path, "job", assets, scene_doc)
    s07 = next(s for s in review["scenes"] if s["scene_id"] == "s07")
    types = {i["type"]: i["severity"] for i in s07["qa"]["issues"]}
    assert types.get("PLACEHOLDER_USED") == "error"
