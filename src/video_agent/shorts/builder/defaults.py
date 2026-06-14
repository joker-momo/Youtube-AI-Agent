"""Default real side-effect implementations (wired lazily) for short_builder."""

from __future__ import annotations

from pathlib import Path


def _default_llm_fn(kind: str, prompt: str) -> str:  # pragma: no cover - needs browser
    raise NotImplementedError("llm_fn must be injected (browser ChatGPT sender).")


def _default_background_fn(
    short_dir: Path, short_scenes: dict, channel_config: dict, on_scene_resolved=None
) -> None:
    from video_agent.shorts.audio import synthesize_short_backgrounds

    synthesize_short_backgrounds(
        short_dir, short_scenes, channel_config, on_scene_resolved=on_scene_resolved
    )


def _default_tts_fn(short_dir: Path, short_scenes: dict, channel_config: dict) -> Path:
    from video_agent.shorts.audio import synthesize_short_narration

    return synthesize_short_narration(short_dir, short_scenes, channel_config)


def _default_mix_fn(
    short_dir: Path,
    narration_wav: Path,
    music_track: str,
    channel_config: dict,
    duration_sec: float,
) -> Path:
    from video_agent.shorts.audio_mixer import mix_short_audio

    return mix_short_audio(short_dir, narration_wav, music_track, channel_config, duration_sec)


def _default_render_fn(
    short_dir: Path, channel_config: dict, stop_request_path: Path | None = None
) -> Path:
    from video_agent.shorts.renderer import render_short_video

    return render_short_video(short_dir, channel_config, stop_request_path=stop_request_path)


def _default_cover_fn(short_dir: Path, channel_config: dict) -> Path:
    from video_agent.shorts.renderer import render_short_cover

    return render_short_cover(short_dir, channel_config)
