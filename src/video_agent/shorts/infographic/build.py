"""Orchestrate static infographic Shorts (plan → poster → QA → music → render)."""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import math
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from video_agent.assets.audio_ops import _mix_bgm_with_narration
from video_agent.shorts import manifest as manifest_mod
from video_agent.shorts import music_selector, paths
from video_agent.shorts.idea_store import read_short_ideas, write_studio_render_run
from video_agent.shorts.infographic.plan import build_poster_plan
from video_agent.shorts.infographic.poster import generate_poster
from video_agent.shorts.infographic.qa import qa_poster
from video_agent.shorts.infographic.render_props import build_infographic_render_props
from video_agent.shorts.infographic.seo import build_infographic_seo
from video_agent.shorts.infographic.voiceover import synthesize_infographic_voiceover
from video_agent.storage.atomic import atomic_write_json


class InfographicStopRequested(RuntimeError):
    """Operator stop observed at a stage boundary of an infographic build.

    Cooperative-cancellation signal (AC8): raised between pipeline stages so a
    stop can never be outrun by later expensive work (poster/LLM/ffmpeg/
    Remotion). The sequential loop converts it into a batch cancellation; it is
    intentionally NOT an ordinary item failure and must never trigger a retry.
    """


def _stop_requested_for(short_dir: Path) -> bool:
    """Same stop contract as the narrated builder: parent-job or short flag."""
    return (
        (_long_job_dir(short_dir) / ".stop_requested").exists()
        or (short_dir / ".stop_requested").exists()
    )


DEFAULT_STATIC_DURATION_SEC = 15.0
_ORIGINAL_PROCEDURAL_SOURCE = "procedural_original"
# Final Like/Subscribe cue length; the cue must OWN its seconds — it may never
# overlap narration (P1-D), so voice-driven durations reserve at least this tail.
_ENGAGEMENT_CUE_SEC = 4.0  # must equal CUE_TOTAL_SEC in endEngagementCueTiming.ts (pinned by test)



def _voice_options(channel_config: dict) -> tuple[bool, float, float, float]:
    """(enabled, padding_sec, min_duration_sec, max_duration_sec) for infographic voice.

    Disabled by default (backward compatible with existing music-only channels);
    a channel opts in via ``shorts.infographic.voice.enabled: true``.
    """
    cfg = ((channel_config.get("shorts") or {}).get("infographic") or {}).get("voice") or {}
    return (
        bool(cfg.get("enabled", False)),
        float(cfg.get("padding_sec", 2.5)),
        float(cfg.get("min_duration_sec", 8.0)),
        float(cfg.get("max_duration_sec", 45.0)),
    )


def _wav_duration_seconds(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def _mix_voice_with_music(
    narration_path: Path, bgm_path: Path, mixed_path: Path, channel_config: dict, duration_sec: float
) -> bool:
    """Real default mixer: pad narration to the full Short length, then blend with
    the music bed using the shared sidechain-duck + loudnorm + limiter primitive.

    BGM base level reuses ``shorts.infographic.music_volume_db`` (already tuned
    and proven audible in music-only mode) — ``shorts.music`` has no ``bgm_gain_db``
    of its own (it's the narrated-Shorts profile, where the mixer's generic -24dB
    default is fine), so falling back to that made the BGM barely audible here.
    Ducking uses ``shorts.infographic.voice_duck_db`` (default 4dB, gentler than
    narrated Shorts' 8dB): an infographic Short's music is the ambience for a still
    image, not a bed under constantly-changing scenes, so it should stay clearly
    audible under voice rather than near-silent for the whole speech portion.
    (Tried disabling loudnorm first, expecting it was erasing the gain boost —
    measured QUIETER across the board instead, since loudnorm's makeup gain is
    what keeps the whole mix audible; reverted, loudnorm stays on.)
    """
    music_cfg = (channel_config.get("shorts") or {}).get("music") or {}
    infographic_cfg = (channel_config.get("shorts") or {}).get("infographic") or {}
    # SINGLE-ATTENUATION CONTRACT: prepare_infographic_music_bed already applied
    # shorts.infographic.music_volume_db when it encoded the bed. Applying it
    # again here attenuated the music twice (-14dB + -14dB = a nearly silent
    # -27.8dB bed measured on a real render), so the mixer applies 0dB and lets
    # ducking be the only additional gain change.
    bgm_gain_db = 0.0
    # amix's duration=first (inside _mix_bgm_with_narration) matches the NARRATION
    # track's own length, so pad it to the full Short duration first — otherwise the
    # BGM's outro padding would be truncated to the raw speech length.
    padded_path = narration_path.parent / "short_narration_padded.wav"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(narration_path),
                "-af", f"apad=whole_dur={duration_sec:.3f}",
                str(padded_path),
            ],
            check=True, capture_output=True,
        )
        return _mix_bgm_with_narration(
            padded_path, bgm_path, mixed_path,
            voice_gain_db=float(music_cfg.get("voice_gain_db", -4.5)),
            bgm_gain_db=bgm_gain_db,
            # Gentler ducking than narrated Shorts (default 8dB): an infographic
            # Short's music IS the ambience for a still image, not a bed under
            # constantly-varied narrated scenes, so it should stay audible under
            # voice, not near-silent (measured: loudnorm's makeup gain is what
            # keeps the track audible at all — disabling it measured QUIETER
            # across the board, so it stays on; duck_db is the real lever).
            duck_db=float(infographic_cfg.get("voice_duck_db", 4.0)),
            target_lufs=float(music_cfg.get("target_lufs", -13.6)),
            target_tp=float(music_cfg.get("target_tp_dbtp", 0.0)),
            target_lra=float(music_cfg.get("target_lra", 4.8)),
            out_sample_rate=int(music_cfg.get("sample_rate", 44100)),
            out_bitrate=str(music_cfg.get("bitrate", "128k")),
        )
    except Exception:
        return False
    finally:
        padded_path.unlink(missing_ok=True)


def _static_options(channel_config: dict, source: dict) -> tuple[float, bool, str]:
    cfg = ((channel_config.get("shorts") or {}).get("infographic") or {})
    duration_sec = float(cfg.get("duration_sec", DEFAULT_STATIC_DURATION_SEC))
    if not 5.0 <= duration_sec <= 60.0:
        raise ValueError("shorts.infographic.duration_sec must be between 5 and 60 seconds")
    music_source = str(cfg.get("music_source") or _ORIGINAL_PROCEDURAL_SOURCE).strip().lower()
    if music_source == _ORIGINAL_PROCEDURAL_SOURCE:
        music_track = _ORIGINAL_PROCEDURAL_SOURCE
    elif music_source == "library":
        music_track = str(
            cfg.get("music_track")
            or music_selector.select_music_track(source.get("pillar") or source.get("topic") or "", channel_config)
        )
    else:
        raise ValueError("shorts.infographic.music_source must be procedural_original or library")
    return duration_sec, bool(cfg.get("ken_burns", False)), music_track


_PROBE_TIMEOUT_SEC = 30
_ENCODE_TIMEOUT_SEC = 180


def probe_audio_duration_seconds(path: Path) -> float:
    """Track length in seconds via ffprobe; 0.0 when the file is unreadable.

    Bounded by a timeout so a wedged ffprobe can never hang the whole
    pipeline; NaN/Inf probe output is treated as unreadable."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SEC,
        ).stdout.strip()
        value = float(out)
        return value if math.isfinite(value) else 0.0
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        ValueError,
        OSError,
        AttributeError,
    ):
        return 0.0


def deterministic_music_excerpt_offset(
    track_duration_sec: float,
    required_bed_sec: float,
    seed_key: str,
    *,
    min_offset_sec: float = 5.0,
    end_margin_sec: float = 1.0,
) -> float:
    """Reproducible pseudo-random start offset into a library track.

    Every Short hears a different part of its topic track instead of always the
    intro, while re-rendering the same Short/track reproduces the same excerpt
    (the seed is ``short_dir.name|track_key``). SHA-256, never process ``hash()``
    or unseeded ``random``. A track too short for bed + margins keeps the
    offset-zero loop fallback."""
    max_offset_sec = track_duration_sec - required_bed_sec - end_margin_sec
    # Inclusive range: a track where max == min still has exactly one valid
    # non-intro start (the minimum offset); only a genuinely too-short track
    # falls back to the offset-zero loop.
    if max_offset_sec < min_offset_sec:
        return 0.0
    digest = hashlib.sha256(seed_key.encode("utf-8")).hexdigest()
    fraction = int(digest[:16], 16) / float(0xFFFFFFFFFFFFFFFF)
    return min_offset_sec + fraction * (max_offset_sec - min_offset_sec)


def prepare_infographic_music_bed(
    short_dir: Path, music_track: str, channel_config: dict, duration_sec: float
) -> Path:
    """Create a self-contained music-only bed from the configured source."""
    cfg = ((channel_config.get("shorts") or {}).get("infographic") or {})
    music_source = str(cfg.get("music_source") or _ORIGINAL_PROCEDURAL_SOURCE).strip().lower()
    if music_source == _ORIGINAL_PROCEDURAL_SOURCE:
        from video_agent.shorts.original_bgm import create_original_bgm

        # A channel that switches library -> procedural must not leave a stale
        # library reproducibility artifact next to a procedural bed.
        (Path(short_dir) / "json" / paths.SHORT_MUSIC_SELECTION_FILE).unlink(missing_ok=True)
        return create_original_bgm(
            short_dir,
            duration_sec=duration_sec,
            seed_key=Path(short_dir).name,
            bitrate=str(cfg.get("music_bitrate", "192k")),
        )
    if music_source != "library":
        raise ValueError("shorts.infographic.music_source must be procedural_original or library")

    # Existing channels can explicitly keep their licensed library bed.
    from video_agent.shorts.audio_mixer import resolve_music_file

    music_file = resolve_music_file(music_track, channel_config)
    if music_file is None or not music_file.exists():
        raise RuntimeError(f"Infographic Short requires an available music track: {music_track}")
    duration_sec = float(duration_sec)
    if not math.isfinite(duration_sec) or duration_sec <= 0.0:
        raise RuntimeError(
            f"Infographic music bed requires a finite positive duration, got {duration_sec!r}."
        )
    audio_dir = Path(short_dir) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    out_path = audio_dir / "infographic_bgm.m4a"
    fade_out_sec = min(float(cfg.get("music_fade_out_sec", 0.45)), duration_sec)
    fade_out_start = max(0.0, duration_sec - fade_out_sec)

    # Deterministic random excerpt (2026-07 engagement spec): topic selection
    # stays authoritative; the excerpt logic only picks WHERE in the chosen
    # track the bed starts. An unreadable duration must fail loudly — silently
    # starting at 00:00 would hide a broken library asset.
    track_duration_sec = probe_audio_duration_seconds(music_file)
    if not math.isfinite(track_duration_sec) or track_duration_sec <= 0.0:
        raise RuntimeError(
            f"Could not read duration of library music track {music_file}; "
            "cannot pick a deterministic excerpt from a broken asset."
        )
    # Seed carries the PARENT JOB identity too: two jobs can hold shorts with
    # identical basenames (short-01_idea-01_...), and those must not share an
    # excerpt. Re-rendering the same short in the same job stays stable.
    seed_key = f"{_long_job_dir(short_dir).name}|{Path(short_dir).name}|{music_track}"
    min_offset_sec = float(cfg.get("music_excerpt_min_offset_sec", 5.0))
    end_margin_sec = float(cfg.get("music_excerpt_end_margin_sec", 1.0))
    for name, value in (("music_excerpt_min_offset_sec", min_offset_sec),
                        ("music_excerpt_end_margin_sec", end_margin_sec)):
        if not math.isfinite(value) or value < 0.0:
            raise RuntimeError(
                f"shorts.infographic.{name} must be finite and non-negative, got {value!r}."
            )
    raw_offset = deterministic_music_excerpt_offset(
        track_duration_sec,
        duration_sec,
        seed_key,
        min_offset_sec=min_offset_sec,
        end_margin_sec=end_margin_sec,
    )
    # Rounding for serialization must never push the offset outside the legal
    # bounds (max could otherwise be exceeded by up to 5ms).
    max_offset_sec = track_duration_sec - duration_sec - end_margin_sec
    offset_sec = round(raw_offset, 2)
    if raw_offset > 0.0 and max_offset_sec > 0.0:
        offset_sec = min(max(offset_sec, min(min_offset_sec, max_offset_sec)), max_offset_sec)
    # NOTE (single-attenuation contract): music_volume_db is applied HERE and
    # only here. The voice mixer must pass bgm_gain_db=0 for this bed — see
    # _mix_voice_with_music.
    out_bitrate = str(cfg.get("music_bitrate", "192k"))
    out_sample_rate = int(
        ((channel_config.get("shorts") or {}).get("music") or {}).get("sample_rate", 44100)
    )
    tmp_path = out_path.with_suffix(".tmp.m4a")
    # Encode to a TEMP file and only replace the final bed after validation —
    # any failure (timeout, ffmpeg error, corrupt output) removes the temp and
    # preserves whatever good bed already exists at out_path.
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-stream_loop", "-1",
                "-ss", f"{offset_sec:.2f}", "-i", str(music_file),
                "-t", f"{duration_sec:.2f}",
                "-af", (
                    f"volume={float(cfg.get('music_volume_db', -14.0))}dB,"
                    f"afade=t=in:st=0:d={float(cfg.get('music_fade_in_sec', 0.16)):.2f},"
                    f"afade=t=out:st={fade_out_start:.2f}:d={fade_out_sec:.2f}"
                ),
                "-c:a", "aac", "-b:a", out_bitrate, "-ar", str(out_sample_rate), str(tmp_path),
            ],
            check=True,
            capture_output=True,
            timeout=_ENCODE_TIMEOUT_SEC,
        )
        # Validate the encode BEFORE it becomes the bed: a truncated/corrupt
        # file must never reach the mixer or the render.
        encoded_duration = probe_audio_duration_seconds(tmp_path)
        if not math.isfinite(encoded_duration) or abs(encoded_duration - duration_sec) > 0.75:
            raise RuntimeError(
                f"Encoded music bed duration {encoded_duration}s does not match the "
                f"requested {duration_sec}s; refusing to use a corrupt bed."
            )
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    os.replace(tmp_path, out_path)
    track_entry = (
        ((channel_config.get("music_library") or {}).get("tracks") or {}).get(music_track) or {}
    )
    # Audit artifact: everything QA needs to reproduce this exact audio bed.
    atomic_write_json(Path(short_dir) / "json" / paths.SHORT_MUSIC_SELECTION_FILE, {
        "source": "library",
        "track_key": music_track,
        "track_title": str(track_entry.get("title") or music_track),
        "track_file": str(music_file),
        "track_duration_sec": track_duration_sec,
        "excerpt_offset_sec": offset_sec,
        "excerpt_duration_sec": duration_sec,
        "seed_key": seed_key,
        "selection_mode": "deterministic_random_excerpt",
    })
    return out_path


def _long_job_dir(short_dir: Path) -> Path:
    """The parent long-form job dir for a short at ``<job>/shorts/<short_id>``."""
    return Path(short_dir).parent.parent


def _public_short_ref(short_dir: Path, subdir: str, name: str) -> str:
    """staticFile ref under remotion/public: ``materialize_short_job_aliases`` publishes
    a short's files to ``remotion/public/jobs/<short_dir.name>/<subdir>/`` (keyed by the
    short's OWN dir name, flattened — NOT nested under the parent job id), so the render
    ref must use that same key. The render_fn is responsible for the materialize step."""
    return f"jobs/{Path(short_dir).name}/{subdir}/{name}"


def run_infographic_short(
    short_dir: Path,
    channel_config: dict,
    source: dict,
    *,
    image_fn,
    llm_fn: Callable[..., str],
    read_text_fn: Callable[[Path], str] | None = None,
    render_fn: Callable[..., Path],
    music_fn: Callable[..., Path] | None = None,
    voice_fn: Callable[[Path, dict, dict], Path] | None = None,
    mix_fn: Callable[[Path, Path, Path, dict, float], bool] | None = None,
    max_poster_attempts: int = 3,
    source_idea: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synchronous orchestrator. ``image_fn`` is async (the only awaited dep), so it is
    driven with a fresh ``asyncio.run`` per poster generation; ``llm_fn``/``music_fn``/
    ``render_fn`` are plain sync callables — some (the real ``chatgpt_fn``) use
    ``asyncio.run`` internally, so this function must NOT itself run inside an event
    loop (nested ``asyncio.run`` raises).

    ``voice_fn``/``mix_fn`` add narration (opt-in via ``shorts.infographic.voice.enabled``):
    when on, the Short's duration follows the synthesized speech length (+padding,
    clamped) instead of the fixed config duration."""
    short_dir = Path(short_dir)
    short_dir.mkdir(parents=True, exist_ok=True)

    voice_enabled, voice_padding, voice_min, voice_max = _voice_options(channel_config)
    skip = frozenset() if voice_enabled else frozenset({"voice", "mix"})

    # Live progress: the Renders tab reads short_status.json, so each stage
    # transition is persisted immediately — an in-flight short must be visible
    # in the UI, not appear only when the whole build finishes.
    stage_names = ("plan", "poster", "poster_qa", "voice", "music", "mix", "seo", "render_props", "render")

    def _progress(current: str) -> None:
        idx = stage_names.index(current)

        def stage_status(n: int, name: str) -> str:
            if name in skip:
                return "skipped"
            return "completed" if n < idx else "in_progress" if n == idx else "pending"

        atomic_write_json(short_dir / paths.SHORT_STATUS_FILE, {
            "short_type": "infographic",
            "status": "generating",
            "rendered": False,
            "stages": [
                {"name": name, "status": stage_status(n, name)}
                for n, name in enumerate(stage_names)
            ],
        })

    def _guard(next_stage: str) -> None:
        """Cooperative stop check at a stage boundary (AC8).

        An in-flight expensive call cannot be interrupted, but the moment it
        returns this guard sees the operator's stop flag and refuses to launch
        any later stage — no new ffmpeg/Remotion/browser work after Stop. The
        short is persisted as terminal ``cancelled`` (never rendered/failed).
        """
        if not _stop_requested_for(short_dir):
            return
        idx = stage_names.index(next_stage)
        atomic_write_json(short_dir / paths.SHORT_STATUS_FILE, {
            "short_type": "infographic",
            "status": "cancelled",
            "rendered": False,
            "stop_requested": True,
            "stages": [
                {
                    "name": name,
                    "status": (
                        "skipped" if name in skip
                        else "completed" if n < idx
                        else "cancelled"
                    ),
                }
                for n, name in enumerate(stage_names)
            ],
        })
        raise InfographicStopRequested(f"stop requested before stage {next_stage!r}")

    _guard("plan")
    _progress("plan")
    plan = build_poster_plan(channel_config, source, llm_fn)
    atomic_write_json(short_dir / "json" / paths.SHORT_POSTER_PLAN_FILE, plan)

    _guard("poster")
    _progress("poster")
    verdict: dict[str, Any]
    if read_text_fn is None:
        # QA disabled: generate the poster once and proceed (no text gate). The
        # AI-only garble risk is accepted; nothing blocks the render.
        asyncio.run(generate_poster(short_dir, plan, image_fn, channel_config))
        verdict = {"verdict": "skipped", "missing": []}
    else:
        verdict = {"verdict": "qa_unavailable", "missing": []}
        for _ in range(max_poster_attempts):
            asyncio.run(generate_poster(short_dir, plan, image_fn, channel_config))
            verdict = qa_poster(
                short_dir / "assets" / paths.SHORT_POSTER_IMAGE_NAME, plan, read_text_fn=read_text_fn
            )
            if verdict["verdict"] == "pass":
                break
    _guard("poster_qa")
    _progress("poster_qa")
    atomic_write_json(short_dir / "json" / paths.SHORT_POSTER_QA_FILE, verdict)

    status: dict[str, Any] = {
        "short_type": "infographic",
        "poster_format": plan.get("poster_format"),
        "rendered": False,
    }
    if verdict["verdict"] not in ("pass", "skipped"):
        status["status"] = "needs_manual_review"
        status["qa"] = verdict
        status["stages"] = [
            {"name": name, "status": "completed" if name in ("plan", "poster", "poster_qa") else "pending"}
            for name in stage_names
        ]
        atomic_write_json(short_dir / paths.SHORT_STATUS_FILE, status)
        return status

    duration, ken_burns, music_track = _static_options(channel_config, source)

    voice_duration_sec: float | None = None
    narration_path: Path | None = None
    engagement_cue_sec = _ENGAGEMENT_CUE_SEC
    show_engagement_cue = True
    if voice_enabled:
        _guard("voice")
        _progress("voice")
        vfn = voice_fn or synthesize_infographic_voiceover
        narration_path = Path(vfn(short_dir, plan, channel_config))
        voice_duration_sec = round(_wav_duration_seconds(narration_path), 2)
        # The tail after the voice must fit the engagement cue (P1-D): the cue
        # may never talk over the narration, so the padding is at least the cue
        # length. When the max-duration clamp bites, the leftover tail decides
        # the cue: shrink it, and below the useful minimum disable it entirely.
        duration = max(
            voice_min,
            min(voice_max, voice_duration_sec + max(voice_padding, _ENGAGEMENT_CUE_SEC)),
        )
        tail_sec = duration - voice_duration_sec
        # ALL-OR-NOTHING (review round 2): a shortened cue cannot complete its
        # press sequence, so either the FULL 3.0s fits after the narration or
        # the cue is disabled entirely (max-duration clamp cases).
        if tail_sec >= _ENGAGEMENT_CUE_SEC - 1e-9:
            engagement_cue_sec = _ENGAGEMENT_CUE_SEC
        else:
            show_engagement_cue = False
            engagement_cue_sec = 0.0

    _guard("music")
    _progress("music")
    music_fn = music_fn or prepare_infographic_music_bed
    audio_path = music_fn(short_dir, music_track, channel_config, duration)

    audio_mode = "music_only"
    if voice_enabled and narration_path is not None:
        _guard("mix")
        _progress("mix")
        mfn = mix_fn or _mix_voice_with_music
        mixed_path = short_dir / "audio" / "infographic_mix.m4a"
        if mfn(narration_path, Path(audio_path), mixed_path, channel_config, duration):
            audio_path = mixed_path
            audio_mode = "voice_plus_music"
        # else: mixer failed — fall back to the music-only bed (already built).

    _guard("seo")
    _progress("seo")
    # SEO/title artifact (writes json/short_seo.json via the shipped Short SEO path:
    # 4 scroll-stopper formulas, <=40 chars, aligned with the poster hook_line).
    # Pass the selected source idea (the full idea contract) so SEO preserves
    # format/title/pain/payoff/idea_id + fixed-count, not just the poster plan
    # (spec §Idea preservation). Defaults to ``source`` when the caller didn't
    # pass an explicit idea, so direct callers stay source-compatible.
    build_infographic_seo(
        _long_job_dir(short_dir), short_dir.name, plan, channel_config, llm_fn,
        source_idea=source_idea if source_idea is not None else source,
    )

    _guard("render_props")
    _progress("render_props")
    props = build_infographic_render_props(
        poster_ref=_public_short_ref(short_dir, "assets", paths.SHORT_POSTER_IMAGE_NAME),
        audio_ref=_public_short_ref(short_dir, "audio", Path(audio_path).name),
        duration_sec=duration,
        music_track="",
        channel_name=str((channel_config.get("channel") or {}).get("name") or ""),
        ken_burns=ken_burns,
        show_engagement_cue=show_engagement_cue,
        engagement_cue_sec=engagement_cue_sec,
    )
    atomic_write_json(short_dir / "json" / paths.SHORT_RENDER_PROPS_FILE, props)
    _guard("render")
    _progress("render")
    out = render_fn(short_dir, props)

    status["rendered"] = bool(Path(out).exists())
    status["stages"] = [
        {
            "name": name,
            "status": (
                "skipped" if name in skip
                else "completed" if (status["rendered"] or name != "render")
                else "failed"
            ),
        }
        for name in stage_names
    ]
    status["status"] = "rendered" if status["rendered"] else "failed"
    status["video_path"] = f"{short_dir.name}/outputs/{Path(out).name}"
    status["audio_mode"] = audio_mode
    status["music_track"] = music_track
    status["music_source"] = str(
        ((channel_config.get("shorts") or {}).get("infographic") or {}).get("music_source")
        or _ORIGINAL_PROCEDURAL_SOURCE
    )
    status["duration_sec"] = duration
    if voice_duration_sec is not None:
        status["voice_duration_sec"] = voice_duration_sec
    atomic_write_json(short_dir / paths.SHORT_STATUS_FILE, status)
    return status


def _slug(text: str, *, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return s[:max_len] or "short"


def render_selected_infographic_ideas(
    long_job_dir: Path,
    channel_config: dict,
    idea_ids: list[str],
    *,
    image_fn,
    llm_fn: Callable[..., str],
    render_fn: Callable[..., Path],
    music_fn: Callable[..., Path] | None = None,
    read_text_fn: Callable[[Path], str] | None = None,
    force: bool = False,
    progress=None,
) -> dict[str, Any]:
    """Build one infographic Short per selected idea (parent topic -> poster short).

    Mirrors ``render_selected_short_ideas`` but runs the infographic pipeline. Writes
    each short's status + a manifest entry tagged ``short_type="infographic"``.

    ``progress`` is an optional batch tracker (``BatchProgress`` contract):
    item_started then item_completed/item_failed fire sequentially per idea,
    batch_finished fires once at the end — or batch_cancelled on an explicit
    operator stop, with no event for the later pending items. An ordinary item
    failure is recorded and the loop CONTINUES with the next idea (spec §8.6).
    """
    long_job_dir = Path(long_job_dir)
    ideas_doc = read_short_ideas(long_job_dir)
    ideas_by_id = {str(i.get("idea_id")): i for i in ideas_doc.get("ideas") or []}
    selected = [ideas_by_id[i] for i in idea_ids if i in ideas_by_id]
    if not selected:
        raise ValueError("No valid idea IDs selected")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    results: list[dict[str, Any]] = []
    cancelled = False
    for n, idea in enumerate(selected, start=1):
        idea_id = str(idea.get("idea_id"))
        if (long_job_dir / ".stop_requested").exists():
            cancelled = True
            if progress is not None:
                progress.batch_cancelled("operator requested stop")
            break
        if progress is not None and not progress.item_started(idea_id):
            # The stop route cancelled the batch between our flag check and the
            # item start — halt without touching later pending items (AC8).
            cancelled = True
            break
        title = str(idea.get("title") or "")
        source = {
            "topic": idea.get("topic") or title,
            "title": title,
            # Real generated ideas carry NO pillar (bug-526): classify the idea
            # text once so topic music selection works through this path too —
            # hook and item labels give the classifier more signal than the
            # title alone.
            "pillar": idea.get("pillar") or music_selector.derive_pillar_from_text(
                " ".join(
                    [title, str(idea.get("hook") or "")]
                    + [
                        str(it.get("label") or "")
                        for it in (idea.get("content_items") or idea.get("items") or [])
                        if isinstance(it, dict)
                    ]
                )
            ),
            # The idea was conceived FOR a poster layout; seed the plan with it
            # (alias-mapped from legacy narrated formats) instead of letting the
            # plan LLM re-pick a random one.
            "poster_format": str(idea.get("format") or ""),
        }
        short_id = f"short-{n:02d}_{idea_id}_{ts}_{_slug(title or idea_id)}"
        short_dir = long_job_dir / "shorts" / short_id
        try:
            status = run_infographic_short(
                short_dir, channel_config, source,
                image_fn=image_fn, llm_fn=llm_fn, music_fn=music_fn, render_fn=render_fn,
                read_text_fn=read_text_fn,
                # The reduced ``source`` dict drives the poster; the FULL idea
                # carries the SEO contract (format/pain/payoff/idea_id/count).
                source_idea=idea,
            )
        except InfographicStopRequested:
            # Operator stop landed at a stage boundary of the CURRENT item:
            # the item is already persisted as cancelled by the guard; cancel
            # the batch (idempotent — the stop route usually did it first) and
            # stop immediately. No rendered/failed manifest entry, no events
            # for later pending items (AC8).
            cancelled = True
            if progress is not None:
                progress.batch_cancelled("operator requested stop")
            results.append({
                "short_id": short_id, "idea_id": idea_id, "short_type": "infographic",
                "status": "cancelled", "rendered": False, "video_path": None,
            })
            break
        except Exception as exc:  # noqa: BLE001 — one bad idea must not sink the batch
            # Ordinary item failure: record it and CONTINUE with the next
            # selected idea (spec §8.6). The old behavior aborted the loop and
            # silently discarded the remaining selection.
            status = {
                "short_type": "infographic",
                "status": "failed",
                "rendered": False,
                "error": str(exc)[:500],
            }
            if progress is not None:
                progress.item_failed(idea_id, exc)
        else:
            if progress is not None:
                if status.get("rendered"):
                    progress.item_completed(idea_id, short_id)
                else:
                    progress.item_failed(
                        idea_id, status.get("error") or f"status={status.get('status')}"
                    )
        status.update({"idea_id": idea_id, "short_id": short_id, "short_type": "infographic"})
        manifest_mod.write_short_status(long_job_dir, short_id, status)
        results.append({
            "short_id": short_id, "idea_id": idea_id, "short_type": "infographic",
            "status": status.get("status"), "rendered": status.get("rendered", False),
            "video_path": status.get("video_path"),
        })

    if progress is not None and not cancelled:
        progress.batch_finished()

    try:
        doc = manifest_mod.read_manifest(long_job_dir) or {}
    except FileNotFoundError:
        doc = {}
    doc["shorts"] = list(doc.get("shorts") or []) + results
    # Operator cancellation is a terminal NON-FAILURE state and wins the
    # top-level badge outright (AC8): a prior rendered entry must NOT relabel a
    # stopped run as "completed", and the cancelled item must NOT read "failed".
    if cancelled:
        doc["status"] = "cancelled"
    elif any(entry.get("rendered") for entry in doc["shorts"]):
        doc["status"] = "completed"
    elif results:
        doc["status"] = "failed"
    manifest_mod.write_manifest(long_job_dir, doc)

    # The Studio job badge reads studio_render_run.json BEFORE the manifest, so
    # this run must overwrite any stale doc left by an earlier narrated attempt.
    rendered_count = sum(1 for r in results if r.get("rendered"))
    cancelled_count = sum(1 for r in results if r.get("status") == "cancelled")
    # Cancelled items are neither rendered nor a failure — exclude them from the
    # failure tally so a stopped run never shows a red "N failed".
    failed_count = sum(
        1 for r in results if not r.get("rendered") and r.get("status") != "cancelled"
    )
    if cancelled:
        run_status = "cancelled"
    elif rendered_count == len(results) and results:
        run_status = "completed"
    elif rendered_count:
        run_status = "completed_with_warnings"
    else:
        run_status = "failed"
    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_studio_render_run(long_job_dir, {
        "schema_version": "studio_render_run.v1",
        "source_long_job_id": long_job_dir.name,
        "mode": "synthesis_ideas",
        "generation_id": ideas_doc.get("generation_id"),
        "status": run_status,
        "started_at": now,
        "completed_at": now,
        "selected_idea_count": len(selected),
        "attempted_render_count": len(results),
        "rendered_count": rendered_count,
        "needs_review_count": sum(1 for r in results if r.get("status") == "needs_manual_review"),
        "failed_count": failed_count,
        "cancelled_count": cancelled_count,
        "skipped_count": 0,
        "blocked_count": 0,
        "warnings": [],
        "errors": [],
    })
    return {"shorts": results}
