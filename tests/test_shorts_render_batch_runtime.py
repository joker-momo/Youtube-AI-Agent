"""Runtime integration tests for sequential render batches (Codex review round 2).

Covers what the store/API acceptance suites do not execute: the worker's
resume/fail-closed/terminal-retry path, callback ordering + continue-after-
failure through BOTH sequential loops, in-flight stop semantics (AC8), and
terminal-document consistency for whole-batch failures. The fc2421a acceptance
files stay untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_agent.orchestrator.worker import _resume_render_batch, _run_short_infographic_job
from video_agent.shorts import manifest, paths
from video_agent.shorts.idea_store import write_short_ideas
from video_agent.shorts.render_batch import BatchProgress, RenderBatchStore
from video_agent.web.app import app, get_jobs_root

CFG = {
    "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+"},
    "audience": {"language": "es-ES", "age_range": [45, 75]},
}


def _ideas(*idea_ids: str) -> list[dict]:
    return [{"idea_id": i, "title": f"Title {i}"} for i in idea_ids]


def _make_parent_job(root: Path, idea_ids: list[str]) -> Path:
    job = root / "job-1"
    (job / "json").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"job_id": "job-1", "channel_id": "vida-plena-45"}), encoding="utf-8")
    (job / "json" / "script.json").write_text(json.dumps({"narration": "n"}), encoding="utf-8")
    (job / "json" / "seo.json").write_text(json.dumps({"title": "t"}), encoding="utf-8")
    (job / "json" / "scenes.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")
    (job / "json" / "idea.json").write_text("{}", encoding="utf-8")
    for rel in ("video.mp4", "script.json", "scenes.json", "seo.json"):
        (job / rel).write_text("{}", encoding="utf-8")
    write_short_ideas(
        job,
        {
            "schema_version": "short_ideas.v1",
            "source_long_job_id": "job-1",
            "generation_id": "ideas-1",
            "ideas": [
                {
                    "idea_id": idea_id,
                    "idea_type": "synthesis",
                    "format": "numbered_tips",
                    "title": f"Title {idea_id}",
                    "hook_text": "SAL: MIRA SU ETIQUETA",
                    "viewer_pain": "p",
                    "practical_payoff": "q",
                    "source_scene_ids": [],
                    "key_points": [],
                    "narration_seed": "seed",
                    "risk_level": "lifestyle",
                    "scores": {"overall": 80},
                }
                for idea_id in idea_ids
            ],
            "warnings": [],
        },
    )
    return job


class _EventLog(BatchProgress):
    """BatchProgress that also records the deterministic event sequence."""

    def __init__(self, store: RenderBatchStore):
        super().__init__(store)
        self.events: list[tuple] = []

    def item_started(self, idea_id: str) -> bool:
        started = super().item_started(idea_id)
        self.events.append(("started", idea_id, started))
        return started

    def item_completed(self, idea_id: str, short_id) -> None:
        super().item_completed(idea_id, short_id)
        self.events.append(("completed", idea_id))

    def item_failed(self, idea_id: str, error) -> None:
        super().item_failed(idea_id, error)
        self.events.append(("failed", idea_id))

    def batch_finished(self):
        doc = super().batch_finished()
        self.events.append(("finished", doc["status"]))
        return doc

    def batch_cancelled(self, error=None):
        doc = super().batch_cancelled(error)
        self.events.append(("cancelled",))
        return doc


# --------------------------------------------------------------------- store --


def test_mark_failed_cancels_pending_items_and_zeroes_remaining(tmp_path):
    store = RenderBatchStore(tmp_path)
    store.create(batch_id="srb-x", ideas=_ideas("idea-01", "idea-02"), short_type="infographic",
                 force=False, generation_id=None)

    doc = store.mark_failed(error="enqueue_failed")

    assert doc["status"] == "failed"
    assert doc["remaining_count"] == 0
    assert all(item["status"] == "cancelled" for item in doc["items"])


def test_late_completion_after_cancel_is_a_tolerated_noop(tmp_path):
    store = RenderBatchStore(tmp_path)
    store.create(batch_id="srb-x", ideas=_ideas("idea-01", "idea-02"), short_type="infographic",
                 force=False, generation_id=None)
    store.start_item("idea-01")
    store.cancel(error="operator requested stop")

    # The expensive in-flight call returns only now — must not raise and must
    # not overwrite the cancelled outcome.
    doc = store.complete_item("idea-01", short_id="short-late")
    assert doc["status"] == "cancelled"
    assert doc["items"][0]["status"] == "cancelled"
    assert doc["items"][0]["short_id"] is None

    # finish() after a cancel keeps the cancelled terminal state too.
    assert store.finish()["status"] == "cancelled"


# -------------------------------------------------------------------- worker --


def test_resume_returns_legacy_none_without_batch_id(tmp_path):
    assert _resume_render_batch(tmp_path, {"idea_ids": ["idea-01"]}) == (None, None)


def test_resume_fails_closed_on_order_mismatch(tmp_path):
    store = RenderBatchStore(tmp_path)
    store.create(batch_id="srb-x", ideas=_ideas("idea-01", "idea-02"), short_type="infographic",
                 force=False, generation_id=None)

    with pytest.raises(RuntimeError, match="failing closed"):
        _resume_render_batch(
            tmp_path, {"batch_id": "srb-x", "idea_ids": ["idea-02", "idea-01"]}
        )
    with pytest.raises(RuntimeError, match="failing closed"):
        _resume_render_batch(tmp_path, {"batch_id": "srb-other", "idea_ids": ["idea-01", "idea-02"]})


def test_resume_skips_completed_and_resets_stale_running(tmp_path):
    store = RenderBatchStore(tmp_path)
    store.create(batch_id="srb-x", ideas=_ideas("idea-01", "idea-02", "idea-03"),
                 short_type="infographic", force=False, generation_id=None)
    store.start_item("idea-01")
    store.complete_item("idea-01", short_id="short-1")
    store.start_item("idea-02")  # interrupted here

    pending, progress = _resume_render_batch(
        tmp_path, {"batch_id": "srb-x", "idea_ids": ["idea-01", "idea-02", "idea-03"]}
    )

    assert pending == ["idea-02", "idea-03"]
    assert isinstance(progress, BatchProgress)
    doc = RenderBatchStore(tmp_path).load()
    assert doc["items"][0]["status"] == "completed"


def test_terminal_batch_retry_is_intentional_noop(tmp_path):
    """After bug-506 style queue retry of a terminal batch, the worker must
    complete without re-running ideas or raising 'No valid idea IDs selected'."""
    job = _make_parent_job(tmp_path, ["idea-01"])
    store = RenderBatchStore(job)
    store.create(batch_id="srb-x", ideas=_ideas("idea-01"), short_type="infographic",
                 force=False, generation_id="ideas-1")
    store.start_item("idea-01")
    store.fail_item("idea-01", error="boom")
    store.finish()
    assert store.load()["status"] == "failed"

    pending, progress = _resume_render_batch(
        job, {"batch_id": "srb-x", "idea_ids": ["idea-01"]}
    )
    assert pending == []

    # Full worker runner path: returns before any LLM/browser work (client=None
    # would explode if it were touched).
    payload = json.dumps({"idea_ids": ["idea-01"], "batch_id": "srb-x", "force": False})
    _run_short_infographic_job(
        {"job_id": "job-1", "payload": payload},
        job_dir=job, channel_path=Path("/nonexistent-channel.yaml"), client=None,
    )
    assert RenderBatchStore(job).load()["status"] == "failed"  # unchanged


# --------------------------------------------------------- infographic loop --


def test_infographic_loop_orders_events_and_continues_after_failure(tmp_path, monkeypatch):
    from video_agent.shorts.infographic import build as build_mod

    job = _make_parent_job(tmp_path, ["idea-01", "idea-02", "idea-03"])
    store = RenderBatchStore(job)
    store.create(batch_id="srb-x", ideas=_ideas("idea-01", "idea-02", "idea-03"),
                 short_type="infographic", force=False, generation_id="ideas-1")
    progress = _EventLog(store)

    calls: list[str] = []

    def fake_run(short_dir, channel_config, source, **kwargs):
        idea = source["title"].split()[-1]
        calls.append(idea)
        Path(short_dir).mkdir(parents=True, exist_ok=True)
        if idea == "idea-02":
            raise RuntimeError("poster generation failed")
        return {"short_type": "infographic", "status": "rendered", "rendered": True}

    monkeypatch.setattr(build_mod, "run_infographic_short", fake_run)

    build_mod.render_selected_infographic_ideas(
        job, CFG, ["idea-01", "idea-02", "idea-03"],
        image_fn=None, llm_fn=lambda p: "", render_fn=lambda *a, **k: Path("x"),
        progress=progress,
    )

    assert calls == ["idea-01", "idea-02", "idea-03"]  # sequential, order kept
    kinds = [(e[0], e[1]) for e in progress.events if e[0] in ("started", "completed", "failed")]
    assert kinds == [
        ("started", "idea-01"), ("completed", "idea-01"),
        ("started", "idea-02"), ("failed", "idea-02"),
        ("started", "idea-03"), ("completed", "idea-03"),
    ]
    doc = store.load()
    assert doc["status"] == "completed_with_errors"
    assert doc["items"][1]["error"].startswith("poster generation failed")


def test_infographic_in_flight_stop_cancels_snapshot_and_blocks_later_items(tmp_path, monkeypatch):
    """AC8: a stop landing DURING an expensive item must flip the durable
    snapshot to cancelled immediately, and no later idea may start."""
    from video_agent.shorts.infographic import build as build_mod

    job = _make_parent_job(tmp_path, ["idea-01", "idea-02"])
    store = RenderBatchStore(job)
    store.create(batch_id="srb-x", ideas=_ideas("idea-01", "idea-02"),
                 short_type="infographic", force=False, generation_id="ideas-1")
    progress = _EventLog(store)
    snapshot_during_call: dict = {}

    def fake_run(short_dir, channel_config, source, **kwargs):
        # Operator presses Stop while this item is in flight: the route
        # writes the flag and cancels the batch atomically.
        (job / ".stop_requested").write_text("1\n", encoding="utf-8")
        store.cancel(error="operator requested stop")
        snapshot_during_call.update(RenderBatchStore(job).load())
        Path(short_dir).mkdir(parents=True, exist_ok=True)
        return {"short_type": "infographic", "status": "rendered", "rendered": True}

    monkeypatch.setattr(build_mod, "run_infographic_short", fake_run)

    build_mod.render_selected_infographic_ideas(
        job, CFG, ["idea-01", "idea-02"],
        image_fn=None, llm_fn=lambda p: "", render_fn=lambda *a, **k: Path("x"),
        progress=progress,
    )

    # The GET snapshot turned cancelled while the call was still running.
    assert snapshot_during_call["status"] == "cancelled"
    # No event for the later pending item; late completion did not resurrect it.
    started = [e[1] for e in progress.events if e[0] == "started" and e[2]]
    assert started == ["idea-01"]
    doc = store.load()
    assert doc["status"] == "cancelled"
    assert doc["items"][1]["status"] == "cancelled"
    assert doc["remaining_count"] == 0


# ------------------------------------------------------------ narrated loop --


def _fake_build_short(outcomes: dict[str, str]):
    def fake_build(long_job_dir, short_plan, channel_config, **kwargs):
        idea_id = short_plan["idea_id"]
        outcome = outcomes.get(idea_id, "rendered")
        if outcome == "raise":
            raise RuntimeError(f"build failed for {idea_id}")
        short_dir = paths.short_dir(long_job_dir, short_plan["short_id"])
        short_dir.mkdir(parents=True, exist_ok=True)
        manifest.write_short_status(
            long_job_dir, short_plan["short_id"],
            {"short_id": short_plan["short_id"], "idea_id": idea_id,
             "status": outcome, "rendered": outcome == "rendered"},
        )
        return {"short_id": short_plan["short_id"], "idea_id": idea_id,
                "status": outcome, "rendered": outcome == "rendered",
                "qa_verdict": "PASS", "video_path": None}
    return fake_build


def test_narrated_loop_orders_events_and_continues_after_failure(tmp_path):
    from video_agent.shorts.synthesis import render_selected_short_ideas

    job = _make_parent_job(tmp_path, ["idea-01", "idea-02"])
    store = RenderBatchStore(job)
    store.create(batch_id="srb-x", ideas=_ideas("idea-01", "idea-02"),
                 short_type="narrated", force=False, generation_id="ideas-1")
    progress = _EventLog(store)

    render_selected_short_ideas(
        job, CFG, ["idea-01", "idea-02"],
        build_short_fn=_fake_build_short({"idea-01": "raise", "idea-02": "rendered"}),
        progress=progress,
    )

    kinds = [(e[0], e[1]) for e in progress.events if e[0] in ("started", "completed", "failed")]
    assert kinds == [
        ("started", "idea-01"), ("failed", "idea-01"),
        ("started", "idea-02"), ("completed", "idea-02"),
    ]
    assert store.load()["status"] == "completed_with_errors"


def test_narrated_all_failed_batch_is_terminal_failed(tmp_path):
    from video_agent.shorts.synthesis import render_selected_short_ideas

    job = _make_parent_job(tmp_path, ["idea-01", "idea-02"])
    store = RenderBatchStore(job)
    store.create(batch_id="srb-x", ideas=_ideas("idea-01", "idea-02"),
                 short_type="narrated", force=False, generation_id="ideas-1")

    render_selected_short_ideas(
        job, CFG, ["idea-01", "idea-02"],
        build_short_fn=_fake_build_short({"idea-01": "raise", "idea-02": "raise"}),
        progress=BatchProgress(store),
    )

    doc = store.load()
    assert doc["status"] == "failed"
    assert doc["remaining_count"] == 0
    # Terminal retry drains to no pending work (worker no-op path).
    pending, _ = _resume_render_batch(
        job, {"batch_id": "srb-x", "idea_ids": ["idea-01", "idea-02"]}
    )
    assert pending == []


# ---------------------------------------------------------------- stop route --


@pytest.fixture
def client(tmp_path: Path):
    app.dependency_overrides[get_jobs_root] = lambda: tmp_path
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_stop_route_cancels_active_batch_immediately(client, tmp_path):
    job = _make_parent_job(tmp_path, ["idea-01", "idea-02"])
    store = RenderBatchStore(job)
    store.create(batch_id="srb-x", ideas=_ideas("idea-01", "idea-02"),
                 short_type="infographic", force=False, generation_id="ideas-1")
    store.start_item("idea-01")  # in-flight item

    response = client.post("/jobs/job-1/stop")

    assert response.status_code == 200
    assert response.json()["batch_cancelled"] is True
    snapshot = client.get("/shorts-studio/jobs/job-1/ideas/render-batch").json()
    assert snapshot["status"] == "cancelled"
    assert snapshot["remaining_count"] == 0
    assert [i["status"] for i in snapshot["items"]] == ["cancelled", "cancelled"]


# ------------------------------------------- AC8 cooperative in-item stop --


def _stage_fns(job: Path, stop_after: str):
    """Real run_infographic_short deps with call spies; the ``stop_after``
    stage writes the operator's stop flag before returning (a stop landing
    while that call is in flight — it completes, later stages must not run)."""
    calls: list[str] = []

    def llm_fn(prompt):
        calls.append("llm")
        if stop_after == "plan":
            (job / ".stop_requested").write_text("1\n", encoding="utf-8")
        return json.dumps({
            "poster_format": "numbered_tips", "title": "Sal en cinco pasos",
            "hook_line": "Sal: compárala en 5 pasos",
            "items": [{"label": f"Paso {n}"} for n in range(1, 6)], "cta": "Sigue",
        })

    async def image_fn(*, prompt, project_name, out_path, aspect_ratio="16:9"):
        calls.append("image")
        if stop_after == "poster":
            (job / ".stop_requested").write_text("1\n", encoding="utf-8")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG")
        return {"bytes": 4}

    def music_fn(short_dir, music_track, cfg, duration_sec):
        calls.append("music")
        p = Path(short_dir) / "audio" / "infographic_bgm.m4a"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RIFF")
        return p

    def render_fn(short_dir, props):
        calls.append("render")
        out = Path(short_dir) / "outputs" / "short.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out

    return calls, llm_fn, image_fn, music_fn, render_fn


def test_stop_during_plan_stage_runs_nothing_after_the_boundary(tmp_path):
    """AC8 stage-level: stop lands while the PLAN call is in flight; the call
    returns, and no poster/music/SEO/render work may start afterwards."""
    from video_agent.shorts.infographic.build import (
        InfographicStopRequested,
        run_infographic_short,
    )

    job = _make_parent_job(tmp_path, ["idea-01"])
    short_dir = job / "shorts" / "short-01_idea-01_stop"
    calls, llm_fn, image_fn, music_fn, render_fn = _stage_fns(job, stop_after="plan")

    with pytest.raises(InfographicStopRequested):
        run_infographic_short(
            short_dir, CFG, {"topic": "sal", "title": "Sal"},
            image_fn=image_fn, llm_fn=llm_fn, music_fn=music_fn,
            render_fn=render_fn, read_text_fn=None,
        )

    assert calls == ["llm"]  # plan returned; poster/music/render never started
    status = json.loads((short_dir / paths.SHORT_STATUS_FILE).read_text())
    assert status["status"] == "cancelled"
    assert status["rendered"] is False
    assert status["stop_requested"] is True


def test_stop_during_poster_stage_stops_before_music_and_render(tmp_path):
    from video_agent.shorts.infographic.build import (
        InfographicStopRequested,
        run_infographic_short,
    )

    job = _make_parent_job(tmp_path, ["idea-01"])
    short_dir = job / "shorts" / "short-01_idea-01_stop2"
    calls, llm_fn, image_fn, music_fn, render_fn = _stage_fns(job, stop_after="poster")

    with pytest.raises(InfographicStopRequested):
        run_infographic_short(
            short_dir, CFG, {"topic": "sal", "title": "Sal"},
            image_fn=image_fn, llm_fn=llm_fn, music_fn=music_fn,
            render_fn=render_fn, read_text_fn=None,
        )

    assert calls == ["llm", "image"]  # poster call finished; nothing after
    status = json.loads((short_dir / paths.SHORT_STATUS_FILE).read_text())
    assert status["status"] == "cancelled"


def test_loop_stop_mid_item_cancels_batch_and_never_starts_later_ideas(tmp_path):
    """AC8 end-to-end through the sequential loop with the REAL pipeline:
    stop during idea-01's plan stage — its later stages never run, the batch
    ends cancelled, idea-02 never starts, and no rendered manifest entry
    appears for either idea."""
    from video_agent.shorts.infographic.build import render_selected_infographic_ideas

    job = _make_parent_job(tmp_path, ["idea-01", "idea-02"])
    store = RenderBatchStore(job)
    store.create(batch_id="srb-x", ideas=_ideas("idea-01", "idea-02"),
                 short_type="infographic", force=False, generation_id="ideas-1")
    progress = _EventLog(store)
    calls, llm_fn, image_fn, music_fn, render_fn = _stage_fns(job, stop_after="plan")

    result = render_selected_infographic_ideas(
        job, CFG, ["idea-01", "idea-02"],
        image_fn=image_fn, llm_fn=llm_fn, music_fn=music_fn, render_fn=render_fn,
        read_text_fn=None, progress=progress,
    )

    assert calls == ["llm"]  # idea-01 stopped at the plan boundary; idea-02 never ran
    doc = store.load()
    assert doc["status"] == "cancelled"
    assert [i["status"] for i in doc["items"]] == ["cancelled", "cancelled"]
    started = [e[1] for e in progress.events if e[0] == "started" and e[2]]
    assert started == ["idea-01"]
    statuses = [r["status"] for r in result["shorts"]]
    assert statuses == ["cancelled"]
    assert not any(r.get("rendered") for r in result["shorts"])


def test_cooperative_stop_makes_every_status_surface_agree_on_cancelled(tmp_path):
    """AC8 terminal agreement: after a real cooperative-stop loop, render_batch,
    per-short status/result, manifest top-level, studio_render_run, and the
    /shorts-studio/state job badge must ALL read cancelled — never failed."""
    from fastapi.testclient import TestClient

    from video_agent.shorts import paths as sp
    from video_agent.shorts.infographic.build import render_selected_infographic_ideas
    from video_agent.web.app import app, get_jobs_root

    job = _make_parent_job(tmp_path, ["idea-01", "idea-02"])
    # A PRIOR rendered manifest entry must NOT relabel the stopped run completed.
    prior_manifest = {
        "mode": "synthesis_ideas", "status": "completed",
        "shorts": [{"short_id": "short-99_old", "idea_id": "idea-99",
                    "rendered": True, "status": "rendered"}],
    }
    (job / "shorts" / sp.MANIFEST_FILE).write_text(json.dumps(prior_manifest), encoding="utf-8")

    store = RenderBatchStore(job)
    store.create(batch_id="srb-x", ideas=_ideas("idea-01", "idea-02"),
                 short_type="infographic", force=False, generation_id="ideas-1")
    progress = _EventLog(store)
    _calls, llm_fn, image_fn, music_fn, render_fn = _stage_fns(job, stop_after="plan")

    render_selected_infographic_ideas(
        job, CFG, ["idea-01", "idea-02"],
        image_fn=image_fn, llm_fn=llm_fn, music_fn=music_fn, render_fn=render_fn,
        read_text_fn=None, progress=progress,
    )

    # 1. batch document
    assert store.load()["status"] == "cancelled"
    # 2. per-short status.json written by the in-pipeline guard
    short_id = next(
        p.name for p in (job / "shorts").iterdir()
        if p.is_dir() and p.name.startswith("short-01_idea-01")
    )
    st = json.loads((job / "shorts" / short_id / sp.SHORT_STATUS_FILE).read_text())
    assert st["status"] == "cancelled" and st["rendered"] is False
    # 3. manifest top-level — the prior rendered entry did NOT win
    manifest = json.loads((job / "shorts" / sp.MANIFEST_FILE).read_text())
    assert manifest["status"] == "cancelled"
    # 4. studio_render_run — cancelled, no failed tally
    run = json.loads(sp.studio_render_run_path(job).read_text())
    assert run["status"] == "cancelled"
    assert run["failed_count"] == 0
    assert run["cancelled_count"] == 1
    # 5. /shorts-studio/state job badge
    app.dependency_overrides[get_jobs_root] = lambda: tmp_path
    try:
        client = TestClient(app)
        state = client.get("/shorts-studio/state").json()
        job_entry = next(j for j in state["jobs"] if j["job_id"] == "job-1")
        assert job_entry["shorts_status"] == "cancelled"
    finally:
        app.dependency_overrides.clear()


def test_ordinary_all_failed_stays_failed_not_cancelled(tmp_path):
    """Regression guard: cancelled handling must not relabel a genuine
    all-failed infographic run — that stays 'failed'."""
    from video_agent.shorts import paths as sp
    from video_agent.shorts.infographic import build as build_mod

    job = _make_parent_job(tmp_path, ["idea-01"])
    store = RenderBatchStore(job)
    store.create(batch_id="srb-x", ideas=_ideas("idea-01"), short_type="infographic",
                 force=False, generation_id="ideas-1")

    def boom(*a, **k):
        raise RuntimeError("poster failed")

    orig = build_mod.run_infographic_short
    build_mod.run_infographic_short = boom
    try:
        build_mod.render_selected_infographic_ideas(
            job, CFG, ["idea-01"], image_fn=None, llm_fn=lambda p: "",
            render_fn=lambda *a, **k: Path("x"), progress=BatchProgress(store),
        )
    finally:
        build_mod.run_infographic_short = orig

    run = json.loads(sp.studio_render_run_path(job).read_text())
    assert run["status"] == "failed"
    assert run["failed_count"] == 1
    assert run["cancelled_count"] == 0
    assert store.load()["status"] == "failed"
