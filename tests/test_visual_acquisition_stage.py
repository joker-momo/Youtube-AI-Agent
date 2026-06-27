from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from video_agent.shorts import paths
from video_agent.shorts.builder.stages.visual_acquisition import _stage_visual_acquisition
from video_agent.shorts.builder.types import StageSignal


def _scenes() -> dict[str, Any]:
    return {
        "short_id": "short-04",
        "scenes": [
            {
                "id": "s01",
                "layout": "short_hook",
                "duration_sec": 2.0,
                "visual_span_id": "vs01",
                "visual_span_intent": "mature adult walking outside",
                "required_subject_tags": ["adult_45_plus"],
                "required_action_tags": ["gentle_walking"],
                "required_environment_tags": ["outdoor_path"],
                "forbidden_evidence_tags": ["visible_injury"],
                "visual_importance": "high",
            },
            {
                "id": "s02",
                "layout": "short_tip",
                "duration_sec": 3.5,
                "visual_span_id": "vs02",
                "visual_span_intent": "mature adult walking outside",
                "required_subject_tags": ["adult_45_plus"],
                "required_action_tags": ["gentle_walking"],
                "required_environment_tags": ["outdoor_path"],
            },
            {
                "id": "s03",
                "layout": "short_tip",
                "duration_sec": 3.5,
                "visual_span_id": "vs02",
                "visual_span_intent": "mature adult walking outside",
                "required_subject_tags": ["adult_45_plus"],
                "required_action_tags": ["gentle_walking"],
                "required_environment_tags": ["outdoor_path"],
            },
        ],
    }


def _spans() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "contract_revision": "3.2.3",
        "short_id": "short-04",
        "spans": [
            {"id": "vs01", "scene_ids": ["s01"], "visual_intent": "mature adult walking outside"},
            {
                "id": "vs02",
                "scene_ids": ["s02", "s03"],
                "visual_intent": "mature adult walking outside",
            },
        ],
        "input_hash": "abc",
        "qa": {"verdict": "PASS", "errors": [], "warnings": []},
    }


class _Service:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_visual_span_candidates(self, *, acquisition_context, budget):
        self.calls.append(acquisition_context["visual_span_id"])
        return [
            {
                "candidate_id": f"pexels_video-{acquisition_context['visual_span_id']}",
                "provider": "pexels_video",
                "provider_asset_id": acquisition_context["visual_span_id"],
                "query_origins": [
                    {
                        "query": acquisition_context["query_plan"]["primary"],
                        "query_class": "primary",
                    }
                ],
                "metadata_gate": {"eligible": True, "reasons": []},
                "local_path": None,
                "public_ref": None,
                "score": None,
                "rank": None,
            }
        ]

    def select_provisional_span_candidate(
        self, *, acquisition_context, candidates, recent_visual_memory, config
    ):
        return {
            "visual_span_id": acquisition_context["visual_span_id"],
            "provisional_candidate_id": candidates[0]["candidate_id"],
            "metadata_selection_status": "metadata_promising",
            "metadata_pre_score": 70.0,
            "runner_up_ids": [],
            "render_eligible": False,
            "requires_local_validation": True,
            "fallback_level": 0,
            "fallback_used": False,
            "rejection_reasons": {},
            "evidence_records": [
                {
                    "requirement": "forbidden_evidence:visible_injury",
                    "status": "UNKNOWN",
                    "capability_source": "provider_metadata",
                    "source_field": "provider.tags",
                    "asset_id": candidates[0]["candidate_id"],
                    "reason": "absence of a provider tag cannot confirm visual absence",
                }
            ],
        }


def _ctx(tmp_path: Path) -> SimpleNamespace:
    json_dir = tmp_path / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    calls: list[tuple[str, str]] = []
    ctx = SimpleNamespace(
        short_plan={"short_id": "short-04"},
        short_dir=tmp_path,
        json_dir=json_dir,
        long_job_dir=tmp_path.parent,
        channel_config={
            "channel": {"id": "vida-plena-45"},
            "visuals": {"providers": ["pexels_video"]},
            "shorts": {
                "visual_quality_flow": {
                    "enabled": True,
                    "mode": "report_only",
                    "acquisition": {"download_policy": "none", "provider": "pexels_video"},
                }
            },
        },
        status={"status": "generating"},
        extras={"short_scenes": _scenes(), "visual_spans": _spans()},
        update_stage=lambda name, status, **kw: calls.append((name, status)),
        check_stop=lambda: None,
    )
    ctx.calls = calls  # type: ignore[attr-defined]
    return ctx


def test_visual_acquisition_stage_writes_pr_c_artifacts_without_pr_d_artifact(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_acquisition.StockAssetService",
        lambda *a, **k: _Service(),
    )
    ctx = _ctx(tmp_path)
    before_scenes = json.dumps(ctx.extras["short_scenes"], sort_keys=True)

    result = _stage_visual_acquisition(ctx)

    assert result.signal is StageSignal.PROCEED
    jd = tmp_path / "json"
    assert (jd / paths.SHORT_VISUAL_ACQUISITION_CONTEXT_FILE).exists()
    assert (jd / paths.SHORT_VISUAL_SPAN_CANDIDATES_FILE).exists()
    assert (jd / paths.SHORT_VISUAL_SPAN_SELECTION_FILE).exists()
    assert (jd / paths.SHORT_VISUAL_SPAN_METADATA_QA_FILE).exists()
    assert not (jd / "visual_span_asset_qa.json").exists()
    assert not (jd / "trim_window_plan.json").exists()
    assert not (jd / "visual_beat_plan.json").exists()
    assert not (jd / "visual_performance_features.json").exists()

    metadata_qa = json.loads((jd / paths.SHORT_VISUAL_SPAN_METADATA_QA_FILE).read_text())
    assert metadata_qa["contract_revision"] == "4.0.3"
    assert metadata_qa["summary"]["download_count"] == 0
    assert all(span["render_eligible"] is False for span in metadata_qa["spans"])
    assert (
        metadata_qa["spans"][0]["evidence_capability"]["provider_metadata_requirements"][0][
            "status"
        ]
        == "UNKNOWN"
    )
    assert json.dumps(ctx.extras["short_scenes"], sort_keys=True) == before_scenes
    assert "visual_metadata_qa" in ctx.extras
    assert ("visual_acquisition", "completed") in ctx.calls


def test_visual_acquisition_routes_graphic_span_without_pexels_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    service = _Service()
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_acquisition.StockAssetService",
        lambda *a, **k: service,
    )
    ctx = _ctx(tmp_path)
    ctx.extras["short_scenes"]["scenes"][1]["asset_strategy"] = "graphic_fallback"

    result = _stage_visual_acquisition(ctx)

    assert result.signal is StageSignal.PROCEED
    selection = json.loads(
        (ctx.json_dir / paths.SHORT_VISUAL_SPAN_SELECTION_FILE).read_text()
    )
    by_span = {span["visual_span_id"]: span for span in selection["spans"]}
    assert by_span["vs02"]["visual_route"] == "generated_graphic"
    assert by_span["vs02"]["provisional_candidate_id"] is None
    assert by_span["vs02"]["requires_local_validation"] is False
    assert service.calls == ["vs01"]
