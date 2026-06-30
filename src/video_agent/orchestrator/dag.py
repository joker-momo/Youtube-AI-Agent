"""Dependency-DAG scheduler for the long-form pipeline (parallel lanes).

The legacy pipeline runs stages strictly serially via a single ``current_stage``
pointer. This module models the post-``scenes`` stages as a dependency DAG grouped
by *resource* so independent lanes run concurrently:

- ``chatgpt``  — one shared browser session → serialized (lock).
- ``local_mps`` — TTS / whisper / render → serialized (avoid GPU thrash).
- ``cpu``      — light report-only stages → run in parallel.

Pure scheduling only: ``ready_stages`` is side-effect-free and unit-testable;
``DagScheduler.run`` drives a caller-supplied ``run_stage`` coroutine. The whole
DAG path is opt-in (``pipeline.parallel_dag``); the linear path is untouched.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping

logger = logging.getLogger("video_agent.orchestrator.dag")

# Long-form stage dependencies. Keys are DAG nodes; values are the stages that
# must be ``completed`` first. ``script``/``scenes`` form a serial spine handled
# before the DAG; the executor schedules the post-``scenes`` subset below.
STAGE_DEPS: dict[str, list[str]] = {
    "script": [],
    "scenes": ["script"],
    # --- post-scenes DAG ---
    "seo": [],
    "graphic_images": [],
    "thumbnail_image": ["seo"],
    "whisper_timestamps": [],
    "visual_spans": [],
    "visual_schedule": ["visual_spans"],  # reads visual_spans.json
    "render": [
        "seo", "graphic_images", "thumbnail_image", "whisper_timestamps",
        "visual_schedule", "visual_spans",
    ],
    "render_continuity_qa": ["render"],
    "review": ["render_continuity_qa"],
}

# Resource bucket per stage. ``chatgpt`` + ``local_mps`` are serialized; ``cpu``
# runs free (parallel).
STAGE_RESOURCE: dict[str, str] = {
    "script": "chatgpt", "scenes": "chatgpt", "seo": "chatgpt",
    "graphic_images": "chatgpt", "thumbnail_image": "chatgpt",
    "whisper_timestamps": "local_mps", "render": "local_mps",
    "visual_spans": "cpu", "visual_schedule": "cpu",
    "render_continuity_qa": "cpu", "review": "cpu",
}

# Stages whose resource is run one-at-a-time.
SERIALIZED_RESOURCES: frozenset[str] = frozenset({"chatgpt", "local_mps"})

# The post-scenes subset scheduled by the DAG executor (the serial spine
# script→scenes runs before it).
POST_SCENES_STAGES: tuple[str, ...] = (
    "seo", "graphic_images", "thumbnail_image", "whisper_timestamps",
    "visual_spans", "visual_schedule",
    "render", "render_continuity_qa", "review",
)

_DONE = "completed"
_BLOCKED = {"failed", "skipped"}


def ready_stages(
    status: Mapping[str, str], deps: Mapping[str, list[str]] = STAGE_DEPS
) -> list[str]:
    """Pending stages whose every in-scope dependency is ``completed``.

    Deps not present in ``status`` are treated as already satisfied (e.g. the
    pre-DAG spine). A pending stage with a failed/skipped dep is NOT ready.
    """
    out: list[str] = []
    for stage, st in status.items():
        if st != "pending":
            continue
        if all(
            status.get(dep) == _DONE
            for dep in deps.get(stage, [])
            if dep in status
        ):
            out.append(stage)
    return out


class DagScheduler:
    """Run a set of stages honouring deps + per-resource serialization.

    ``run_stage(name)`` is an async callable that executes one stage and raises
    on failure. One failed stage marks its dependents ``skipped`` but never
    cancels independent lanes.
    """

    def __init__(
        self,
        deps: Mapping[str, list[str]] = STAGE_DEPS,
        resource: Mapping[str, str] = STAGE_RESOURCE,
    ) -> None:
        self.deps = deps
        self.resource = resource

    async def run(
        self, stages: Iterable[str], run_stage: Callable[[str], Awaitable[None]]
    ) -> dict[str, str]:
        """Execute ``stages``; return {stage: completed|failed|skipped}."""
        stage_list = list(stages)
        events: dict[str, asyncio.Event] = {s: asyncio.Event() for s in stage_list}
        result: dict[str, str] = {}
        locks: dict[str, asyncio.Lock] = {}

        async def _one(stage: str) -> None:
            in_scope_deps = [d for d in self.deps.get(stage, []) if d in events]
            for dep in in_scope_deps:
                await events[dep].wait()
            if any(result.get(d) in _BLOCKED for d in in_scope_deps):
                result[stage] = "skipped"
                logger.warning("DAG: skip %s (upstream failed)", stage)
                events[stage].set()
                return
            res = self.resource.get(stage, "cpu")
            try:
                if res in SERIALIZED_RESOURCES:
                    lock = locks.setdefault(res, asyncio.Lock())
                    async with lock:
                        await run_stage(stage)
                else:
                    await run_stage(stage)
                result[stage] = "completed"
            except Exception:  # noqa: BLE001 - isolate per-stage failure
                result[stage] = "failed"
                logger.exception("DAG stage failed: %s", stage)
            finally:
                events[stage].set()

        await asyncio.gather(*(_one(s) for s in stage_list))
        return result
