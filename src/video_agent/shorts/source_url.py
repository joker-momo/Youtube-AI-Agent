"""Propagate the long-video URL into the manifest and every Short's SEO."""
from __future__ import annotations

import json
from pathlib import Path

from video_agent.shorts import manifest as manifest_mod
from video_agent.shorts import paths
from video_agent.storage.atomic import atomic_write_json


def update_long_video_url(long_job_dir: Path, url: str, channel_config: dict | None = None) -> int:
    """Set the long-video URL in shorts_manifest.json and each short_seo.json
    (title/long_video_url + regenerated pinned comment). Returns # SEO files updated."""
    updated = 0

    # Manifest
    mpath = paths.manifest_path(long_job_dir)
    if mpath.exists():
        data = json.loads(mpath.read_text(encoding="utf-8"))
        data["source_video_url"] = url
        manifest_mod.write_manifest(long_job_dir, data)

    pinned_template = ""
    if channel_config:
        pinned_template = ((channel_config.get("shorts") or {}).get("funnel") or {}).get("pinned_comment_template", "")

    shorts_root = paths.shorts_dir(long_job_dir)
    if shorts_root.exists():
        for child in sorted(shorts_root.iterdir()):
            if not child.is_dir() or not child.name.startswith("short-"):
                continue
            seo_path = child / paths.SHORT_SEO_FILE
            if not seo_path.exists():
                continue
            seo = json.loads(seo_path.read_text(encoding="utf-8"))
            seo["long_video_url"] = url
            if pinned_template:
                seo["pinned_comment"] = pinned_template.replace("{long_video_url}", url)
            elif seo.get("pinned_comment"):
                # keep existing text but append URL if absent
                if url and url not in seo["pinned_comment"]:
                    seo["pinned_comment"] = f"{seo['pinned_comment']}\n{url}".strip()
            atomic_write_json(seo_path, seo)
            updated += 1

    return updated
