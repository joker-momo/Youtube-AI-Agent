"""Shared scene-prep helpers used by BOTH asset pipelines.

Source-dir / local-image resolution and the per-scene background sourcing report
are driven by both the long `prepare_assets` and the Shorts `prepare.py`, so they
live here (NOT in shorts/assets — long uses them too). Leaf module: must not
import from video_agent.stages or video_agent.shorts (see
tests/test_asset_layer_boundary.py).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.contracts import repo_root
from video_agent.utils.json_io import write_json

SUPPORTED_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def _resolve_source_dir(source_dir: str | None) -> Path | None:
    if not source_dir:
        return None
    path = Path(source_dir)
    if not path.is_absolute():
        path = repo_root() / path
    return path


def _find_local_scene_image(scene_id: str, source_dir: Path | None) -> Path | None:
    if not source_dir or not source_dir.exists():
        return None
    for suffix in SUPPORTED_IMAGE_SUFFIXES:
        candidate = source_dir / f"{scene_id}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _find_asset_refs_primary(scene: dict[str, Any], job_dir: Path) -> Path | None:
    """Return the scene's ``asset_refs.primary`` image if it exists.

    Lets the orchestrator (or external image generator) inject a
    job-local image — e.g. ChatGPT-generated artwork at
    ``jobs/<id>/assets/scene-NN.png`` — and have it win over the
    channel's stock-image directory.
    """
    refs = scene.get("asset_refs") or {}
    primary = refs.get("primary")
    if not isinstance(primary, str) or not primary:
        return None
    # Always interpret as job-relative — the field is operator-controlled
    # (model output / external image generator), so an absolute path or
    # ``..`` segment must not escape the job dir and end up mirrored under
    # ``remotion/public/jobs/<id>/assets`` (a publicly-served directory).
    if Path(primary).is_absolute() or ".." in Path(primary).parts:
        return None
    candidate = (job_dir / primary).resolve()
    try:
        candidate.relative_to(job_dir.resolve())
    except ValueError:
        return None
    return candidate if candidate.exists() else None


def _background_source_label(scene_asset: dict[str, Any]) -> str:
    """Human label for where a scene's background came from (UI report)."""
    source = str(scene_asset.get("source") or "")
    provider = str(scene_asset.get("provider") or "").lower()
    tier = str(scene_asset.get("asset_tier") or "").lower()
    # graphic_fallback is a degraded "no real asset matched" tier — flag it
    # distinctly BEFORE the generic placeholder check so the report shows WHY a
    # scene has no real footage/image (was mislabeled "Placeholder").
    if provider == "graphic_fallback" or tier == "graphic_fallback":
        return "Graphic fallback"
    if source == "generated_placeholder":
        return "Placeholder"
    if provider == "ai_generated" or tier in {"ai_image", "ai_generated"}:
        if str(scene_asset.get("generated_image_source_layout") or "").startswith("graphic_"):
            return "ChatGPT infographic"
        return "ChatGPT lifestyle image"
    if "video" in tier:  # pexels_video
        return "Pexels video"
    if "pexels" in provider or "pexels" in tier:
        return "Pexels photo"
    if "pixabay" in provider:
        return "Pixabay photo"
    if source in {"asset_refs_primary", "local_directory"}:
        return "Local asset"
    return provider.title() or "Unknown"


def _write_background_report(
    json_dir: Path,
    scene_assets: list[dict[str, Any]],
    scene_doc: dict[str, Any],
    vision_rejections: list[dict[str, Any]] | None = None,
    merge: bool = False,
) -> None:
    """Per-scene background sourcing report consumed by the Shorts Studio UI.

    When ``merge`` is set, only the scenes in ``scene_assets`` are rewritten and
    the rest of the existing report is preserved (lazy re-gen pass).
    """
    motion_by_id = {s.get("id"): s.get("motion") for s in (scene_doc.get("scenes") or [])}
    entries = []
    for a in scene_assets:
        sid = a.get("scene_id")
        sel = a.get("asset_selection") or {}
        entries.append({
            "scene_id": sid,
            "background_source": a.get("background_source") or _background_source_label(a),
            "media_kind": a.get("media_kind") or "video",
            "provider": a.get("provider"),
            "asset_tier": a.get("asset_tier"),
            "query": sel.get("query"),
            "attribution": a.get("attribution"),
            "source_url": a.get("source_url"),
            "public_background": a.get("public_background"),
            "motion": motion_by_id.get(sid),
        })
    json_dir.mkdir(parents=True, exist_ok=True)
    if merge:
        try:
            from video_agent.utils.json_io import read_json as _rjr
            prev = (_rjr(json_dir / "background_report.json") or {}).get("scenes") or []
        except Exception:
            prev = []
        fresh_ids = {e["scene_id"] for e in entries}
        by_id = {e["scene_id"]: e for e in prev if e.get("scene_id") not in fresh_ids}
        for e in entries:
            by_id[e["scene_id"]] = e
        order = [s.get("id") for s in (scene_doc.get("scenes") or [])]
        entries = [by_id[i] for i in order if i in by_id] or list(by_id.values())
    report: dict[str, Any] = {"scenes": entries}
    if vision_rejections:
        report["vision_rejections"] = vision_rejections
    write_json(json_dir / "background_report.json", report)
