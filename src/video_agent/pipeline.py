from __future__ import annotations

import json
import logging
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from video_agent.assets.materialize import materialize_media
from video_agent.branding import (
    disclaimer_duration_sec,
    without_long_form_branding,
)
from video_agent.contracts import (
    ARTIFACT_RENDER_PROPS,
    ARTIFACT_REPORT,
    ARTIFACT_SCENES,
    ARTIFACT_SEO,
    ARTIFACT_THUMBNAIL,
    ARTIFACT_VIDEO,
    ARTIFACT_VISUAL_CONTACT_SHEET,
    ARTIFACT_VISUAL_REVIEW,
    EVENT_LOG,
    repo_root,
)
from video_agent.operator import assert_operator_qa_passed, write_operator_review
from video_agent.providers.mock import MockProvider
from video_agent.runtime.providers import AUDIO_SUBPROCESS_ENV, SubprocessAudioTaskProvider
from video_agent.stages.assets import prepare_assets
from video_agent.stages.render import render_with_remotion
from video_agent.stages.scene import run_scene_stage
from video_agent.stages.script import run_script_stage
from video_agent.stages.thumbnail import create_thumbnail_and_seo
from video_agent.storage.atomic import atomic_write_text
from video_agent.utils.json_io import read_json, read_yaml, write_json
from video_agent.utils.logging import EventLogger
from video_agent.utils.paths import create_job_dir
from video_agent.utils.validation import validate_json
from video_agent.visual.spans import GRAPHIC_LAYOUTS

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
    # Prepared Shorts have already completed scene validation, background
    # acquisition, crop planning, TTS, and audio mix in the Shorts builder.
    # Render must consume those exact artifacts instead of re-entering the
    # generic prepare_assets/TTS path.
    prepared_short: bool = False
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
    configured_disclaimer = branding_cfg.get("disclaimer_video_path")
    disclaimer_video_source = (
        _resolve_brand_video_source(channel_config, "disclaimer")
        if isinstance(configured_disclaimer, str) and configured_disclaimer.strip()
        else None
    )
    logo_public = None
    intro_video_public = None
    outro_video_public = None
    disclaimer_video_public = None
    disclaimer_sec = 0.0
    if intro_video_source:
        intro_sec = _probe_duration_sec(intro_video_source)
    if outro_video_source:
        outro_sec = _probe_duration_sec(outro_video_source)
    if disclaimer_video_source:
        probed_disclaimer_sec = _probe_duration_sec(disclaimer_video_source)
        configured_disclaimer_sec = branding_cfg.get("disclaimer_sec")
        disclaimer_sec = (
            probed_disclaimer_sec
            if configured_disclaimer_sec is None
            else min(
                probed_disclaimer_sec,
                max(0.0, float(configured_disclaimer_sec)),
            )
        )
    if logo_source or intro_video_source or outro_video_source or disclaimer_video_source:
        dest_dir = root / "remotion" / "public" / "branding" / str(channel_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        if logo_source:
            dest = dest_dir / logo_source.name
            materialize_media(logo_source, dest)
            logo_public = f"branding/{channel_id}/{logo_source.name}"
        if intro_video_source:
            dest = dest_dir / intro_video_source.name
            materialize_media(intro_video_source, dest)
            intro_video_public = f"branding/{channel_id}/{intro_video_source.name}"
        if outro_video_source:
            dest = dest_dir / outro_video_source.name
            materialize_media(outro_video_source, dest)
            outro_video_public = f"branding/{channel_id}/{outro_video_source.name}"
        if disclaimer_video_source:
            dest = dest_dir / disclaimer_video_source.name
            materialize_media(disclaimer_video_source, dest)
            disclaimer_video_public = f"branding/{channel_id}/{disclaimer_video_source.name}"
    # Hybrid graphic cards: graphic scenes render the generated card shrunk &
    # centered over a fixed brand-gradient background video (kills the static
    # "slideshow" feel of consecutive graphic scenes). Enabled via
    # ``visual.hybrid_card.enabled``; bg path defaults to the bundled brand
    # gradient, override with ``visual.hybrid_card.bg``.
    hybrid_cfg = (channel_config.get("visual") or {}).get("hybrid_card") or {}
    hybrid_card_bg = (
        str(hybrid_cfg.get("bg") or "assets/brand_bg_soft.mp4")
        if hybrid_cfg.get("enabled")
        else None
    )
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
        # Replaceable video clip between intro and main content. Its duration
        # is probed from the file and may be shortened by branding.disclaimer_sec.
        "disclaimer_video_path": disclaimer_video_public,
        "disclaimer_sec": disclaimer_sec,
        # Hybrid graphic cards over a fixed brand-gradient bg (visual.hybrid_card).
        # None → graphic cards render full-bleed (legacy).
        "hybrid_card_bg": hybrid_card_bg,
    }


def _load_style(channel_config: dict) -> dict:
    return read_json(repo_root() / channel_config["style_dna"]["path"])


def _is_key_scene(scene: dict) -> bool:
    layout = scene.get("layout") or ""
    if layout in {
        "short_hook",
        "short_cta",
        "graphic_label_callout",
        "graphic_comparison",
        "graphic_checklist",
    }:
        return True
    if scene.get("retention_function") in {"hook", "proof", "payoff", "cta"}:
        return True
    prompt = (scene.get("visual_prompt") or "").lower()
    key_terms = {
        "package",
        "label",
        "ingredients",
        "fibra",
        "harina",
        "compare",
        "turn",
        "rotate",
        "back label",
    }
    if any(term in prompt for term in key_terms):
        return True
    return False


def _scene_visual_issues(scene: dict, *, span_covered: bool = False) -> list[dict]:
    issues = []
    source = scene.get("source")
    provider = scene.get("provider")
    selection = scene.get("selection") or {}
    score = selection.get("score")
    asset_key = (
        f"{provider}:{scene.get('provider_asset_id')}"
        if provider and scene.get("provider_asset_id")
        else None
    )

    if source == "generated_placeholder":
        if span_covered:
            # Shorts enforced-schedule path: a render-eligible background_media
            # track (real native/AI asset, schedule QA PASS) fully covers this
            # scene at render time, so the placeholder BACKGROUND is an unused
            # fallback layer that never appears on screen. Blocking here was a
            # false positive that killed otherwise-valid renders (bug-485).
            issues.append(
                {
                    "type": "PLACEHOLDER_BACKGROUND_COVERED_BY_SPAN",
                    "severity": "warning",
                    "message": (
                        "Background is a generated placeholder, but the enforced "
                        "visual schedule covers this scene with a real asset track — "
                        "the placeholder never renders."
                    ),
                }
            )
        else:
            # HARD GATE (user rule): never ship a video with a blank/placeholder scene.
            # A placeholder = no real asset was found → ERROR so _validate_visual_review
            # blocks the render. Fix the asset (re-fetch) and re-render; do not render
            # until EVERY scene has a real asset.
            issues.append(
                {
                    "type": "PLACEHOLDER_USED",
                    "severity": "error",
                    "message": "Scene fell back to a generated placeholder (no real asset) — render blocked.",
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

    # Photo-backed background on a NON-graphic scene (bug-455 follow-up):
    # the render already avoids treating this as a static slide (Ken Burns
    # motion is applied), and the asset cascade already tries video before
    # photo at every strictness tier, so this is not necessarily wrong — but
    # the operator has no visibility into which scenes ended up on a still
    # photo instead of real footage. Surface it as a warning (never blocks
    # render) so it can be reviewed/re-selected if it recurs too often.
    # Graphic-layout scenes are exempt: their living background is a
    # secondary element behind a generated card, not the primary visual.
    if scene.get("media_kind") == "image" and scene.get("layout") not in GRAPHIC_LAYOUTS:
        issues.append(
            {
                "type": "PHOTO_BACKED_BACKGROUND",
                "severity": "warning",
                "message": (
                    "Scene background resolved to a still photo, not real video "
                    "footage (Ken Burns motion applied at render time, but no "
                    "live b-roll)."
                ),
            }
        )

    # A graphic-layout scene whose designed card never materialized (image
    # generation failed and the late-recovery sweep found nothing) silently
    # downgrades to a plain video background — real footage, so not blocking,
    # but the intended card treatment is lost quality the operator must see
    # (Codex bridge 20260704-130051: scene-27 recipe_snapshot shipped without
    # its card and QA still PASSed with zero signal).
    graphic = scene.get("graphic") or {}
    if (
        scene.get("layout") in GRAPHIC_LAYOUTS
        and graphic.get("needed") is not False
        and not graphic.get("image_ref")
    ):
        detail = f" Generation error: {graphic.get('error')}" if graphic.get("failed") else ""
        issues.append(
            {
                "type": "GRAPHIC_CARD_MISSING",
                "severity": "warning",
                "message": (
                    f"Graphic-layout scene ({scene.get('layout')}) has no generated "
                    f"card image — downgraded to a plain video background.{detail}"
                ),
            }
        )

    asset_match_status = selection.get("asset_match_status") or (
        "weak_match" if selection.get("weak_match") else "unknown"
    )

    # Skip match-quality checks for non-stock sources (local_directory, generated_placeholder
    # without stock API, graphic_fallback). These sources don't involve stock search matching.
    if source in {"local_directory", "generated_placeholder"} and not selection:
        if asset_key:
            scene["asset_key"] = asset_key
        return issues
    if asset_match_status == "graphic_fallback":
        if asset_key:
            scene["asset_key"] = asset_key
        return issues

    # Detailed Context for Errors
    context_str = f"Scene ID: {scene.get('scene_id')} | Layout: '{scene.get('layout')}' | Prompt: '{scene.get('visual_prompt')}'"
    if selection:
        context_str += f" | Selected Asset: {selection.get('selected_asset_title', '')} ({selection.get('source_url', '')})"
        if selection.get("failed_required_terms"):
            context_str += f" | Failed Terms: {selection.get('failed_required_terms')}"
        if selection.get("fallback_reason"):
            context_str += f" | Fallback Reason: {selection.get('fallback_reason')}"

    if _is_key_scene(scene):
        if asset_match_status == "weak_match":
            # WARNING, not error. A "weak" match is usually a query-ranked Pexels
            # VIDEO that lacks the strict tag/alt metadata needed to score "strong"
            # (Pexels videos carry no tags/alt at all) — it is still on-topic footage
            # for the right demographic. Hard-failing the WHOLE render over it is
            # worse than the placeholder gradient that critical scenes already fall
            # back to, so downgrade to a warning and let the render proceed with the
            # real asset. Only a generated_placeholder (truly blank) blocks the render
            # (PLACEHOLDER_USED=error); a weak but real match is allowed.
            issues.append(
                {
                    "type": "WEAK_MATCH_ON_CRITICAL_SCENE",
                    "severity": "warning",
                    "message": f"Weak (query-ranked) match on critical scene — allowed. {context_str}",
                }
            )
        elif asset_match_status in {"no_match", "unknown", "", None}:
            # A weak/low match is still REAL footage (not blank), so it is allowed
            # (warning). The genuinely-blank case is a generated_placeholder, flagged
            # PLACEHOLDER_USED=error above, which blocks the render. So this match-
            # quality flag must NOT block (would wrongly reject real-but-weak scenes).
            issues.append(
                {
                    "type": "NO_SAFE_VISUAL_ASSET",
                    "severity": "warning",
                    "message": f"Weak/low visual match on key scene (real footage kept). {context_str}",
                }
            )
    else:
        # Non-key scene
        if asset_match_status == "weak_match":
            if _is_contradictory(scene, selection):
                issues.append(
                    {
                        "type": "CONTRADICTORY_WEAK_MATCH",
                        "severity": "error",
                        "message": f"Contradictory weak match is not allowed. {context_str}",
                    }
                )
            else:
                issues.append(
                    {
                        "type": "WEAK_MATCH_ON_NON_CRITICAL_SCENE",
                        "severity": "warning",
                        "message": f"Weak match fallback accepted for non-critical scene. {context_str}",
                    }
                )

    if asset_key:
        scene["asset_key"] = asset_key
    return issues


def _is_contradictory(scene: dict, asset_selection: dict) -> bool:
    prompt = (scene.get("visual_prompt") or "").lower()
    tags = set(str(t).lower() for t in asset_selection.get("candidate_tags", []))
    if "supermarket" in prompt or "store" in prompt:
        if "sleep" in tags or "bed" in tags or "sleeping" in tags:
            return True
    if ("turn" in prompt or "read" in prompt) and "label" in prompt:
        if "slice" in tags or "cutting" in tags:
            return True
    return False


def _span_covered_scene_ids(job_dir: Path) -> set[str]:
    """Scene ids fully covered by REAL background_media tracks of an ENFORCED,
    QA-passed compiled visual schedule (Shorts only; long-form has no schedule).

    Only these scenes may downgrade the placeholder-background gate: with the
    enforced schedule the Remotion timeline renders the track assets, so the
    per-scene background file is a hidden fallback layer.
    """
    schedule_path = job_dir / "json" / "compiled_asset_schedule.json"
    qa_path = job_dir / "json" / "compiled_asset_schedule_qa.json"
    if not schedule_path.exists() or not qa_path.exists():
        return set()
    try:
        schedule = read_json(schedule_path)
        schedule_qa = read_json(qa_path)
    except Exception:
        return set()
    if str(schedule_qa.get("verdict")) != "PASS" or str(schedule_qa.get("mode")) != "enforced":
        return set()
    covered: set[str] = set()
    for track in schedule.get("tracks") or []:
        if str(track.get("track_type")) != "background_media":
            continue
        provider = str(track.get("provider") or "").lower()
        asset_id = str(track.get("asset_id") or "").lower()
        if provider == "graphic_fallback" or "placeholder" in asset_id:
            continue
        for sid in track.get("scene_ids") or []:
            covered.add(str(sid))
    return covered


def _add_visual_qa(review: dict, *, span_covered_ids: set[str] | None = None) -> dict:
    span_covered_ids = span_covered_ids or set()
    seen_assets = {}
    issue_count = 0
    for scene in review["scenes"]:
        issues = _scene_visual_issues(
            scene, span_covered=str(scene.get("scene_id")) in span_covered_ids
        )
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
    for scene_asset, scene in zip(asset_scenes, doc_scenes, strict=False):
        selection = scene_asset.get("asset_selection") or {}
        scenes.append(
            {
                "scene_id": scene_asset["scene_id"],
                "layout": scene.get("layout"),
                "retention_function": scene.get("retention_function"),
                "visual_prompt": scene.get("visual_prompt"),
                "on_screen_text": scene.get("on_screen_text"),
                "source": scene_asset.get("source"),
                "provider": scene_asset.get("provider"),
                "provider_asset_id": scene_asset.get("provider_asset_id"),
                "source_url": scene_asset.get("source_url"),
                "asset_tier": scene_asset.get("asset_tier"),
                "media_kind": scene_asset.get("media_kind"),
                # Graphic-card outcome (image_ref / failed / error) so QA can
                # flag a graphic-layout scene whose card never materialized.
                "graphic": scene.get("graphic") if isinstance(scene.get("graphic"), dict) else None,
                "asset_match_status": selection.get("asset_match_status"),
                "query": selection.get("query"),
                "selection": selection,
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
            summary["by_provider"][scene["provider"]] = (
                summary["by_provider"].get(scene["provider"], 0) + 1
            )
        selection = scene.get("selection") or {}
        if isinstance(selection.get("score"), (int, float)):
            selection_scores.append(selection["score"])
        for provider in selection.get("searched_providers") or []:
            summary["searched_providers"][provider] = (
                summary["searched_providers"].get(provider, 0) + 1
            )
    if selection_scores:
        summary["selection_scores"] = {
            "min": min(selection_scores),
            "avg": round(sum(selection_scores) / len(selection_scores), 1),
            "max": max(selection_scores),
        }
    review = _add_visual_qa(
        {"job_id": job_id, "summary": summary, "scenes": scenes},
        span_covered_ids=_span_covered_scene_ids(job_dir),
    )
    review["contact_sheet"] = ARTIFACT_VISUAL_CONTACT_SHEET
    write_json(job_dir / ARTIFACT_VISUAL_REVIEW, review)
    return review


def _validate_visual_review(visual_review: dict, *, render: bool = True) -> None:
    # bug: this used to raise a single hardcoded message ("Weak match on
    # critical scenes (hook/CTA)") regardless of which issue actually
    # triggered the block -- e.g. a PLACEHOLDER_USED (no real asset found at
    # all) got misreported as a weak-match problem, sending debugging down
    # the wrong path entirely. Report every actual error-severity issue.
    #
    # The HARD GATE (user rule) is "never SHIP a video with a blank/placeholder
    # scene" -- when render=False this run produces artifacts only (no video
    # is ever rendered/shipped), so the gate has nothing to protect and must
    # not block artifact-only/dry-run pipeline runs.
    if not render:
        return
    error_issues = [
        (scene.get("scene_id"), issue)
        for scene in visual_review.get("scenes", [])
        for issue in scene.get("qa", {}).get("issues", [])
        if issue.get("severity") == "error"
    ]
    if error_issues:
        from video_agent.orchestrator.stages import StageInputMissingError

        details = "; ".join(
            f"{scene_id}: {issue.get('type')} — {issue.get('message')}"
            for scene_id, issue in error_issues
        )
        raise StageInputMissingError(
            f"QA validation failed: {details} "
            "Please check visual_review.json for details."
        )


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
        suffix = (
            f" ({issue_count} issue)"
            if issue_count == 1
            else f" ({issue_count} issues)"
            if issue_count
            else ""
        )
        visual_lines.append(f"- {scene['scene_id']}: {source} {provider}/{asset_id}{suffix}")

    seo_path = _resolve_json_file(job_dir, "seo.json")
    pinned_comment_lines = []
    if seo_path.exists():
        try:
            seo_data = read_json(seo_path)
            comments = seo_data.get("suggested_pinned_comments")
            if isinstance(comments, str):
                pinned_comment_lines.extend(["", "## Suggested Pinned Comment", comments])
            elif isinstance(comments, dict):
                eb = comments.get("engagement_boosting") or comments.get("engage") or ""
                sg = comments.get("subscriber_growth") or comments.get("subscriber") or ""
                merged = f"{eb}\n\n{sg}".strip()
                pinned_comment_lines.extend(["", "## Suggested Pinned Comment", merged or "n/a"])
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


def _read_sidecar_json(job_dir: Path, name: str) -> dict | None:
    """Read a long-form sidecar artifact (``json/<name>`` then root), or ``None``."""
    for candidate in (job_dir / "json" / name, job_dir / name):
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def _recompile_asset_schedule(job_dir: Path, scenes: list[dict], fps: int) -> dict | None:
    """Recompile the asset schedule from the FINAL post-TTS scene durations.

    The ``visual_schedule`` stage compiles against scenes.json *before* render-time
    TTS finalizes per-scene durations, so the on-disk artifact is stale by the time
    the renderer consumes it. Recompiling here keeps the background-layer frame
    boundaries identical to the actual scene timeline. Falls back to ``None`` (→ the
    on-disk artifact) when inputs are missing or compilation fails.
    """
    spans = _read_sidecar_json(job_dir, "visual_spans.json")
    if not spans:
        return None
    try:
        from video_agent.visual import compile_asset_schedule

        source_clips = _read_sidecar_json(job_dir, "span_source_clips.json")
        return compile_asset_schedule(
            scene_doc={"scenes": scenes},
            visual_spans=spans,
            fps=fps,
            timing_source="tts_final",
            source_clips=source_clips or None,
        )
    except Exception as exc:  # noqa: BLE001 - fall back to on-disk artifact
        logging.getLogger(__name__).warning(
            "Visual schedule recompile failed (%s); using on-disk artifact", exc
        )
        return None


def _comp_duration_in_frames(
    scenes: list[dict],
    *,
    intro_sec: float,
    outro_sec: float,
    fps: int,
    disclaimer_sec: float = 0.0,
) -> int:
    """Total composition frames for the long-form ``ChannelVideo`` layout.

    Mirrors ``ChannelVideo.tsx``: the scene layer is shifted by the intro plus
    medical disclaimer and an outro is appended, so the composition length is
    ``introFrames + disclaimerFrames + totalSceneFrames + outroFrames`` — NOT the scenes-only
    ``visual_schedule.total_duration_in_frames``. Per-quantity rounding uses
    ``floor(x*fps + 0.5)`` to match JS ``Math.round`` (the schedule + renderer use
    the same), so the result is frame-exact. Root.tsx consumes this via
    ``render.duration_in_frames``; without it an enforced render would cut the intro
    shift + the entire outro.
    """

    def _f(sec: float) -> int:
        return math.floor(float(sec or 0.0) * fps + 0.5)

    scene_frames = sum(_f(s.get("duration_sec")) for s in (scenes or []))
    return _f(intro_sec) + _f(disclaimer_sec) + scene_frames + _f(outro_sec)


def _build_render_props(
    *,
    channel_config: dict,
    style: dict,
    render_base: dict,
    scene_doc: dict,
    audio: dict,
    seo: dict,
    branding: dict,
    fps: int,
    include_duration_in_frames: bool,
) -> dict:
    """Assemble the render_props dict shared by ``run_pipeline`` and
    ``render_operator_job``.

    ``duration_sec`` always covers scenes + branding intro/disclaimer/outro.
    ``duration_in_frames``
    (the exact composition frame count) is pinned only for long-form
    (``include_duration_in_frames``) — shorts mount the scene layer at frame 0 and let
    Root size to the schedule total, so it must stay absent there. Centralized so the
    duration math lives in ONE place instead of being edited in two call sites.
    """
    scenes = scene_doc["scenes"]
    scene_duration_sec = round(sum(float(s.get("duration_sec") or 0.0) for s in (scenes or [])), 1)
    # Shorts normalize this shared branding field to disabled before reaching
    # this builder, so content selection stays independent from frame pinning.
    disclaimer_sec = disclaimer_duration_sec(branding)
    render = dict(render_base) | {
        "duration_sec": (
            scene_duration_sec
            + branding["intro_sec"]
            + disclaimer_sec
            + branding["outro_sec"]
        )
    }
    if include_duration_in_frames:
        render["duration_in_frames"] = _comp_duration_in_frames(
            scenes,
            intro_sec=branding["intro_sec"],
            outro_sec=branding["outro_sec"],
            fps=fps,
            disclaimer_sec=disclaimer_sec,
        )
    return {
        "channel": channel_config["channel"],
        "style": style,
        "render": render,
        "scenes": scenes,
        "audio": audio,
        "seo": seo,
        "branding": branding,
    }


def _attach_enforced_visual_schedule(
    render_props: dict, job_dir: Path, channel_config: dict
) -> None:
    """Inject the compiled asset schedule into ``render_props`` only when long-form
    span planning is ``enforced``.

    In ``report_only`` / ``disabled`` (the default) the schedule is omitted, so the
    renderer keeps the legacy per-scene background — frame-identical to before this
    feature. When enforced, the schedule is recompiled from the
    final post-TTS scene durations in ``render_props`` (the on-disk sidecars are
    stale — compiled before render-time duration sync); callers without ``scenes``
    fall back to the on-disk artifacts.

    Enforced span planning is a LONG-FORM feature: shorts mount the scene layer at
    frame 0 and own their render_props via ``video_agent.shorts``, so a short job
    dir must never receive the long enforced schedule. Short dirs already lack the
    long sidecars (so this was a no-op in practice); the explicit guard codifies
    the "independent of the Shorts render path" invariant and keeps it leak-proof
    if a short ever runs under an enforced channel config.
    """
    from video_agent.visual import resolve_visual_span_config

    if _is_short_job_dir(job_dir, channel_config):
        return
    if resolve_visual_span_config(channel_config or {}).get("mode") != "enforced":
        return

    scenes = render_props.get("scenes")
    fps = int(((channel_config or {}).get("render") or {}).get("fps") or 30)

    schedule = _recompile_asset_schedule(job_dir, scenes, fps) if scenes else None
    if schedule is None:
        schedule = _read_sidecar_json(job_dir, "compiled_asset_schedule.json")
    if schedule is not None:
        render_props["visual_schedule"] = schedule



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

    logger.log(
        "JOB_STARTED",
        {"job_id": job_id, "channel_id": channel_config["channel"]["id"], "cost_usd": 0},
    )
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
        tts_config=(channel_config.get("tts") or {}) | {"music": channel_config.get("music") or {}},
        channel_id=channel_config["channel"]["id"],
    )
    seo = create_thumbnail_and_seo(provider, channel_config, style, idea, job_dir)
    validate_json(seo, root / "schemas/seo.schema.json")
    logger.log("ASSETS_READY", {"job_id": job_id, "cost_usd": 0})

    branding = _prepare_branding(channel_config)
    from video_agent.operator import resync_seo_chapters

    content_offset_sec = (
        float(branding.get("intro_sec") or 0.0)
        + disclaimer_duration_sec(branding)
    )
    if resync_seo_chapters(
        job_dir,
        scene_doc=scene_doc,
        script=script,
        content_offset_sec=content_offset_sec,
    ):
        seo = read_json(_resolve_json_file(job_dir, "seo.json"))
    render_props = _build_render_props(
        channel_config=channel_config,
        style=style,
        render_base=channel_config["render"],
        scene_doc=scene_doc,
        audio=assets["audio"],
        seo=seo,
        branding=branding,
        fps=int((channel_config["render"] or {}).get("fps") or 30),
        include_duration_in_frames=True,
    )
    _attach_enforced_visual_schedule(render_props, job_dir, channel_config)
    write_json(job_dir / ARTIFACT_RENDER_PROPS, render_props)
    validate_json(render_props, root / "schemas/render-props.schema.json")
    visual_review = _write_visual_review(job_dir, job_id, assets, scene_doc)
    _validate_visual_review(visual_review, render=options.render)

    video_path = None
    if options.render:
        video_path = job_dir / ARTIFACT_VIDEO
        render_with_remotion(
            job_dir / ARTIFACT_RENDER_PROPS,
            video_path,
            stop_request_path=options.stop_request_path,
            notify_telegram=options.notify_telegram,
        )
        logger.log("RENDERED", {"job_id": job_id, "video_path": str(video_path), "cost_usd": 0})

    report_path = _write_report(
        job_dir, job_id, channel_config, idea, options.render, visual_review
    )
    logger.log("JOB_COMPLETED", {"job_id": job_id, "cost_usd": 0})
    return PipelineResult(
        job_id=job_id,
        job_dir=job_dir,
        video_path=video_path if video_path and video_path.exists() else None,
        thumbnail_path=job_dir / ARTIFACT_THUMBNAIL,
        seo_path=job_dir / ARTIFACT_SEO,
        report_path=report_path,
    )


_TIMELINE_OVERFLOW_TOL_SEC = 0.05  # ignore sub-frame rounding noise


def _warn_if_timeline_exceeds_audio(
    log: logging.Logger, scenes: list[dict], total_audio: float, *, strategy: str
) -> bool:
    """Warn when the 3.0 s min-scene floor pushes the scene timeline past the
    measured narration (-> the last scene lingers with no voice = trailing dead air).

    The floor is a deliberate readability guard (no scene flashes under 3.0 s), so
    when it forces an overflow the right fix is upstream (fewer scenes / longer
    narration). This only SURFACES that mismatch for review; it changes no
    duration. Returns True when it warned."""
    timeline = sum(float(s.get("duration_sec") or 0.0) for s in scenes)
    if timeline > total_audio + _TIMELINE_OVERFLOW_TOL_SEC:
        log.warning(
            "Duration sync (%s): scene timeline %.2fs exceeds measured narration "
            "%.2fs by %.2fs after the 3.0s min-scene floor on %d scenes — trailing "
            "dead air; reduce scene count or lengthen narration",
            strategy,
            timeline,
            total_audio,
            timeline - total_audio,
            len(scenes),
        )
        return True
    return False


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

            # Whisper ``audio_offset_sec`` values are cumulative *plan* durations,
            # so the raw per-scene deltas reproduce the LLM estimates and carry no
            # audio correction on their own. Use the deltas only as relative shares
            # and scale them to the MEASURED narration length, so the plan/measure
            # gap is spread across ALL scenes instead of dumped onto the last one.
            if scenes and all(s["id"] in offsets for s in scenes):
                # Offsets confirm whisper mapped every scene against real audio.
                # Weight by each scene's plan duration (the offsets are merely the
                # cumulative sum of these, so they add no extra per-scene signal),
                # then rescale to the measured total below.
                plan_durs = [float(s.get("duration_sec") or 0.0) for s in scenes]
                plan_total = sum(plan_durs)
                if plan_total > 0:
                    usable = max(0.0, total_audio - TAIL_PAD_SEC)
                    last_idx = len(scenes) - 1
                    for idx, scene in enumerate(scenes):
                        dur = usable * (plan_durs[idx] / plan_total)
                        if idx == last_idx:
                            dur += TAIL_PAD_SEC
                        scene["duration_sec"] = round(max(3.0, dur), 3)
                    _warn_if_timeline_exceeds_audio(log, scenes, total_audio, strategy="whisper")
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
    total_audio = None
    for fname in ("narration.wav", "narration_mixed.m4a"):
        p = job_dir / "assets" / fname
        if p.exists() and p.stat().st_size > 0:
            try:
                import soundfile as sf

                info = sf.info(str(p))
                total_audio = float(info.duration)
                break
            except Exception as exc:
                log.warning("Duration sync strategy B: cannot read audio %s (%s)", fname, exc)

    if total_audio is None:
        return  # nothing to measure — let existing values stand

    original_total = sum(float(s.get("duration_sec") or 1) for s in scenes)
    if original_total <= 0:
        return

    for scene in scenes:
        ratio = float(scene.get("duration_sec") or 1) / original_total
        scene["duration_sec"] = round(max(3.0, ratio * total_audio), 3)

    _warn_if_timeline_exceeds_audio(log, scenes, total_audio, strategy="proportional")
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


def _is_short_job_dir(job_dir: Path, channel_config: dict | None = None) -> bool:
    if job_dir.parent.name == "shorts":
        return True
    if (job_dir / "json" / "short_render_props.json").exists() or (
        job_dir / "short_render_props.json"
    ).exists():
        return True
    try:
        render_resolution = str(
            ((channel_config or {}).get("render") or {}).get("resolution") or ""
        )
        w_str, h_str = render_resolution.lower().split("x", 1)
        return int(h_str) > int(w_str)
    except (ValueError, AttributeError):
        return False


def _prepared_short_inputs_present(job_dir: Path) -> bool:
    """True when BOTH artifacts the prepared-render owner consumes are on disk: the
    builder handoff (short_render_props.json) and assets_manifest.json. The prepared
    branch reads both unconditionally, so auto-routing must require both — otherwise
    a partial/older Short dir (handoff but no manifest) would crash on read where the
    legacy path would have rebuilt assets."""

    def _either(name: str) -> bool:
        return (job_dir / "json" / name).exists() or (job_dir / name).exists()

    return _either("short_render_props.json") and _either("assets_manifest.json")


def _should_use_prepared_short(*, prepared_short: bool, is_short_job: bool, job_dir: Path) -> bool:
    """A Short job dir with the prepared inputs must ALWAYS render through the shared
    prepared-short owner, regardless of which entry point invoked us. The explicit
    ``prepared_short`` flag forces it (caller asserts readiness); otherwise we
    auto-detect so the CLI (and any caller that forgets the flag) no longer silently
    falls into the legacy prepare_assets path and renders the same dir differently.
    Auto-detect requires BOTH prepared inputs so a partial dir falls back to legacy
    instead of crashing."""
    return bool(prepared_short) or (is_short_job and _prepared_short_inputs_present(job_dir))


def _scene_duration_sum(scene_doc: dict) -> float:
    return round(
        sum(float(scene.get("duration_sec") or 0.0) for scene in (scene_doc.get("scenes") or [])), 1
    )


def _snapshot_scene_durations(scene_doc: dict) -> dict[str, float]:
    snapshot: dict[str, float] = {}
    for index, scene in enumerate((scene_doc or {}).get("scenes") or []):
        scene_id = str(scene.get("id") or scene.get("scene_id") or index)
        snapshot[scene_id] = float(scene.get("duration_sec") or 0.0)
    return snapshot


def _restore_scene_durations(scene_doc: dict, snapshot: dict[str, float]) -> None:
    if not snapshot:
        return
    for index, scene in enumerate((scene_doc or {}).get("scenes") or []):
        scene_id = str(scene.get("id") or scene.get("scene_id") or index)
        if scene_id in snapshot:
            scene["duration_sec"] = snapshot[scene_id]
    scene_doc["total_duration_sec"] = int(math.ceil(_scene_duration_sum(scene_doc)))


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

    logger.log(
        "OPERATOR_RENDER_STARTED", {"job_id": job_id, "channel_id": channel_config["channel"]["id"]}
    )

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

    is_short_job = _is_short_job_dir(job_dir, channel_config)
    use_prepared = _should_use_prepared_short(
        prepared_short=options.prepared_short, is_short_job=is_short_job, job_dir=job_dir
    )
    if use_prepared:
        if not options.prepared_short:
            logger.log("OPERATOR_RENDER_PREPARED_AUTO", {"job_id": job_id})
        from video_agent.shorts import paths as short_paths
        from video_agent.shorts.builder.render_props import build_prepared_short_render_props

        handoff = read_json(_resolve_json_file(job_dir, short_paths.SHORT_RENDER_PROPS_FILE))
        assets_manifest = read_json(_resolve_json_file(job_dir, "assets_manifest.json"))
        branding = _prepare_branding(channel_config)
        visual_mode = str(
            ((channel_config.get("shorts") or {}).get("visual_timeline") or {}).get("mode")
            or "disabled"
        )
        render_props = build_prepared_short_render_props(
            short_dir=job_dir,
            channel_config=channel_config,
            style=style,
            scenes=scene_doc,
            assets_manifest=assets_manifest,
            seo=seo,
            branding=branding,
            handoff=handoff,
            visual_timeline_mode=visual_mode,
        )
        write_json(job_dir / ARTIFACT_RENDER_PROPS, render_props)
        validate_json(render_props, root / "schemas/render-props.schema.json")

        visual_review = _write_visual_review(job_dir, job_id, assets_manifest, scene_doc)
        _validate_visual_review(visual_review, render=options.render)

        video_path = None
        if options.render:
            video_path = job_dir / ARTIFACT_VIDEO
            render_with_remotion(
                job_dir / ARTIFACT_RENDER_PROPS,
                video_path,
                stop_request_path=options.stop_request_path,
                notify_telegram=options.notify_telegram,
            )
            logger.log("RENDERED", {"job_id": job_id, "video_path": str(video_path), "cost_usd": 0})

        idea = {"topic": seo.get("title") or job_id}
        report_path = _write_report(
            job_dir, job_id, channel_config, idea, options.render, visual_review
        )
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

    short_duration_snapshot = _snapshot_scene_durations(scene_doc) if is_short_job else {}

    # ── Duration sync: recalculate long-form scene duration_sec from actual audio ─
    # Shorts have already passed scene validation + audio tail repair before
    # render_operator_job is called. Resyncing them against cached narration audio
    # compresses approved scene plans (for example 26.4s -> 20.0s), so keep the
    # scene-plan timings authoritative for Short jobs.
    if not is_short_job:
        _sync_scene_durations_from_audio(job_dir, scene_doc)

    if _should_prepare_audio_in_subprocess(job_dir, channel_config):
        _run_prepare_assets_audio_subprocess(job_dir, options.channel_path)
        scene_doc = read_json(_resolve_json_file(job_dir, "scenes.json"))
        if is_short_job:
            _restore_scene_durations(scene_doc, short_duration_snapshot)

    # Force portrait stock-asset orientation when this job is a Short, so the
    # render-path call to prepare_assets stays in sync with the TTS-path
    # override in shorts/audio.py. Without this, Shorts re-fetch landscape
    # Pexels clips here and overwrite the portrait ones from the TTS stage.
    visual_config = dict(channel_config.get("visuals") or {})
    if is_short_job and (channel_config.get("shorts") or {}).get("source", {}).get(
        "prefer_vertical_assets", True
    ):
        visual_config["orientation"] = "portrait"

    assets = prepare_assets(
        job_dir,
        style,
        scene_doc,
        visual_config=visual_config,
        tts_config=(channel_config.get("tts") or {}) | {"music": channel_config.get("music") or {}},
        channel_id=channel_config["channel"]["id"],
    )
    branding = _prepare_branding(channel_config)
    if is_short_job:
        _restore_scene_durations(scene_doc, short_duration_snapshot)
        write_json(job_dir / ARTIFACT_SCENES, scene_doc)
        validate_json(scene_doc, root / "schemas/scenes.schema.json")
        branding = without_long_form_branding(branding)
    else:
        # AUTHORITATIVE chapter resync (bug-531): prepare_assets audio-fits the
        # scene timeline, so only NOW does scene_doc reflect what the viewer
        # will actually watch. The whisper-stage resync ran earlier against
        # planned durations and can overshoot the real video length.
        try:
            from video_agent.operator import resync_seo_chapters

            content_offset_sec = (
                float(branding.get("intro_sec") or 0.0)
                + disclaimer_duration_sec(branding)
            )
            chapters = resync_seo_chapters(
                job_dir,
                scene_doc=scene_doc,
                content_offset_sec=content_offset_sec,
            )
            if chapters:
                logger.log(
                    "OPERATOR_RENDER_PROGRESS",
                    {
                        "job_id": job_id,
                        "step": "seo_chapters_final_resync",
                        "chapter_count": len(chapters),
                    },
                )
        except Exception as exc:  # noqa: BLE001 — chapters must never kill a render
            logger.log(
                "OPERATOR_RENDER_PROGRESS",
                {
                    "job_id": job_id,
                    "step": "seo_chapters_final_resync_failed",
                    "error": str(exc)[:200],
                },
            )
    render_config = channel_config["render"].copy()
    if job_dir.parent.name == "shorts":
        shorts_render = (channel_config.get("shorts") or {}).get("render") or {}
        for k, v in shorts_render.items():
            if v is not None:
                render_config[k] = v

    # Long-form pins the exact composition frame count (intro + scenes + outro) so
    # an enforced render is not sized to the scenes-only schedule total (which would
    # cut the intro shift + outro). Shorts mount the scene layer at frame 0 → leave
    # it absent so Root keeps using the schedule total.
    render_props = _build_render_props(
        channel_config=channel_config,
        style=style,
        render_base=render_config,
        scene_doc=scene_doc,
        audio=assets["audio"],
        seo=seo,
        branding=branding,
        fps=int(render_config.get("fps") or 30),
        include_duration_in_frames=not is_short_job,
    )
    _attach_enforced_visual_schedule(render_props, job_dir, channel_config)
    write_json(job_dir / ARTIFACT_RENDER_PROPS, render_props)
    validate_json(render_props, root / "schemas/render-props.schema.json")
    visual_review = _write_visual_review(job_dir, job_id, assets, scene_doc)
    _validate_visual_review(visual_review, render=options.render)

    video_path = None
    if options.render:
        video_path = job_dir / ARTIFACT_VIDEO
        render_with_remotion(
            job_dir / ARTIFACT_RENDER_PROPS,
            video_path,
            stop_request_path=options.stop_request_path,
            notify_telegram=options.notify_telegram,
        )
        logger.log("RENDERED", {"job_id": job_id, "video_path": str(video_path), "cost_usd": 0})

    idea = {"topic": seo.get("title") or job_id}
    report_path = _write_report(
        job_dir, job_id, channel_config, idea, options.render, visual_review
    )
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
