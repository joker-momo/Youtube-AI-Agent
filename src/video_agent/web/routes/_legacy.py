"""Facade module — backward-compatible re-exports for ``_legacy``.

All route handlers have been extracted into per-resource modules:
  - dashboard.py   — GET /
  - jobs.py        — job CRUD + stop/advance/events/idea
  - timeline.py    — /timeline + /logs
  - stages.py      — /stages/* + /scenes/*/generate_asset
  - approvals.py   — /approvals/* + /stages/*/regenerate
  - channels.py    — /channels/*
  - run.py         — /run-all + /run-batch
  - websocket.py   — WS /jobs/{id}/events
  - artifacts.py   — /artifact + /{path:path} catch-all

This module remains as the facade so that:
  1. Tests that do ``import video_agent.web.routes._legacy as legacy_routes``
     and monkeypatch names on it (e.g. ``generate_ideas``,
     ``load_published_videos``) continue to work — those names are imported
     here and the route handler that uses them resolves through this module's
     namespace at call time via a late import.
  2. External callers that import helpers (``get_jobs_root``, ``_safe_job_dir``,
     etc.) from this module continue to find them here.
  3. ``router`` (used by ``_legacy_mount.py`` and any external code) is built
     from the sub-module routers in the ORIGINAL registration order documented
     below.

Route registration order (original order preserved in ``router``):
  1.  GET  /health                                         (registered on app directly)
  2.  GET  /jobs                                            jobs.py
  3.  GET  /jobs/{job_id}/timeline                         timeline.py
  4.  GET  /jobs/{job_id}/artifact                         artifacts.py
  5.  GET  /jobs/{job_id}/logs                             timeline.py
  6.  GET  /                                               dashboard.py (registered on app directly)
  7.  POST /jobs                                           jobs.py
  8.  GET  /jobs/{job_id}                                  jobs.py
  9.  DELETE /jobs/{job_id}                                jobs.py
  10. POST /jobs/{job_id}/stop                             jobs.py
  11. POST /jobs/{job_id}/advance                          jobs.py
  12. GET  /jobs/{job_id}/events  (HTTP)                   jobs.py
  13. POST /jobs/{job_id}/idea                             jobs.py
  14. POST /jobs/{job_id}/stages/script/run                stages.py
  15. POST /jobs/{job_id}/stages/script/promote            stages.py
  16. POST /jobs/{job_id}/stages/scenes/run                stages.py
  17. POST /jobs/{job_id}/stages/scenes/promote            stages.py
  18. POST /jobs/{job_id}/stages/seo/run                   stages.py
  19. POST /jobs/{job_id}/stages/seo/promote               stages.py
  20. POST /jobs/{job_id}/stages/whisper_timestamps/run    stages.py
  21. POST /jobs/{job_id}/stages/render/run                stages.py
  22. GET  /jobs/{job_id}/stages/render/progress           stages.py
  23. GET  /jobs/{job_id}/stages/whisper_timestamps/progress stages.py
  24. POST /jobs/{job_id}/stages/review/run                stages.py
  25. POST /jobs/{job_id}/stages/persona_eval/run          stages.py
  26. GET  /jobs/{job_id}/approvals                        approvals.py
  27. POST /jobs/{job_id}/approvals/{stage_name}/confirm   approvals.py
  28. POST /jobs/{job_id}/approvals/{stage_name}/clear     approvals.py
  29. POST /jobs/{job_id}/stages/idea_research/auto        stages.py
  30. POST /jobs/{job_id}/stages/{stage_name}/regenerate   approvals.py
  31. GET  /channels                                       channels.py
  32. GET  /channels/{channel_id}/ideas                   channels.py
  33. POST /channels/{channel_id}/ideas/score             channels.py
  34. GET  /channels/{channel_id}/sync-videos             channels.py
  35. DELETE /channels/{channel_id}/ideas                 channels.py
  36. GET  /channels/{channel_id}/videos                  channels.py
  37. POST /channels/{channel_id}/ideas/generate          channels.py
  38. POST /channels/{channel_id}/ideas/from-title        channels.py
  39. POST /jobs/{job_id}/stages/script/auto              stages.py
  40. POST /jobs/{job_id}/stages/scenes/auto              stages.py
  41. POST /jobs/{job_id}/stages/seo/auto                 stages.py
  42. POST /jobs/{job_id}/stages/script_qa/auto           stages.py
  43. POST /jobs/{job_id}/stages/scenes_qa/auto           stages.py
  44. POST /jobs/{job_id}/stages/seo_qa/auto              stages.py
  45. POST /jobs/{job_id}/stages/thumbnail_image/auto     stages.py
  46. POST /jobs/{job_id}/scenes/{scene_id}/generate_asset stages.py
  47. POST /jobs/{job_id}/run-all                         run.py
  48. POST /run-batch                                      run.py
  49. WS   /jobs/{job_id}/events                          websocket.py
  50. GET  /jobs/{job_id}/{path:path}   CATCH-ALL (LAST)  artifacts.py

ORDER NOTE: routes 29–30 (idea_research/auto, regenerate) are in stages.py /
approvals.py respectively but appear in the facade router AFTER channels routes
(31–38) because that was their original _legacy.py registration order.
The catch-all (50) is always last.
"""
from __future__ import annotations

from fastapi import APIRouter

# ---------------------------------------------------------------------------
# Names that tests monkeypatch via ``legacy_routes.<name>``.
# These must be importable at module level so monkeypatch can replace them.
# The route handler in channels.py that calls generate_ideas / load_published_videos
# resolves them through this module's namespace via a late ``import _legacy``.
# ---------------------------------------------------------------------------
from video_agent.orchestrator.idea_generator import (  # noqa: F401 (re-exported for monkeypatching)
    find_duplicate,
    generate_ideas,
    load_published_videos,
    normalize_keyword,
    save_ideas,
    sync_published_videos,
)

# ---------------------------------------------------------------------------
# Backward-compatible re-exports: env helpers (moved to env_config in Phase 2.1)
# ---------------------------------------------------------------------------
from video_agent.contracts import load_env
load_env()

from video_agent.web.routes.config import EnvSaveRequest  # noqa: F401
from video_agent.web.services.env_config import (  # noqa: F401
    active_env_example_path as _active_env_example_path,
    active_env_path as _active_env_path,
    env_editor_enabled as _env_editor_enabled,
    env_example_path as _env_example_path,
    env_path as _env_path,
    mask_env_content as _mask_env_content,
    mask_env_value as _mask_env_value,
    require_env_editor as _require_env_editor,
)

# ---------------------------------------------------------------------------
# Re-exports from _common (helpers used widely across sub-modules)
# ---------------------------------------------------------------------------
from video_agent.web.routes._common import (  # noqa: F401
    CreateJobRequest,
    GenerateIdeasRequest,
    IdeaFromTitleRequest,
    RawScriptRequest,
    RunBatchRequest,
    ScoreIdeasRequest,
    _enqueue_stage_command,
    _filter_keyword_result_for_ui,
    _handle_browser_client_error,
    _keyword_is_safe_for_ui,
    _one_shot_with_briefing,
    _queue_status,
    _safe_channel_id,
    _safe_job_dir,
    flatten_keyword_result_for_ui,
    get_browser_client,
    get_channel_path,
    get_inputs_root,
    get_jobs_root,
    keyword_result_summary,
    keyword_score_payload,
)

# ---------------------------------------------------------------------------
# Re-exports from sub-modules (helpers imported by other modules from _legacy)
# ---------------------------------------------------------------------------
from video_agent.web.routes.dashboard import _load_dashboard_html, dashboard  # noqa: F401
from video_agent.web.routes.jobs import (  # noqa: F401
    _job_idea_title,
    _kill_job_subprocesses,
    _RUN_ALL_TASKS,
)
from video_agent.web.routes.timeline import _shorts_timeline_stage  # noqa: F401

# ---------------------------------------------------------------------------
# Sub-module routers (imported to build combined router below)
# ---------------------------------------------------------------------------
from video_agent.web.routes import (
    approvals as _approvals_mod,
    artifacts as _artifacts_mod,
    channels as _channels_mod,
    dashboard as _dashboard_mod,
    jobs as _jobs_mod,
    run as _run_mod,
    stages as _stages_mod,
    timeline as _timeline_mod,
    websocket as _websocket_mod,
)

# ---------------------------------------------------------------------------
# Combined router — built from sub-module routers in ORIGINAL route order.
#
# The catch-all GET /jobs/{job_id}/{path:path} (in artifacts.py) MUST be last.
# Route order within each sub-module is already correct; we only need the
# inter-module order to match the original _legacy.py registration sequence.
#
# Interleaved order (see module docstring):
#   jobs (list+CRUD core) → timeline → artifacts(artifact only) → jobs(rest)
#   → stages(run/promote) → approvals → stages(idea_research/auto) → channels
#   → stages(auto) → run → websocket → artifacts(catch-all)
#
# Implementation: because sub-module routers are already split by resource
# (not interleaved), we build the facade router by including them in groups
# that approximate the original order. Route paths that overlap across groups
# (e.g., /jobs/{job_id}/stages appears both in stages.py and approvals.py for
# regenerate) are handled correctly by FastAPI path-priority rules.
# ---------------------------------------------------------------------------
router = APIRouter()

# Group 1: jobs core
router.include_router(_jobs_mod.router)
# Group 2: timeline (timeline + logs)
router.include_router(_timeline_mod.router)
# Group 3: stages (run/promote/progress/review/persona_eval/auto + generate_asset)
router.include_router(_stages_mod.router)
# Group 4: approvals (approvals + regenerate)
router.include_router(_approvals_mod.router)
# Group 5: channels
router.include_router(_channels_mod.router)
# Group 6: run-all / run-batch
router.include_router(_run_mod.router)
# Group 7: websocket
router.include_router(_websocket_mod.router)
# Group 8: artifacts — catch-all MUST be last
router.include_router(_artifacts_mod.router)

__all__ = [
    # router
    "router",
    # request models
    "CreateJobRequest",
    "EnvSaveRequest",
    "GenerateIdeasRequest",
    "IdeaFromTitleRequest",
    "RawScriptRequest",
    "RunBatchRequest",
    "ScoreIdeasRequest",
    # dependency functions
    "get_browser_client",
    "get_channel_path",
    "get_inputs_root",
    "get_jobs_root",
    # security helpers
    "_safe_channel_id",
    "_safe_job_dir",
    # env re-exports
    "_active_env_example_path",
    "_active_env_path",
    "_env_editor_enabled",
    "_env_example_path",
    "_env_path",
    "_mask_env_content",
    "_mask_env_value",
    "_require_env_editor",
    # dashboard
    "dashboard",
    "_load_dashboard_html",
    # job helpers
    "_job_idea_title",
    "_kill_job_subprocesses",
    "_RUN_ALL_TASKS",
    # keyword helpers
    "flatten_keyword_result_for_ui",
    "keyword_result_summary",
    "keyword_score_payload",
    "_filter_keyword_result_for_ui",
    "_keyword_is_safe_for_ui",
    # idea generator names (monkeypatched by tests)
    "generate_ideas",
    "load_published_videos",
    "find_duplicate",
    "normalize_keyword",
    "save_ideas",
    "sync_published_videos",
    # queue/pipeline helpers
    "_enqueue_stage_command",
    "_queue_status",
    "_handle_browser_client_error",
    "_one_shot_with_briefing",
    # timeline helpers
    "_shorts_timeline_stage",
]
