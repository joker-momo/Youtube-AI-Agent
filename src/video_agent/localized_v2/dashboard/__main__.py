from __future__ import annotations

from pathlib import Path

import uvicorn

from video_agent.localized_v2.dashboard.app import create_app
from video_agent.localized_v2.dashboard.bootstrap import load_enabled_channels
from video_agent.localized_v2.dashboard.service import DashboardService
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.queue import LocalizedQueue
from video_agent.localized_v2.runtime import (
    LocalizedRuntime,
    load_runtime_settings,
)


def _authority(host: str, port: int) -> str:
    rendered_host = f"[{host}]" if ":" in host else host
    return f"{rendered_host}:{port}"


def main() -> None:
    repo_root = Path.cwd().resolve()
    settings = load_runtime_settings(
        repo_root / "configs" / "localized-v2" / "runtime.yaml",
        repo_root=repo_root,
    )
    paths = RuntimePaths.build(settings.root, legacy_jobs_root=repo_root / "jobs")
    paths.initialize()
    queue = LocalizedQueue(paths.queue_db, busy_timeout_ms=settings.busy_timeout_ms)
    channels = load_enabled_channels(
        channel_root=repo_root / "configs" / "localized-v2" / "channels",
        locale_root=repo_root / "configs" / "localized-v2" / "locales",
        schema_root=repo_root / "schemas",
        settings=settings,
    )
    service = DashboardService(LocalizedRuntime(paths, queue), queue, channels)
    host_port = _authority(settings.host, settings.port)
    app = create_app(
        service,
        bind_host=settings.host,
        allowed_hosts={host_port, f"localhost:{settings.port}"},
        allowed_origins={
            f"http://{host_port}",
            f"http://localhost:{settings.port}",
        },
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
