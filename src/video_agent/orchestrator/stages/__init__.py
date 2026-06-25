"""Facade package: re-exports every public (and private) symbol from all
submodules so that ``from video_agent.orchestrator.stages import X`` and
``video_agent.orchestrator.stages.X`` work exactly as before.

Import path contract: ``video_agent.orchestrator.stages`` is identical to
the old monolithic ``stages.py``.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Re-export contracts / operator symbols that callers import directly from
# this package (e.g. ``from video_agent.orchestrator.stages import repo_root``)
# ---------------------------------------------------------------------------
from video_agent.contracts import (
    repo_root,
    ARTIFACT_SEO,
    ARTIFACT_SCRIPT,
    ARTIFACT_SCENES,
    ARTIFACT_VISUAL_REVIEW,
    ARTIFACT_THUMBNAIL,
    ARTIFACT_VIDEO,
    ARTIFACT_REPORT,
    ARTIFACT_VISUAL_CONTACT_SHEET,
    ARTIFACT_PERSONA_EVAL,
    EVENT_LOG,
)
from video_agent.operator import (
    write_operator_review,
    extract_json_objects,
)
from video_agent.pipeline import render_operator_job

# ---------------------------------------------------------------------------
# _shared
# ---------------------------------------------------------------------------
from video_agent.orchestrator.stages._shared import (
    StageInputMissingError,
    _resolve_idea_path,
    _resolve_artifact,
    _run_blocking_with_timeout,
    _start_stage,
    _complete_stage,
    IDEA_FILE,
    _IDEA_FILE_LEGACY,
    _AUDIO_SUBPROCESS_ENV,
    SCRIPT_PROMPT_PATH,
    SCRIPT_RAW_PATH,
    SCENES_PROMPT_PATH,
    SCENES_RAW_PATH,
    SCENES_PLAN_PATH,
    SCENES_BATCHES_DIR,
    SCENES_QA_BATCHES_DIR,
    SEO_PROMPT_PATH,
    SEO_RAW_PATH,
    SCRIPT_QA_RAW_PATH,
    SCENES_QA_RAW_PATH,
    SEO_QA_RAW_PATH,
)

# ---------------------------------------------------------------------------
# script
# ---------------------------------------------------------------------------
from video_agent.orchestrator.stages.script import (
    run_script_stage,
    promote_script_stage,
    auto_script_stage,
)

# ---------------------------------------------------------------------------
# scenes
# ---------------------------------------------------------------------------
from video_agent.orchestrator.stages.scenes import (
    run_scenes_stage,
    promote_scenes_stage,
    auto_scenes_stage,
    _enforce_scenes_visual_prompt_english,
)

# ---------------------------------------------------------------------------
# sharding
# ---------------------------------------------------------------------------
from video_agent.orchestrator.stages.sharding import (
    _request_shard_envelope,
    _scene_id_to_batch_index,
    _scene_ids_from_validation_error,
    _scene_batch_repair_prompt,
    _merge_scene_batches_with_repair,
    auto_scenes_stage_sharded,
    auto_scenes_qa_stage_sharded,
)

# ---------------------------------------------------------------------------
# seo
# ---------------------------------------------------------------------------
from video_agent.orchestrator.stages.seo import (
    run_seo_stage,
    promote_seo_stage,
    auto_seo_stage,
    _enforce_seo_language_qa,
)

# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------
from video_agent.orchestrator.stages.audio import (
    _run_audio_subprocess,
    run_whisper_timestamps_stage,
    _rebase_words_to_scene_timestamps,
    _run_whisper_timestamps_stage_inline,
)

# ---------------------------------------------------------------------------
# visual_spans / visual_schedule (long-form, report-only)
# ---------------------------------------------------------------------------
from video_agent.orchestrator.stages.visual_spans import (
    run_visual_spans_stage,
)
from video_agent.orchestrator.stages.visual_schedule import (
    run_visual_schedule_stage,
)
from video_agent.orchestrator.stages.graphic_images import (
    run_graphic_images_stage,
)

# ---------------------------------------------------------------------------
# render_review
# ---------------------------------------------------------------------------
from video_agent.orchestrator.stages.render_review import (
    run_render_stage,
    run_review_stage,
    run_persona_eval_stage,
)

# ---------------------------------------------------------------------------
# qa
# ---------------------------------------------------------------------------
from video_agent.orchestrator.stages.qa import (
    _QA_ARTIFACT_FILE,
    _QA_RAW_PATH,
    promote_qa_stage,
    _auto_run_then_promote,
    _auto_qa,
    auto_script_qa_stage,
    auto_scenes_qa_stage,
    auto_seo_qa_stage,
    _reset_promote_and_qa,
    auto_rework_artifact,
    _max_retries_per_qa,
    auto_qa_with_rework,
)

# ---------------------------------------------------------------------------
# assets_thumbnail
# ---------------------------------------------------------------------------
from video_agent.orchestrator.stages.assets_thumbnail import (
    RESEARCH_FILE,
    _idea_keywords,
    auto_idea_research_stage,
    _ASSET_GEN_PROMPT_PREFIX,
    _scene_project_name,
    _VARIANT_STRATEGY,
    _topic_category_guidance,
    _build_thumbnail_prompt,
    _legacy_build_thumbnail_prompt,
    generate_scene_asset,
    auto_assets_chatgpt_stage,
    auto_thumbnail_image_stage,
)

# ---------------------------------------------------------------------------
# Type aliases (kept at facade level for backward compatibility with callers
# that do ``from video_agent.orchestrator.stages import SessionFn``)
# ---------------------------------------------------------------------------
from typing import Awaitable, Callable, Sequence

PromptFn = Callable[[str], Awaitable[str]]
"""Async callable: takes a prompt string, returns the raw model response."""

SessionFn = Callable[[Sequence[str]], Awaitable[str]]
"""Async callable: takes a list of messages to send in one temp chat,
returns the last assistant response."""

# ---------------------------------------------------------------------------
# Populate cross-submodule dicts that reference functions from multiple
# submodules. These must be set at facade level AFTER all imports so that
# auto_qa_with_rework (in qa.py) can access them via late facade import.
# ---------------------------------------------------------------------------
# NOTE: adding a new artifact type? Update BOTH dicts here AND they are synced
# into the qa submodule below via _qa_mod.*.update(...) — qa.auto_qa_with_rework
# and qa.auto_rework_artifact read them through the facade at call time.
_QA_STAGE_FN = {
    "script": auto_script_qa_stage,
    "scenes": auto_scenes_qa_stage,
    "seo": auto_seo_qa_stage,
}

_ARTIFACT_PROMOTER = {
    "script": promote_script_stage,
    "scenes": promote_scenes_stage,
    "seo": promote_seo_stage,
}

_ARTIFACT_RAW_PATH = {
    "script": SCRIPT_RAW_PATH,
    "scenes": SCENES_RAW_PATH,
    "seo": SEO_RAW_PATH,
}

# Also update qa.py's local copies so they stay in sync when accessed
# directly from that module.
from video_agent.orchestrator.stages import qa as _qa_mod
_qa_mod._QA_STAGE_FN.update(_QA_STAGE_FN)
_qa_mod._ARTIFACT_PROMOTER.update(_ARTIFACT_PROMOTER)
