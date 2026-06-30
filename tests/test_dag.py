"""Unit tests for the parallel-pipeline DAG scheduler (orchestrator/dag.py)."""
from __future__ import annotations

import asyncio

from video_agent.orchestrator.dag import (
    POST_SCENES_STAGES,
    STAGE_DEPS,
    DagScheduler,
    ready_stages,
)


def test_ready_stages_gates_on_deps() -> None:
    status = {
        "visual_spans": "completed",
        "visual_schedule": "pending",
        "render": "pending",
    }
    r = ready_stages(status)
    assert "visual_schedule" in r  # its dep (visual_spans) is completed
    assert "render" not in r  # dep visual_schedule still pending


def test_ready_stages_blocked_by_failed_dep() -> None:
    status = {"visual_spans": "failed", "visual_schedule": "pending"}
    assert "visual_schedule" not in ready_stages(status)


def test_ready_stages_deps_outside_scope_treated_satisfied() -> None:
    # render's deps are absent from status → treated as satisfied; seo has none.
    assert ready_stages({"seo": "pending"}) == ["seo"]
    assert "render" in ready_stages({"render": "pending"})


def test_scheduler_cpu_parallel_serialized_resources_serial() -> None:
    deps = {"a": [], "b": [], "c": [], "x": [], "y": []}
    resource = {"a": "cpu", "b": "cpu", "c": "cpu", "x": "chatgpt", "y": "chatgpt"}
    cur = {"cpu": 0, "chatgpt": 0}
    mx = {"cpu": 0, "chatgpt": 0}

    async def run_stage(name: str) -> None:
        res = resource[name]
        cur[res] += 1
        mx[res] = max(mx[res], cur[res])
        await asyncio.sleep(0.02)
        cur[res] -= 1

    result = asyncio.run(DagScheduler(deps, resource).run(list(resource), run_stage))
    assert all(v == "completed" for v in result.values())
    assert mx["cpu"] >= 2  # cpu lane ran in parallel
    assert mx["chatgpt"] == 1  # chatgpt serialized to one session


def test_scheduler_error_isolation_and_skip() -> None:
    deps = {"bad": [], "dependent": ["bad"], "indep": []}
    resource = {"bad": "cpu", "dependent": "cpu", "indep": "cpu"}

    async def run_stage(name: str) -> None:
        if name == "bad":
            raise RuntimeError("boom")
        await asyncio.sleep(0.01)

    result = asyncio.run(DagScheduler(deps, resource).run(list(deps), run_stage))
    assert result["bad"] == "failed"
    assert result["dependent"] == "skipped"  # upstream failed → skipped
    assert result["indep"] == "completed"  # independent lane unaffected


def test_scheduler_render_runs_after_all_deps() -> None:
    order: list[str] = []

    async def run_stage(name: str) -> None:
        order.append(name)
        await asyncio.sleep(0.005)

    result = asyncio.run(DagScheduler().run(list(POST_SCENES_STAGES), run_stage))
    assert all(v == "completed" for v in result.values())
    for dep in STAGE_DEPS["render"]:
        assert order.index(dep) < order.index("render")
    assert order.index("render") < order.index("review")
