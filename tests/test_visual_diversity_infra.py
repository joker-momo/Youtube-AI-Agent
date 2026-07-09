"""Spec §18–§23: migration idempotency, reservations, API budget, backoff, baseline, query expansion."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from PIL import Image

from video_agent.assets.library import AssetLibrary
from video_agent.assets.visual_diversity.api_budget import (
    ApiBudget,
    is_backoff_active,
    read_backoff_state,
    write_backoff_state,
)
from video_agent.assets.visual_diversity.integration import (
    finalize_visual_diversity_report,
    prepare_visual_diversity,
)
from video_agent.assets.visual_diversity.backfill import backfill_asset
from video_agent.assets.visual_diversity.placeholder_baseline import resolve_placeholder_baseline
from video_agent.assets.visual_diversity.query_expansion import (
    expand_pexels_queries,
    normalize_to_english_pexels_query,
)
from video_agent.assets.visual_diversity.report import build_report, visual_diversity_score
from video_agent.assets.visual_diversity.reservations import try_reserve_asset


def _dna() -> dict:
    return yaml.safe_load(
        Path("configs/vida-plena-45/visual-dna.yaml").read_text(encoding="utf-8")
    )


def _candidate(downloaded: Path) -> dict:
    Image.new("RGB", (640, 360), (10, 20, 30)).save(downloaded, quality=80)
    return {
        "provider": "pexels",
        "provider_asset_id": "9001",
        "source_url": "https://www.pexels.com/photo/9001/",
        "photographer": "Test",
        "photographer_url": "https://www.pexels.com/@test",
        "attribution": "Test on Pexels",
        "license": "Pexels License",
        "quality": "large2x",
        "tags": ["morning"],
    }


def test_sqlite_migrations_are_idempotent(tmp_path: Path):
    root = tmp_path / "library"
    library = AssetLibrary(root)
    # Second instantiation must not raise.
    library_again = AssetLibrary(root)
    cols = {row[1] for row in sqlite3.connect(library.db_path).execute("PRAGMA table_info(assets)")}
    assert {"visual_bucket", "shot_type", "creator_key", "metadata_json"} <= cols


def test_record_usage_clears_matching_reservation(tmp_path: Path):
    library = AssetLibrary(tmp_path / "lib")
    downloaded = tmp_path / "in.jpg"
    asset = library.store_photo(_candidate(downloaded), downloaded, original_query="morning")

    db = library.db_path
    assert try_reserve_asset(db, asset["asset_id"], "ch", "job-a", "scene-1", ttl_minutes=10) is True
    library.record_usage(asset["asset_id"], "ch", "job-a", "scene-1")
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute(
            "SELECT 1 FROM asset_reservations WHERE asset_id = ?", (asset["asset_id"],)
        ).fetchone()
    assert row is None


def test_reservation_prevents_other_jobs(tmp_path: Path):
    library = AssetLibrary(tmp_path / "lib")
    db = library.db_path
    assert try_reserve_asset(db, "asset-X", "ch", "job-a", "scene-1", ttl_minutes=10) is True
    # Different job, same asset: must be denied.
    assert try_reserve_asset(db, "asset-X", "ch", "job-b", "scene-2", ttl_minutes=10) is False
    # Same job again: allowed.
    assert try_reserve_asset(db, "asset-X", "ch", "job-a", "scene-3", ttl_minutes=10) is True


def test_reservation_expires(tmp_path: Path):
    library = AssetLibrary(tmp_path / "lib")
    db = library.db_path
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    # Insert a stale reservation by hand.
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """
            INSERT INTO asset_reservations (
                reservation_id, asset_id, channel_id, job_id, scene_id, reserved_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("stale", "asset-Y", "ch", "job-old", "scene-1", past.isoformat(), past.isoformat()),
        )
    assert try_reserve_asset(db, "asset-Y", "ch", "job-new", "scene-1", ttl_minutes=10) is True


def test_api_budget_stops_after_429():
    budget = ApiBudget(max_per_video=5)
    assert budget.can_call()
    budget.record_429()
    assert not budget.can_call()
    stats = budget.stats(hourly_budget_warning="no orchestrator")
    assert stats["rate_limited"] is True
    assert stats["hourly_budget_warning"]


def test_backoff_state_round_trip(tmp_path: Path):
    state_path = tmp_path / "backoff.json"
    write_backoff_state(state_path, job_id="job-Z", backoff_seconds=60)
    assert is_backoff_active(state_path)
    assert read_backoff_state(state_path)["source_job_id"] == "job-Z"


def test_placeholder_baseline_resolves_from_prior_reports(tmp_path: Path):
    outputs = tmp_path / "outputs"
    (outputs / "job-old").mkdir(parents=True)
    (outputs / "job-old" / "visual-diversity-report.json").write_text(
        json.dumps({"channel_id": "vida-plena-45", "job_id": "job-old", "placeholder_ratio": 0.10})
    )
    baseline = resolve_placeholder_baseline("vida-plena-45", "job-current", outputs)
    assert baseline == 0.10


def test_placeholder_baseline_returns_none_when_no_history(tmp_path: Path):
    assert resolve_placeholder_baseline("vida-plena-45", "job-x", tmp_path / "missing") is None


def test_expand_pexels_queries_uses_visual_brief_and_bucket_queries():
    dna = _dna()
    scene = {
        "id": "s1",
        "visual_brief": "Mujer 50 mañana cocina café",
        "visual_bucket": "persona_moment",
    }
    queries = expand_pexels_queries(scene, dna)
    assert queries  # non-empty
    # Output queries must be English-normalized; mañana → morning.
    assert any("morning" in q for q in queries)
    assert all(q == q.lower() for q in queries)


def test_visual_dna_stock_queries_avoid_repetitive_wellness_defaults():
    dna = _dna()
    queries = [
        query.lower()
        for bucket in (dna.get("visual_buckets") or {}).values()
        for query in bucket.get("pexels_queries_en", []) or []
    ]
    joined = "\n".join(queries)
    assert "sitting on sofa calm lifestyle" not in joined
    assert "relaxing sofa evening" not in joined
    assert "tea cup evening home calm" not in joined
    assert "morning kitchen coffee calm" not in joined


def test_normalize_to_english_translates_spanish_tokens():
    dna = _dna()
    assert "morning" in normalize_to_english_pexels_query("mañana cocina café", dna)


def test_backfill_infers_visual_bucket_and_quality_score(tmp_path: Path):
    dna = _dna()
    asset = {
        "asset_id": "X",
        "provider": "pexels",
        "provider_asset_id": "1",
        "original_query": "woman 50 morning kitchen coffee calm",
        "provider_tags_json": json.dumps(["morning", "kitchen"]),
        "photographer": "Anna",
        "photographer_url": "https://www.pexels.com/@anna",
        "width": 1920,
        "height": 1080,
        "duration": 18,
    }
    patch = backfill_asset(asset, dna)
    assert patch["visual_bucket"] == "persona_moment"
    assert "creator_key" in patch
    assert "quality_score" in patch
    metadata = json.loads(patch["metadata_json"])
    assert metadata["backfill"]["confidence"] == "low"


def test_build_report_shape_includes_required_fields():
    dna = _dna()
    plans = [{"scene_id": f"s{i}", "scene_index": i, "role": "hook",
              "visual_bucket": "persona_moment", "shot_type": "medium"} for i in range(30)]
    selections = [{
        "scene_id": p["scene_id"],
        "visual_bucket": p["visual_bucket"],
        "shot_type": p["shot_type"],
        "creator_key": "pexels:1",
        "locale_feel": "Spain",
        "original_query": "morning",
        "provider_tags_json": "[]",
        "provider": "pexels",
        "provider_asset_id": str(i),
    } for i, p in enumerate(plans)]
    report = build_report(
        job_id="job-X",
        channel_id="vida-plena-45",
        rollout_mode="report_only",
        plans=plans,
        selections=selections,
        visual_dna=dna,
        api_budget_stats={"max_api_requests_per_video": 80, "api_requests_used": 20, "rate_limited": False},
    )
    for key in (
        "scene_count",
        "video_length_profile",
        "bucket_distribution",
        "shot_type_distribution",
        "creator_distribution",
        "api_budget",
        "placeholder_count",
        "placeholder_ratio",
        "semantic_metadata_present",
        "visual_diversity_score",
    ):
        assert key in report
    assert report["video_length_profile"] == "long"
    assert 0.0 <= report["visual_diversity_score"] <= 1.0


def test_diversity_score_does_not_become_trivially_perfect_for_tiny_videos():
    report = {
        "scene_count": 4,
        "bucket_distribution": {"a": 1, "b": 1, "c": 1, "d": 1},
        "shot_type_distribution": {"a": 1, "b": 1, "c": 1, "d": 1},
        "creator_distribution": {"unknown": 1},
        "reuse_warnings": [],
        "spain_or_mediterranean_scene_count": 0,
        "graphic_cards_rendered": 0,
        "graphic_cards_target": 4,
    }
    score = visual_diversity_score(report)
    assert score < 0.85  # not trivially 1.0


def test_diversity_score_treats_missing_creator_scenes_as_unknown():
    report = {
        "scene_count": 10,
        "video_length_profile": "long",
        "bucket_distribution": {"a": 4, "b": 3, "c": 3},
        "shot_type_distribution": {"medium": 5, "wide": 3, "closeup": 2},
        "creator_distribution": {"pexels:1": 4},
        "reuse_warnings": [],
        "spain_or_mediterranean_scene_count": 0,
        "graphic_cards_rendered": 0,
        "graphic_cards_target": 0,
    }
    score = visual_diversity_score(report)
    # Missing creator coverage becomes "unknown" for the remaining 6 scenes,
    # so max creator ratio is 0.6 and diversity contribution is 0.4.
    assert score == 0.5538


def test_diversity_score_older_short_report_without_target_defaults_to_zero_cards():
    report = {
        "scene_count": 8,
        "video_length_profile": "short",
        "bucket_distribution": {"a": 3, "b": 3, "c": 2},
        "shot_type_distribution": {"medium": 4, "wide": 2, "closeup": 2},
        "creator_distribution": {},
        "reuse_warnings": [],
        "spain_or_mediterranean_scene_count": 0,
        "graphic_cards_rendered": 0,
    }
    expected = dict(report)
    expected["graphic_cards_target"] = 0
    assert visual_diversity_score(report) == visual_diversity_score(expected)


def test_report_only_does_not_mutate_scenes_and_writes_dry_run_file(tmp_path: Path):
    scene_doc = {
        "scenes": [
            {
                "id": f"scene_{i:03d}",
                "narration_text": "tres pasos para mañana lista",
                "on_screen_text": "resumen",
            }
            for i in range(30)
        ]
    }
    visual_config = {
        "visual_dna_path": "configs/vida-plena-45/visual-dna.yaml",
        "diversity": {"enabled": True, "rollout_mode": "report_only"},
        "graphic_cards": {
            "enabled": True,
            "rollout_mode": "report_only",
            "min_per_long_video": 4,
            "supported_card_types": ["checklist", "timeline", "habit_matrix"],
        },
    }

    run = prepare_visual_diversity(
        scene_doc=scene_doc,
        visual_config=visual_config,
        channel_id="vida-plena-45",
        job_id="job-report-only",
        repo_root=Path.cwd(),
        outputs_root=tmp_path / "outputs",
    )

    assert run.rollout_mode == "report_only"
    assert all("visual_bucket" not in scene for scene in scene_doc["scenes"])
    assert all("shot_type" not in scene for scene in scene_doc["scenes"])
    assert all("scene_role" not in scene for scene in scene_doc["scenes"])

    report_path = finalize_visual_diversity_report(
        run,
        job_id="job-report-only",
        channel_id="vida-plena-45",
        outputs_dir=tmp_path / "job-report-only",
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["dry_run_selection_changes_path"] == "outputs/job-report-only/visual-diversity-dry-run.json"
    assert report["graphic_cards_planned"] == 4
    assert report["dry_run_selection_changes_count"] == 4

    dry_run_path = tmp_path / "job-report-only" / "visual-diversity-dry-run.json"
    payload = json.loads(dry_run_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "report_only"
    assert payload["production_assets_changed"] is False
    assert all(change["change_type"] == "graphic_card_plan" for change in payload["proposed_changes"])
