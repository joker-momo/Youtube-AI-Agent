from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from video_agent.localized_v2.dashboard.app import create_app
from video_agent.localized_v2.dashboard.service import DashboardService, EnabledChannel
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.preflight import CapabilityInventory
from video_agent.localized_v2.queue import LocalizedQueue
from video_agent.localized_v2.runtime import LocalizedRuntime

DASHBOARD_BASE_URL = "http://127.0.0.1"


@dataclass(slots=True)
class DashboardContext:
    app: FastAPI
    service: DashboardService
    queue: LocalizedQueue
    paths: RuntimePaths


def make_dashboard(
    tmp_path: Path,
    *,
    allowed_hosts: set[str] | None = None,
    allowed_origins: set[str] | None = None,
    bind_host: str = "127.0.0.1",
) -> DashboardContext:
    legacy = tmp_path / "legacy-jobs"
    legacy.mkdir(parents=True, exist_ok=True)
    paths = RuntimePaths.build(tmp_path / "localized-runtime", legacy_jobs_root=legacy)
    media_root = tmp_path / "localized-media"
    brand_root = media_root / "brand"
    brand_root.mkdir(parents=True, exist_ok=True)
    for name in ("intro.mp4", "disclaimer.mp4", "outro.mp4"):
        (brand_root / name).write_bytes(b"fixture")
    channel = {
        "schemaVersion": "localized-channel-v2/v1",
        "channelId": "healthy-life-en",
        "locale": "en-US",
        "brand": {
            "name": "Healthy Life 45+",
            "introClip": "brand/intro.mp4",
            "disclaimerClip": "brand/disclaimer.mp4",
            "outroClip": "brand/outro.mp4",
        },
        "voice": {
            "provider": "kokoro",
            "language": "a",
            "voiceId": "af_heart",
            "speed": 1.0,
        },
        "render": {
            "composition": "LocalizedV2ChannelVideo",
            "concurrency": "auto",
            "subtitles": {"enabled": False},
        },
        "content": {"type": "long_form", "targetDurationSec": 840},
    }
    locale = {
        "locale": "en-US",
        "medicalSafety": {
            "softClaims": ["research suggests"],
            "disclaimer": "Educational information only.",
        },
        "fonts": {"families": ["Inter"], "requiredCodepoints": ["0041"]},
        "textMetrics": {"charsPerWord": 5.0, "expansionRatio": 1.0},
    }
    inventory = CapabilityInventory(
        media_root=media_root,
        voices=frozenset({("kokoro", "a", "af_heart")}),
        fonts=frozenset({"Inter"}),
    )
    queue = LocalizedQueue(paths.queue_db)
    runtime = LocalizedRuntime(paths, queue)
    service = DashboardService(
        runtime,
        queue,
        {
            "healthy-life-en": EnabledChannel(
                channel=channel,
                locale_pack=locale,
                inventory=inventory,
            )
        },
    )
    app = create_app(
        service,
        bind_host=bind_host,
        allowed_hosts=allowed_hosts or {"127.0.0.1"},
        allowed_origins=allowed_origins or {DASHBOARD_BASE_URL},
    )
    return DashboardContext(app=app, service=service, queue=queue, paths=paths)
