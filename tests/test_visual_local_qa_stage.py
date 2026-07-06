from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from video_agent.shorts import paths
from video_agent.shorts.builder.stages.visual_local_qa import _stage_visual_local_qa


def _scenes(*, critical: bool = False) -> dict[str, Any]:
    importance = "critical" if critical else "normal"
    return {
        "short_id": "short-04",
        "scenes": [
            {
                "id": "s01",
                "layout": "short_tip",
                "duration_sec": 2.0,
                "visual_span_id": "vs01",
                "visual_importance": importance,
                "required_subject_tags": ["adult_45_plus"],
                "required_action_tags": ["gentle_walking"],
                "required_environment_tags": ["outdoor_path"],
                "forbidden_evidence_tags": ["visible_injury"],
            },
            {
                "id": "s02",
                "layout": "short_tip",
                "duration_sec": 2.0,
                "visual_span_id": "vs01",
                "visual_importance": importance,
                "required_subject_tags": ["adult_45_plus"],
                "required_action_tags": ["gentle_walking"],
                "required_environment_tags": ["outdoor_path"],
            },
        ],
    }


def _prc_docs() -> dict[str, Any]:
    span = {
        "visual_span_id": "vs01",
        "scene_ids": ["s01", "s02"],
        "visual_importance": "normal",
        "planned_duration_sec": 4.0,
        "trim_margin_sec": 1.0,
        "required_subject_tags": ["adult_45_plus"],
        "required_action_tags": ["gentle_walking"],
        "required_environment_tags": ["outdoor_path"],
        "forbidden_evidence_tags": ["visible_injury"],
    }
    candidates = [
        {
            "candidate_id": "pexels_video-winner",
            "provider": "pexels_video",
            "provider_asset_id": "winner",
            "download_url_ref": "https://cdn.example.invalid/winner.mp4",
            "render_media_kind": "video",
            "source_media_kind": "native_video",
            "metadata_gate": {"eligible": True, "reasons": []},
        },
        {
            "candidate_id": "pexels_video-runner",
            "provider": "pexels_video",
            "provider_asset_id": "runner",
            "download_url_ref": "https://cdn.example.invalid/runner.mp4",
            "render_media_kind": "video",
            "source_media_kind": "native_video",
            "metadata_gate": {"eligible": True, "reasons": []},
        },
        {
            "candidate_id": "pexels_video-bulk",
            "provider": "pexels_video",
            "provider_asset_id": "bulk",
            "download_url_ref": "https://cdn.example.invalid/bulk.mp4",
            "render_media_kind": "video",
            "source_media_kind": "native_video",
            "metadata_gate": {"eligible": True, "reasons": []},
        },
    ]
    selection = {
        "visual_span_id": "vs01",
        "provisional_candidate_id": "pexels_video-winner",
        "runner_up_ids": ["pexels_video-runner", "pexels_video-bulk"],
        "render_eligible": False,
        "requires_local_validation": True,
        "metadata_selection_status": "metadata_promising",
    }
    return {
        "visual_acquisition_context": {"spans": [span]},
        "visual_span_candidates": {"spans": [{"visual_span_id": "vs01", "candidates": candidates}]},
        "visual_span_asset_selection": {
            "selection_kind": "provisional_metadata",
            "spans": [selection],
        },
    }


class _Downloader:
    def __init__(self) -> None:
        self.downloaded: list[str] = []

    def download(self, *, candidate, short_dir, span_id):
        self.downloaded.append(candidate["candidate_id"])
        out = short_dir / "assets" / f"{candidate['provider_asset_id']}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake mp4")
        return {
            **candidate,
            "local_path": str(out),
            "public_ref": f"jobs/short-04/assets/{out.name}",
        }


class _Analyzer:
    def analyze(self, path, *, required_frames, fps):
        name = Path(path).name
        duration = 60 if "winner" in name else 180
        return {
            "decode": {"verdict": "PASS"},
            "actual_duration_in_frames": duration,
            "fps": fps,
            "black_frame_ratio": 0.0,
            "motion_band": "normal_motion",
            "technical_quality": {"verdict": "PASS", "sharpness_score": 80.0},
            "crop_feasibility": {"full_window_feasible": True, "crop_stability_score": 0.9},
            "sampled_frames": [],
        }


class _PassAnalyzer:
    def analyze(self, path, *, required_frames, fps):
        return {
            "decode": {"verdict": "PASS"},
            "actual_duration_in_frames": 180,
            "actual_duration_sec": 6.0,
            "fps": fps,
            "black_frame_ratio": 0.0,
            "motion_band": "normal_motion",
            "technical_quality": {"verdict": "PASS", "sharpness_score": 80.0},
            "crop_feasibility": {"full_window_feasible": True, "crop_stability_score": 0.9},
            "sampled_frames": [],
        }


class _SemanticRejectWinner:
    def analyze_span(self, **kw):
        asset_id = kw.get("asset_id")
        status = "CONTRADICTED" if asset_id == "pexels_video-winner" else "SUPPORTED"
        return [
            {
                # required_subject is a HARD gate (a contradicted subject = wrong
                # footage); action/environment/brand are advisory.
                "requirement": "required_subject:age_band_45_plus",
                "status": status,
                "capability_source": "optional_semantic_model",
                "model": "fake-vlm",
                "model_version": "fake-vlm",
                "asset_id": asset_id,
                "confidence": None,
                "reason": "semantic gate test",
            }
        ]


class _SemanticWeakWinnerPassRunner:
    def analyze_span(self, **kw):
        asset_id = kw.get("asset_id")
        confidence = 0.2 if asset_id == "pexels_video-winner" else 2.0
        return [
            {
                "requirement": "topic:visual_intent",
                "status": "SUPPORTED",
                "capability_source": "optional_semantic_model",
                "model": "fake-vlm",
                "model_version": "fake-vlm",
                "asset_id": asset_id,
                "confidence": confidence,
                "reason": "semantic weak-support gate test",
            }
        ]


def _ctx(tmp_path: Path, *, mode: str = "report_only", critical: bool = False) -> SimpleNamespace:
    json_dir = tmp_path / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    extras = {"short_scenes": _scenes(critical=critical), **_prc_docs()}
    if critical:
        extras["visual_acquisition_context"]["spans"][0]["visual_importance"] = "critical"
    calls: list[tuple[str, str]] = []
    ctx = SimpleNamespace(
        short_plan={"short_id": "short-04"},
        short_dir=tmp_path,
        json_dir=json_dir,
        long_job_dir=tmp_path.parent,
        channel_config={
            "channel": {"id": "vida-plena-45"},
            "shorts": {
                "visual_quality_flow": {
                    "enabled": True,
                    "mode": mode,
                    "local_qa": {
                        "enabled": True,
                        "max_runner_ups": 1,
                        "semantic_adapter": "none",
                        "detector_adapter": "none",
                        "critical_fail_closed": True,
                        "report_only_never_blocks_render": True,
                    },
                    "trim_selector": {"stride_sec": 0.5, "max_windows": 12},
                },
                "render": {"fps": 30},
            },
        },
        status={"status": "generating"},
        extras=extras,
        update_stage=lambda name, status, **kw: calls.append((name, status)),
        check_stop=lambda: None,
    )
    ctx.calls = calls  # type: ignore[attr-defined]
    return ctx


def test_visual_local_qa_downloads_only_winner_and_bounded_runner_then_replaces(
    tmp_path: Path, monkeypatch
) -> None:
    downloader = _Downloader()
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_local_qa.FinalistDownloader",
        lambda *a, **k: downloader,
    )
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_local_qa.LocalVisualAnalyzer",
        lambda *a, **k: _Analyzer(),
    )
    ctx = _ctx(tmp_path)

    result = _stage_visual_local_qa(ctx)

    assert result.returns is None
    assert downloader.downloaded == ["pexels_video-winner", "pexels_video-runner"]
    assert (ctx.json_dir / paths.SHORT_VISUAL_SPAN_ASSET_QA_FILE).exists()
    assert (ctx.json_dir / paths.SHORT_TRIM_WINDOW_PLAN_FILE).exists()
    asset_qa = json.loads((ctx.json_dir / paths.SHORT_VISUAL_SPAN_ASSET_QA_FILE).read_text())
    trim_plan = json.loads((ctx.json_dir / paths.SHORT_TRIM_WINDOW_PLAN_FILE).read_text())
    span_qa = asset_qa["spans"][0]
    assert span_qa["final_selection_status"] == "replaced"
    assert span_qa["final_candidate_id"] == "pexels_video-runner"
    assert span_qa["qa"]["verdict"] == "CAPABILITY_REDUCED"
    assert trim_plan["spans"][0]["selected_window_start_in_frames"] >= 0
    assert trim_plan["spans"][0]["required_duration_in_frames"] == 120
    assert ctx.extras["trim_window_plan"] == trim_plan


def test_visual_local_qa_skips_generated_graphic_route_without_download(
    tmp_path: Path, monkeypatch
) -> None:
    downloader = _Downloader()
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_local_qa.FinalistDownloader",
        lambda *a, **k: downloader,
    )
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_local_qa.LocalVisualAnalyzer",
        lambda *a, **k: _Analyzer(),
    )
    ctx = _ctx(tmp_path)
    span = ctx.extras["visual_acquisition_context"]["spans"][0]
    span["visual_route"] = "generated_graphic"
    selection = ctx.extras["visual_span_asset_selection"]["spans"][0]
    selection["visual_route"] = "generated_graphic"
    selection["provisional_candidate_id"] = None
    selection["runner_up_ids"] = []
    selection["requires_local_validation"] = False

    result = _stage_visual_local_qa(ctx)

    assert result.returns is None
    assert downloader.downloaded == []
    asset_qa = json.loads((ctx.json_dir / paths.SHORT_VISUAL_SPAN_ASSET_QA_FILE).read_text())
    trim_plan = json.loads((ctx.json_dir / paths.SHORT_TRIM_WINDOW_PLAN_FILE).read_text())
    span_qa = asset_qa["spans"][0]
    assert span_qa["visual_route"] == "generated_graphic"
    assert span_qa["final_selection_status"] == "routed_to_generated_graphic"
    assert span_qa["qa"]["verdict"] == "PASS"
    assert trim_plan["spans"] == []


def test_report_only_capability_reduced_does_not_block_render(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_local_qa.FinalistDownloader",
        lambda *a, **k: _Downloader(),
    )
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_local_qa.LocalVisualAnalyzer",
        lambda *a, **k: _Analyzer(),
    )
    ctx = _ctx(tmp_path, mode="report_only", critical=True)

    result = _stage_visual_local_qa(ctx)

    assert result.returns is None
    assert ("visual_local_qa", "completed") in ctx.calls
    assert ctx.status["status"] == "generating"


def test_semantic_fail_rejects_winner_and_tries_runner(tmp_path: Path, monkeypatch) -> None:
    downloader = _Downloader()
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_local_qa.FinalistDownloader",
        lambda *a, **k: downloader,
    )
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_local_qa.LocalVisualAnalyzer",
        lambda *a, **k: _PassAnalyzer(),
    )
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_local_qa.build_semantic_analyzer",
        lambda *a, **k: _SemanticRejectWinner(),
    )
    ctx = _ctx(tmp_path)
    ctx.channel_config["shorts"]["visual_quality_flow"]["local_qa"]["semantic_adapter"] = "clip_vlm"

    result = _stage_visual_local_qa(ctx)

    assert result.returns is None
    assert downloader.downloaded == ["pexels_video-winner", "pexels_video-runner"]
    asset_qa = json.loads((ctx.json_dir / paths.SHORT_VISUAL_SPAN_ASSET_QA_FILE).read_text())
    span_qa = asset_qa["spans"][0]
    assert span_qa["final_selection_status"] == "replaced"
    assert span_qa["final_candidate_id"] == "pexels_video-runner"
    assert span_qa["candidate_qa"][0]["qa"]["verdict"] == "FAIL"
    assert span_qa["candidate_qa"][0]["qa"]["rejection_reasons"] == ["semantic_mismatch"]
    assert span_qa["candidate_qa"][1]["qa"]["verdict"] == "PASS"


def test_enforced_critical_weak_semantic_support_tries_runner(
    tmp_path: Path, monkeypatch
) -> None:
    downloader = _Downloader()
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_local_qa.FinalistDownloader",
        lambda *a, **k: downloader,
    )
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_local_qa.LocalVisualAnalyzer",
        lambda *a, **k: _PassAnalyzer(),
    )
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_local_qa.build_semantic_analyzer",
        lambda *a, **k: _SemanticWeakWinnerPassRunner(),
    )
    ctx = _ctx(tmp_path, mode="enforced", critical=True)
    ctx.channel_config["shorts"]["visual_quality_flow"]["local_qa"]["semantic_adapter"] = "clip"

    result = _stage_visual_local_qa(ctx)

    assert result.returns is None
    assert downloader.downloaded == ["pexels_video-winner", "pexels_video-runner"]
    asset_qa = json.loads((ctx.json_dir / paths.SHORT_VISUAL_SPAN_ASSET_QA_FILE).read_text())
    span_qa = asset_qa["spans"][0]
    assert span_qa["final_selection_status"] == "replaced"
    assert span_qa["final_candidate_id"] == "pexels_video-runner"
    assert span_qa["candidate_qa"][0]["qa"]["verdict"] == "FAIL"
    assert span_qa["candidate_qa"][0]["qa"]["rejection_reasons"] == [
        "semantic_weak_required_support"
    ]
    assert span_qa["candidate_qa"][1]["qa"]["verdict"] == "PASS"


def test_enforced_critical_missing_semantic_capability_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_local_qa.FinalistDownloader",
        lambda *a, **k: _Downloader(),
    )
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_local_qa.LocalVisualAnalyzer",
        lambda *a, **k: _Analyzer(),
    )
    ctx = _ctx(tmp_path, mode="enforced", critical=True)

    result = _stage_visual_local_qa(ctx)

    assert result.returns is not None
    assert result.returns["status"] == "failed"
    assert result.returns["failure_stage"] == "visual_local_qa"


# --------------------------------------------------------------------------- #
# bug-475: revalidate a native span's background fallback through the SAME local
# + semantic gate before beat planning, so a validated fallback rescues a span
# whose PR-C finalists all failed — without weakening any quality gate.
# --------------------------------------------------------------------------- #
from video_agent.shorts.builder.stages.visual_local_qa import (  # noqa: E402
    _trim_config,
    revalidate_failed_spans_with_fallback,
)


class _SemanticPass:
    def analyze_span(self, **kw):
        return [
            {
                "requirement": "required_subject:age_band_45_plus",
                "status": "SUPPORTED",
                "capability_source": "optional_semantic_model",
                "model": "fake", "model_version": "fake",
                "asset_id": kw.get("asset_id"), "confidence": 2.0, "reason": "ok",
            }
        ]


class _SemanticContradict:
    def analyze_span(self, **kw):
        return [
            {
                "requirement": "required_subject:age_band_45_plus",
                "status": "CONTRADICTED",
                "capability_source": "optional_semantic_model",
                "model": "fake", "model_version": "fake",
                "asset_id": kw.get("asset_id"), "confidence": None, "reason": "wrong age",
            }
        ]


def _revalidate_fixture(tmp_path: Path, *, fallback_asset_id: str = "bg-s04"):
    """Build the minimal inputs for revalidate_failed_spans_with_fallback with a
    single native span vs04 (scene s04) that local QA left FAIL / ineligible, plus
    a real on-disk background fallback asset for s04."""
    asset = tmp_path / "assets" / "s04.mp4"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"fake mp4")
    spans = [{
        "visual_span_id": "vs04",
        "scene_ids": ["s04"],
        "visual_intent": "eating a meal",
        "visual_importance": "critical",
        "required_subject_tags": ["age_band_45_plus"],
    }]
    asset_qa = {
        "spans": [{
            "visual_span_id": "vs04",
            "scene_ids": ["s04"],
            "visual_route": "native_video_candidate",
            "final_candidate_id": None,
            "final_selection_status": "rejected",
            "render_eligible": False,
            "requires_local_validation": True,
            "candidate_qa": [{"candidate_id": "pexels-REJECTED"}],
            "evidence_records": [],
            "qa": {"verdict": "FAIL", "errors": [], "warnings": []},
        }],
        "qa": {"verdict": "FAIL", "errors": [], "warnings": []},
    }
    trim_plan = {"spans": [], "qa": {"verdict": "FAIL", "errors": [], "warnings": []}}
    resolved = {"scenes": {"s04": {
        "scene_id": "s04",
        "local_path": str(asset),
        "public_ref": "jobs/x/assets/s04.mp4",
        "provider": "pexels",
        "provider_asset_id": "12908877",
        "asset_id": fallback_asset_id,
        "source_media_kind": "native_video",
        "exists": True,
        "semantic_rejected": False,
    }}}
    short_scenes = {"scenes": [{"id": "s04", "layout": "short_tip", "duration_sec": 5.0}]}
    local_qa = {"enabled": True, "critical_fail_closed": True}
    flow_cfg = {"trim_selector": {"stride_sec": 0.5, "max_windows": 12}}
    return dict(
        spans=spans, asset_qa=asset_qa, trim_plan=trim_plan, resolved=resolved,
        short_scenes=short_scenes, local_qa=local_qa, mode="enforced", fps=30,
        trim_cfg=_trim_config(flow_cfg),
    )


def test_revalidate_rescues_span_when_background_fallback_passes(tmp_path: Path) -> None:
    kw = _revalidate_fixture(tmp_path)
    report = revalidate_failed_spans_with_fallback(
        analyzer=_PassAnalyzer(), semantic_analyzer=_SemanticPass(), **kw
    )
    assert report["rescued_count"] == 1
    span_qa = kw["asset_qa"]["spans"][0]
    assert span_qa["render_eligible"] is True
    assert span_qa["qa"]["verdict"] == "PASS"
    assert span_qa["final_candidate_id"] == "bg-s04"
    assert span_qa["revalidated_from_background_fallback"] is True
    # A trim entry now exists so the beat planner can build a validated native clip.
    trim = kw["trim_plan"]["spans"]
    assert len(trim) == 1 and trim[0]["visual_span_id"] == "vs04"
    assert trim[0]["final_candidate_id"] == "bg-s04"
    assert trim[0]["selected_window_start_in_frames"] >= 0


def test_revalidate_does_not_rescue_when_fallback_fails_same_gate(tmp_path: Path) -> None:
    kw = _revalidate_fixture(tmp_path)
    report = revalidate_failed_spans_with_fallback(
        analyzer=_PassAnalyzer(), semantic_analyzer=_SemanticContradict(), **kw
    )
    assert report["rescued_count"] == 0
    span_qa = kw["asset_qa"]["spans"][0]
    assert span_qa["render_eligible"] is False
    assert span_qa["qa"]["verdict"] == "FAIL"
    assert kw["trim_plan"]["spans"] == []


def test_revalidate_strict_no_rescue_when_semantic_unavailable(tmp_path: Path) -> None:
    # semantic_analyzer=None -> placeholder records -> CAPABILITY_REDUCED, never PASS.
    kw = _revalidate_fixture(tmp_path)
    report = revalidate_failed_spans_with_fallback(
        analyzer=_PassAnalyzer(), semantic_analyzer=None, **kw
    )
    assert report["rescued_count"] == 0
    assert kw["asset_qa"]["spans"][0]["render_eligible"] is False


def test_revalidate_skips_asset_already_rejected_by_local_qa(tmp_path: Path) -> None:
    # The background fallback resolves to the SAME asset id local QA already rejected.
    kw = _revalidate_fixture(tmp_path, fallback_asset_id="pexels-REJECTED")
    report = revalidate_failed_spans_with_fallback(
        analyzer=_PassAnalyzer(), semantic_analyzer=_SemanticPass(), **kw
    )
    assert report["rescued_count"] == 0
    assert kw["asset_qa"]["spans"][0]["render_eligible"] is False


def test_revalidate_leaves_already_eligible_span_untouched(tmp_path: Path) -> None:
    kw = _revalidate_fixture(tmp_path)
    kw["asset_qa"]["spans"][0]["render_eligible"] = True
    kw["asset_qa"]["spans"][0]["qa"]["verdict"] = "PASS"
    report = revalidate_failed_spans_with_fallback(
        analyzer=_PassAnalyzer(), semantic_analyzer=_SemanticPass(), **kw
    )
    assert report["rescued_count"] == 0
    assert "vs04" not in report["attempted_span_ids"]
