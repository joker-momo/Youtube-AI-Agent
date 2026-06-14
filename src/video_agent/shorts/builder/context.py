"""Shared mutable context threaded through the per-stage build functions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from video_agent.shorts import llm_history


@dataclass
class BuildContext:
    # Paths
    long_job_dir: Path
    short_dir: Path
    json_dir: Path
    outputs_dir: Path

    # Plan / config
    short_plan: dict[str, Any]
    plan_for_prompt: dict[str, Any]
    channel_config: dict[str, Any]

    # Injected side-effect functions (already wrapped with recorder + retries)
    llm_fn: Callable[..., str]
    gemini_fn: Callable[[str], str] | None
    background_fn: Callable[..., None]
    tts_fn: Callable[..., Path]
    mix_fn: Callable[..., Path]
    render_fn: Callable[..., Path]
    cover_fn: Callable[..., Path]

    # Shared mutable orchestration state
    status: dict[str, Any]
    recorder: llm_history.LLMHistoryRecorder

    # Callbacks threaded from _build_short_impl closure
    update_stage: Callable[..., None]
    check_stop: Callable[[], None]

    # Retry budgets
    max_regen: int
    max_structural_attempts: int
    max_product_attempts: int
    max_chatgpt_provider_retries: int

    # Misc per-build settings
    music_track: Any
    cover_words: int
    long_video_url: str
    require_render_confirmation: bool
    source_artifacts: dict | None

    # Stage carry-over values (written by stages, read by later stages)
    extras: dict[str, Any] = field(default_factory=dict)
