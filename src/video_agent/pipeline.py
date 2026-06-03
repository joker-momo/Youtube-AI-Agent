from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import json

from video_agent.contracts import (
    ARTIFACT_REPORT,
    ARTIFACT_RENDER_PROPS,
    ARTIFACT_VISUAL_CONTACT_SHEET,
    ARTIFACT_VISUAL_REVIEW,
    ARTIFACT_VIDEO,
    ARTIFACT_SCRIPT,
    ARTIFACT_SCENES,
    ARTIFACT_SEO,
    ARTIFACT_THUMBNAIL,
    EVENT_LOG,
    repo_root,
)
from video_agent.operator import assert_operator_qa_passed, write_operator_review
from video_agent.providers.mock import MockProvider
from video_agent.stages.assets import prepare_assets
from video_agent.stages.render import render_with_remotion
from video_agent.stages.scene import run_scene_stage
from video_agent.stages.script import run_script_stage
from video_agent.stages.thumbnail import create_thumbnail_and_seo
from video_agent.stages.visual_contact_sheet import create_visual_contact_sheet
from video_agent.utils.json_io import read_json, read_yaml, write_json
from video_agent.utils.logging import EventLogger
from video_agent.utils.paths import create_job_dir
from video_agent.utils.validation import validate_json
from video_agent.runtime.providers import AUDIO_SUBPROCESS_ENV, SubprocessAudioTaskProvider
from video_agent.storage.atomic import atomic_write_text

_AUDIO_SUBPROCESS_ENV = AUDIO_SUBPROCESS_ENV


def _resolve_json_file(job_dir: Path, filename: str) -> Path:
    """Resolve a JSON file with fallback: root → json/ subdirectory."""
    root_path = job_dir / filename
    if root_path.exists():
        return root_path
    json_path = job_dir / "json" / filename
    if json_path.exists():
        return json_path
    return root_path  # default to root for error messages


@dataclass
class PipelineOptions:
    channel_path: Path
    idea_path: Path
    jobs_dir: Path = Path("jobs")
    render: bool = True
    tts_override: dict | None = None


@dataclass
class PipelineResult:
    job_id: str
    job_dir: Path
    video_path: Path | None
    thumbnail_path: Path
    seo_path: Path
    report_path: Path


@dataclass
class OperatorRenderOptions:
    channel_path: Path
    job_dir: Path
    render: bool = True
    require_operator_qa: bool = True
    tts_override: dict | None = None
    stop_request_path: Path | None = None
    # Send Telegram notify after render completes. Full pipeline sets this False
    # to avoid duplicating the final job_done notify; single-stage re-renders set True.
    notify_telegram: bool = False


def _resolve_brand_logo_source(channel_config: dict) -> Path | None:
    root = repo_root()
    branding = channel_config.get("branding") or {}
    configured = branding.get("logo_path")
    if isinstance(configured, str) and configured.strip():
        p = Path(configured.strip())
        if not p.is_absolute():
            p = root / p
        if p.exists() and p.is_file():
            return p
    for logo_dir in (root / "asset_library" / "source", root / "asset_library" / "logo"):
        if not logo_dir.exists() or not logo_dir.is_dir():
            continue
        exact = logo_dir / "Logo.png"
        if exact.exists() and exact.is_file():
            return exact
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            matches = sorted(logo_dir.glob(ext))
            if matches:
                return matches[0]
    return None


def _resolve_brand_video_source(channel_config: dict, kind: str) -> Path | None:
    root = repo_root()
    branding = channel_config.get("branding") or {}
    configured = branding.get(f"{kind}_video_path")
    if isinstance(configured, str) and configured.strip():
        p = Path(configured.strip())
        if not p.is_absolute():
            p = root / p
        if p.exists() and p.is_file():
            return p
    source_dir = root / "asset_library" / "source"
    if not source_dir.exists() or not source_dir.is_dir():
        return None
    patterns = [f"*{kind}*.mp4", f"*{kind}*.mov", f"*{kind}*.mkv", f"*{kind}*.webm"]
    if kind == "outro":
        patterns.extend(["*outtro*.mp4", "*outtro*.mov", "*outtro*.mkv", "*outtro*.webm"])
    for pat in patterns:
        matches = sorted(source_dir.glob(pat))
        if matches:
            return matches[0]
    return None


def _probe_duration_sec(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True)
    payload = json.loads(out)
    duration = float((payload.get("format") or {}).get("duration") or 0.0)
    return max(0.0, duration)


def _prepare_branding(channel_config: dict) -> dict:
    root = repo_root()
    channel_id = (channel_config.get("channel") or {}).get("id", "default")
    branding_cfg = channel_config.get("branding") or {}
    # Intro/outro default OFF for maximum opening retention. YouTube viewers
    # decide whether to keep watching within the first 5-15 seconds, so the
    # video must start on the narration hook rather than a logo card. The
    # legacy logo intro/outro is only re-enabled when ``branding.enable_intro_outro``
    # is explicitly true in channel config.
    enable_intro_outro = bool(branding_cfg.get("enable_intro_outro", False))
    if enable_intro_outro:
        intro_sec = max(0.0, float(branding_cfg.get("intro_sec", 2.0)))
        outro_sec = max(0.0, float(branding_cfg.get("outro_sec", 2.0)))
    else:
        intro_sec = 0.0
        outro_sec = 0.0
    logo_source = _resolve_brand_logo_source(channel_config)
    intro_video_source = (
        _resolve_brand_video_source(channel_config, "intro") if enable_intro_outro else None
    )
    outro_video_source = (
        _resolve_brand_video_source(channel_config, "outro") if enable_intro_outro else None
    )
    logo_public = None
    intro_video_public = None
    outro_video_public = None
    if intro_video_source:
        intro_sec = _probe_duration_sec(intro_video_source)
    if outro_video_source:
        outro_sec = _probe_duration_sec(outro_video_source)
    if logo_source or intro_video_source or outro_video_source:
        dest_dir = root / "remotion" / "public" / "branding" / str(channel_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        if logo_source:
            dest = dest_dir / logo_source.name
            shutil.copy2(logo_source, dest)
            logo_public = f"branding/{channel_id}/{logo_source.name}"
        if intro_video_source:
            dest = dest_dir / intro_video_source.name
            shutil.copy2(intro_video_source, dest)
            intro_video_public = f"branding/{channel_id}/{intro_video_source.name}"
        if outro_video_source:
            dest = dest_dir / outro_video_source.name
            shutil.copy2(outro_video_source, dest)
            outro_video_public = f"branding/{channel_id}/{outro_video_source.name}"
    return {
        "logo_path": logo_public,
        "intro_video_path": intro_video_public,
        "outro_video_path": outro_video_public,
        "intro_sec": intro_sec,
        "outro_sec": outro_sec,
        "watermark_enabled": bool(logo_public),
        # Channel-name label in the top-left corner of every scene. Defaults
        # to OFF so the opening frame is clean. Toggle via
        # ``branding.show_channel_name_overlay: true`` when a one-off cut
        # needs the brand label visible.
        "show_channel_name_overlay": bool(branding_cfg.get("show_channel_name_overlay", False)),
    }


def _load_style(channel_config: dict) -> dict:
    return read_json(repo_root() / channel_config["style_dna"]["path"])


def _scene_visual_issues(scene: dict) -> list[dict]:
    issues = []
    source = scene.get("source")
    provider = scene.get("provider")
    selection = scene.get("selection") or {}
    score = selection.get("score")
    asset_key = f"{provider}:{scene.get('provider_asset_id')}" if provider and scene.get("provider_asset_id") else None

    if source == "generated_placeholder":
        issues.append(
            {
                "type": "PLACEHOLDER_USED",
                "severity": "warning",
                "message": "Scene fell back to a generated placeholder image.",
            }
        )
        for error in scene.get("stock_errors") or []:
            issue_type = "STOCK_PROVIDER_ERROR"
            if "API_KEY is required" in error.get("message", ""):
                issue_type = "MISSING_STOCK_API_KEY"
            issues.append(
                {
                    "type": issue_type,
                    "severity": "warning",
                    "message": f"{error.get('provider')}: {error.get('message')}",
                }
            )
    if source == "asset_library" and not provider:
        issues.append(
            {
                "type": "MISSING_PROVIDER",
                "severity": "warning",
                "message": "Asset library scene is missing provider metadata.",
            }
        )
    if score is not None and score < 40:
        issues.append(
            {
                "type": "LOW_SELECTION_SCORE",
                "severity": "warning",
                "message": f"Selected asset score is low: {score}.",
            }
        )
    if source == "asset_library" and not selection:
        issues.append(
            {
                "type": "MISSING_SELECTION_METADATA",
                "severity": "warning",
                "message": "Stock asset has no selection metadata.",
            }
        )
    if asset_key:
        scene["asset_key"] = asset_key
    return issues


def _add_visual_qa(review: dict) -> dict:
    seen_assets = {}
    issue_count = 0
    for scene in review["scenes"]:
        issues = _scene_visual_issues(scene)
        asset_key = scene.pop("asset_key", None)
        if asset_key:
            if asset_key in seen_assets:
                issues.append(
                    {
                        "type": "DUPLICATE_ASSET_IN_JOB",
                        "severity": "warning",
                        "message": f"Asset already used in {seen_assets[asset_key]}.",
                    }
                )
            else:
                seen_assets[asset_key] = scene["scene_id"]
        scene["qa"] = {"status": "WARN" if issues else "PASS", "issues": issues}
        issue_count += len(issues)
    review["qa"] = {"status": "WARN" if issue_count else "PASS", "issue_count": issue_count}
    return review


def _write_visual_review(job_dir: Path, job_id: str, assets: dict, scene_doc: dict) -> dict:
    asset_scenes = assets["scenes"]
    doc_scenes = scene_doc["scenes"]
    if len(asset_scenes) != len(doc_scenes):
        raise RuntimeError(
            f"visual_review scene count mismatch: assets={len(asset_scenes)}, scenes={len(doc_scenes)}"
        )
    scenes = []
    for scene_asset, scene in zip(asset_scenes, doc_scenes):
        scenes.append(
            {
                "scene_id": scene_asset["scene_id"],
                "on_screen_text": scene.get("on_screen_text"),
                "source": scene_asset.get("source"),
                "provider": scene_asset.get("provider"),
                "provider_asset_id": scene_asset.get("provider_asset_id"),
                "source_url": scene_asset.get("source_url"),
                "query": (scene_asset.get("asset_selection") or {}).get("query"),
                "selection": scene_asset.get("asset_selection"),
                "stock_errors": scene_asset.get("stock_errors") or [],
                "background": scene_asset.get("background"),
            }
        )
    summary = {
        "total_scenes": len(scenes),
        "by_source": {},
        "by_provider": {},
        "selection_scores": None,
        "searched_providers": {},
    }
    selection_scores = []
    for scene in scenes:
        summary["by_source"][scene["source"]] = summary["by_source"].get(scene["source"], 0) + 1
        if scene["provider"]:
            summary["by_provider"][scene["provider"]] = summary["by_provider"].get(scene["provider"], 0) + 1
        selection = scene.get("selection") or {}
        if isinstance(selection.get("score"), (int, float)):
            selection_scores.append(selection["score"])
        for provider in selection.get("searched_providers") or []:
            summary["searched_providers"][provider] = summary["searched_providers"].get(provider, 0) + 1
    if selection_scores:
        summary["selection_scores"] = {
            "min": min(selection_scores),
            "avg": round(sum(selection_scores) / len(selection_scores), 1),
            "max": max(selection_scores),
        }
    review = _add_visual_qa({"job_id": job_id, "summary": summary, "scenes": scenes})
    review["contact_sheet"] = ARTIFACT_VISUAL_CONTACT_SHEET
    write_json(job_dir / ARTIFACT_VISUAL_REVIEW, review)
    return review


def _write_report(
    job_dir: Path,
    job_id: str,
    channel_config: dict,
    idea: dict,
    render_enabled: bool,
    visual_review: dict,
) -> Path:
    visual_lines = [
        "",
        "## Visual Review",
        f"- Visual QA: {visual_review['qa']['status']}",
        f"- Visual contact sheet: {visual_review['contact_sheet']}",
    ]
    for scene in visual_review["scenes"]:
        provider = scene.get("provider") or "-"
        asset_id = scene.get("provider_asset_id") or "-"
        source = scene.get("source") or "-"
        issue_count = len(scene.get("qa", {}).get("issues", []))
        suffix = f" ({issue_count} issue)" if issue_count == 1 else f" ({issue_count} issues)" if issue_count else ""
        visual_lines.append(f"- {scene['scene_id']}: {source} {provider}/{asset_id}{suffix}")

    seo_path = _resolve_json_file(job_dir, "seo.json")
    pinned_comment_lines = []
    if seo_path.exists():
        try:
            seo_data = read_json(seo_path)
            comments = seo_data.get("suggested_pinned_comments")
            if isinstance(comments, str):
                pinned_comment_lines.extend([
                    "",
                    "## Suggested Pinned Comment",
                    comments
                ])
            elif isinstance(comments, dict):
                eb = comments.get("engagement_boosting") or comments.get("engage") or ""
                sg = comments.get("subscriber_growth") or comments.get("subscriber") or ""
                merged = f"{eb}\n\n{sg}".strip()
                pinned_comment_lines.extend([
                    "",
                    "## Suggested Pinned Comment",
                    merged or "n/a"
                ])
        except Exception:
            pass

    report_path = job_dir / ARTIFACT_REPORT
    atomic_write_text(
        report_path,
        "\n".join(
            [
                f"# Job Report: {job_id}",
                "",
                f"- Channel: {channel_config['channel']['name']}",
                f"- Topic: {idea['topic']}",
                f"- Render enabled: {render_enabled}",
                "- Outputs:",
                "  - script.json",
                "  - scenes.json",
                "  - assets_manifest.json",
                "  - render_props.json",
                "  - visual_review.json",
                "  - seo.json",
                "  - thumbnail.jpg",
                "  - video.mp4" if render_enabled else "  - video.mp4 skipped",
                *visual_lines,
                *pinned_comment_lines,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return report_path


def run_pipeline(options: PipelineOptions) -> PipelineResult:
    root = repo_root()
    channel_config = read_yaml(options.channel_path)
    if options.tts_override:
        channel_config["tts"] = (channel_config.get("tts") or {}) | options.tts_override
    idea = read_json(options.idea_path)
    validate_json(channel_config, root / "schemas/channel-config.schema.json")
    validate_json(idea, root / "schemas/manual-idea.schema.json")

    job_dir = create_job_dir(options.jobs_dir, channel_config["channel"]["id"], idea["topic"])
    (job_dir / "json").mkdir(parents=True, exist_ok=True)
    (job_dir / "outputs").mkdir(parents=True, exist_ok=True)
    job_id = job_dir.name
    logger = EventLogger(job_dir / EVENT_LOG)
    provider = MockProvider()
    style = _load_style(channel_config)

    logger.log("JOB_STARTED", {"job_id": job_id, "channel_id": channel_config["channel"]["id"], "cost_usd": 0})
    script = run_script_stage(provider, channel_config, idea, job_id, job_dir)
    validate_json(script, root / "schemas/script.schema.json")
    logger.log("SCRIPTED", {"job_id": job_id, "cost_usd": 0})

    scene_doc = run_scene_stage(provider, channel_config, idea, script, job_id, job_dir)
    validate_json(scene_doc, root / "schemas/scenes.schema.json")
    logger.log("SCENED", {"job_id": job_id, "cost_usd": 0})

    assets = prepare_assets(
        job_dir,
        style,
        scene_doc,
        visual_config=channel_config.get("visuals"),
        tts_config=(channel_config.get("tts") or {})
        | {"music": channel_config.get("music") or {}},
        channel_id=channel_config["channel"]["id"],
    )
    seo = create_thumbnail_and_seo(provider, channel_config, style, idea, job_dir)
    validate_json(seo, root / "schemas/seo.schema.json")
    logger.log("ASSETS_READY", {"job_id": job_id, "cost_usd": 0})

    branding = _prepare_branding(channel_config)
    render_props = {
        "channel": channel_config["channel"],
        "style": style,
        "render": channel_config["render"]
        | {"duration_sec": scene_doc["total_duration_sec"] + branding["intro_sec"] + branding["outro_sec"]},
        "scenes": scene_doc["scenes"],
        "audio": assets["audio"],
        "seo": seo,
        "branding": branding,
    }
    write_json(job_dir / ARTIFACT_RENDER_PROPS, render_props)
    validate_json(render_props, root / "schemas/render-props.schema.json")
    visual_review = _write_visual_review(job_dir, job_id, assets, scene_doc)
    create_visual_contact_sheet(job_dir, visual_review)

    video_path = None
    if options.render:
        video_path = job_dir / ARTIFACT_VIDEO
        render_with_remotion(
            job_dir / ARTIFACT_RENDER_PROPS,
            video_path,
            job_dir / "thumbnail.jpg",
            stop_request_path=options.stop_request_path,
            notify_telegram=options.notify_telegram,
        )
        logger.log("RENDERED", {"job_id": job_id, "video_path": str(video_path), "cost_usd": 0})

    report_path = _write_report(job_dir, job_id, channel_config, idea, options.render, visual_review)
    logger.log("JOB_COMPLETED", {"job_id": job_id, "cost_usd": 0})
    return PipelineResult(
        job_id=job_id,
        job_dir=job_dir,
        video_path=video_path if video_path and video_path.exists() else None,
        thumbnail_path=job_dir / ARTIFACT_THUMBNAIL,
        seo_path=job_dir / ARTIFACT_SEO,
        report_path=report_path,
    )


def _sync_scene_durations_from_audio(job_dir: Path, scene_doc: dict) -> None:
    """Recalculate each scene's duration_sec from the actual synthesized audio.

    When TTS is cached (audio already exists), prepare_assets skips dynamic_sync
    so scene durations stay as the LLM-estimated values from scenes.json (e.g. 10-11s
    uniform blocks). This function corrects them before render_props is written.

    Strategy A — whisper_timestamps.json exists (most accurate):
      The whisper stage records the audio_offset_sec for every scene.
      Duration of scene[i] = offset[i+1] - offset[i].
      Duration of the last scene = total_audio_duration - offset[last].

    Strategy B — no whisper but narration audio exists:
      Read total audio duration from the narration WAV.
      Distribute proportionally based on original scene durations.

    Both strategies add a 0.35 s tail pause after the last word so the
    last frame doesn't cut off mid-sentence.
    """
    import logging
    log = logging.getLogger(__name__)

    scenes = scene_doc.get("scenes") or []
    if not scenes:
        return

    TAIL_PAD_SEC = 0.35  # breathing room after last spoken word

    # ── Strategy A: derive from whisper offsets ──────────────────────────────────
    whisper_path = _resolve_json_file(job_dir, "whisper_timestamps.json")
    if whisper_path.exists():
        try:
            from video_agent.utils.json_io import read_json as _rj
            w_data = _rj(whisper_path)
            w_scenes = w_data.get("scenes") or []
            offsets = {s["scene_id"]: float(s["audio_offset_sec"]) for s in w_scenes}

            # Measure total audio duration from the narration file
            total_audio = None
            for fname in ("narration_mixed.m4a", "narration.wav"):
                p = job_dir / "assets" / fname
                if p.exists() and p.stat().st_size > 0:
                    try:
                        import soundfile as sf
                        info = sf.info(str(p))
                        total_audio = float(info.duration)
                        break
                    except Exception:
                        pass

            if total_audio is None:
                total_audio = float(scene_doc.get("total_duration_sec") or 60)

            updated = False
            for idx, scene in enumerate(scenes):
                sid = scene["id"]
                if sid not in offsets:
                    continue
                my_offset = offsets[sid]
                if idx + 1 < len(scenes):
                    next_sid = scenes[idx + 1]["id"]
                    next_offset = offsets.get(next_sid, total_audio)
                    dur = next_offset - my_offset
                else:
                    dur = total_audio - my_offset + TAIL_PAD_SEC
                scene["duration_sec"] = round(max(3.0, dur), 3)
                updated = True

            if updated:
                scene_doc["total_duration_sec"] = int(
                    round(sum(float(s["duration_sec"]) for s in scenes))
                )
                log.info(
                    "Duration sync (whisper): %s",
                    [(s["id"], s["duration_sec"]) for s in scenes],
                )
                return
        except Exception as exc:
            log.warning("Duration sync strategy A failed: %s", exc)

    # ── Strategy B: proportional split from total audio duration ─────────────────
    narration_path = None
    for fname in ("narration_mixed.m4a", "narration.wav"):
        p = job_dir / "assets" / fname
        if p.exists() and p.stat().st_size > 0:
            narration_path = p
            break

    if narration_path is None:
        return  # nothing to measure — let existing values stand

    try:
        import soundfile as sf
        info = sf.info(str(narration_path))
        total_audio = float(info.duration)
    except Exception as exc:
        log.warning("Duration sync strategy B: cannot read audio (%s)", exc)
        return

    original_total = sum(float(s.get("duration_sec") or 1) for s in scenes)
    if original_total <= 0:
        return

    for scene in scenes:
        ratio = float(scene.get("duration_sec") or 1) / original_total
        scene["duration_sec"] = round(max(3.0, ratio * total_audio), 3)

    scene_doc["total_duration_sec"] = int(round(total_audio))
    log.info(
        "Duration sync (proportional): total=%.1fs, scenes=%s",
        total_audio,
        [(s["id"], s["duration_sec"]) for s in scenes],
    )


def _run_prepare_assets_audio_subprocess(job_dir: Path, channel_path: Path) -> None:
    SubprocessAudioTaskProvider().prepare_assets(job_dir, channel_path)


def _should_prepare_audio_in_subprocess(job_dir: Path, channel_config: dict) -> bool:
    if os.environ.get(_AUDIO_SUBPROCESS_ENV) == "1":
        return False
    tts_cfg = channel_config.get("tts") or {}
    if tts_cfg.get("provider", "mock-local") == "mock-local":
        return False
    narration = job_dir / "assets" / "narration.wav"
    return not (narration.exists() and narration.stat().st_size > 0)


def render_operator_job(options: OperatorRenderOptions) -> PipelineResult:
    root = repo_root()
    channel_config = read_yaml(options.channel_path)
    if options.tts_override:
        channel_config["tts"] = (channel_config.get("tts") or {}) | options.tts_override
    validate_json(channel_config, root / "schemas/channel-config.schema.json")

    job_dir = options.job_dir
    job_dir.mkdir(parents=True, exist_ok=True)
    job_id = job_dir.name
    logger = EventLogger(job_dir / EVENT_LOG)
    style = _load_style(channel_config)

    script = read_json(_resolve_json_file(job_dir, "script.json"))
    scene_doc = read_json(_resolve_json_file(job_dir, "scenes.json"))
    seo = read_json(_resolve_json_file(job_dir, "seo.json"))
    validate_json(script, root / "schemas/script.schema.json")
    validate_json(scene_doc, root / "schemas/scenes.schema.json")
    validate_json(seo, root / "schemas/seo.schema.json")
    if options.require_operator_qa:
        assert_operator_qa_passed(job_dir)

    logger.log("OPERATOR_RENDER_STARTED", {"job_id": job_id, "channel_id": channel_config["channel"]["id"]})

    # Merge Whisper word timestamps into scene_doc if available
    whisper_path = _resolve_json_file(job_dir, "whisper_timestamps.json")
    if whisper_path.exists():
        whisper_data = read_json(whisper_path)
        whisper_by_id = {s["scene_id"]: s for s in whisper_data.get("scenes") or []}
        for scene in scene_doc["scenes"]:
            ws = whisper_by_id.get(scene["id"])
            if ws:
                scene["audio_offset_sec"] = ws["audio_offset_sec"]
                scene["word_segments"] = ws["word_segments"]

    # ── Duration sync: recalculate scene duration_sec from actual audio ──────────
    # When TTS was already synthesized (tts_cached), prepare_assets skips
    # dynamic_sync so scene durations stay at the LLM-estimated values (10-11s).
    # We fix this here, before render_props is assembled, by deriving the true
    # per-scene durations from:
    #   a) whisper offset table (most accurate), or
    #   b) actual narration audio file duration split proportionally.
    _sync_scene_durations_from_audio(job_dir, scene_doc)

    if _should_prepare_audio_in_subprocess(job_dir, channel_config):
        _run_prepare_assets_audio_subprocess(job_dir, options.channel_path)
        scene_doc = read_json(_resolve_json_file(job_dir, "scenes.json"))

    # Force portrait stock-asset orientation when this job is a Short, so the
    # render-path call to prepare_assets stays in sync with the TTS-path
    # override in shorts/audio.py. Without this, Shorts re-fetch landscape
    # Pexels clips here and overwrite the portrait ones from the TTS stage.
    visual_config = dict(channel_config.get("visuals") or {})
    short_render_props = job_dir / "short_render_props.json"
    is_short_job = False
    if short_render_props.exists():
        is_short_job = True
    else:
        try:
            render_resolution = str((channel_config.get("render") or {}).get("resolution") or "")
            w_str, h_str = render_resolution.lower().split("x", 1)
            if int(h_str) > int(w_str):
                is_short_job = True
        except (ValueError, AttributeError):
            pass
    if is_short_job and (channel_config.get("shorts") or {}).get(
        "source", {}
    ).get("prefer_vertical_assets", True):
        visual_config["orientation"] = "portrait"

    assets = prepare_assets(
        job_dir,
        style,
        scene_doc,
        visual_config=visual_config,
        tts_config=(channel_config.get("tts") or {})
        | {"music": channel_config.get("music") or {}},
        channel_id=channel_config["channel"]["id"],
    )
    branding = _prepare_branding(channel_config)

    render_config = channel_config["render"].copy()
    if job_dir.parent.name == "shorts":
        shorts_render = (channel_config.get("shorts") or {}).get("render") or {}
        for k, v in shorts_render.items():
            if v is not None:
                render_config[k] = v

    render_props = {
        "channel": channel_config["channel"],
        "style": style,
        "render": render_config
        | {"duration_sec": scene_doc["total_duration_sec"] + branding["intro_sec"] + branding["outro_sec"]},
        "scenes": scene_doc["scenes"],
        "audio": assets["audio"],
        "seo": seo,
        "branding": branding,
    }
    write_json(job_dir / ARTIFACT_RENDER_PROPS, render_props)
    validate_json(render_props, root / "schemas/render-props.schema.json")
    visual_review = _write_visual_review(job_dir, job_id, assets, scene_doc)
    create_visual_contact_sheet(job_dir, visual_review)

    video_path = None
    if options.render:
        video_path = job_dir / ARTIFACT_VIDEO
        render_with_remotion(
            job_dir / ARTIFACT_RENDER_PROPS,
            video_path,
            job_dir / "thumbnail.jpg",
            stop_request_path=options.stop_request_path,
            notify_telegram=options.notify_telegram,
        )
        logger.log("RENDERED", {"job_id": job_id, "video_path": str(video_path), "cost_usd": 0})

    idea = {"topic": seo.get("title") or job_id}
    report_path = _write_report(job_dir, job_id, channel_config, idea, options.render, visual_review)
    write_operator_review(job_dir)
    logger.log("OPERATOR_RENDER_COMPLETED", {"job_id": job_id, "cost_usd": 0})
    return PipelineResult(
        job_id=job_id,
        job_dir=job_dir,
        video_path=video_path if video_path and video_path.exists() else None,
        thumbnail_path=job_dir / "thumbnail.jpg",
        seo_path=job_dir / "seo.json",
        report_path=report_path,
    )
