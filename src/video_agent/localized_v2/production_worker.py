from __future__ import annotations

import fcntl
import logging
import os
import signal
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from video_agent.localized_v2.assets import LocalizedAssetPipeline
from video_agent.localized_v2.audio.capabilities import VoiceCapabilityRegistry
from video_agent.localized_v2.audio.production import KokoroBackend
from video_agent.localized_v2.audio.tts import LocalizedTTS
from video_agent.localized_v2.brand_assets import BrandClip, probe_brand_clip
from video_agent.localized_v2.dashboard.bootstrap import load_dashboard_channels
from video_agent.localized_v2.dashboard.service import EnabledChannel
from video_agent.localized_v2.orchestrator import (
    LocalizedMediaOrchestrator,
    LocalizedOrchestrator,
    LocalizedPromptRunner,
)
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.production_assets import (
    BrowserImageProvider,
    StockVideoProvider,
    load_stock_provider_credentials,
)
from video_agent.localized_v2.providers import (
    BrowserProviderConfig,
    BrowserStructuredProvider,
)
from video_agent.localized_v2.queue import LocalizedQueue
from video_agent.localized_v2.render import RemotionRenderer
from video_agent.localized_v2.runtime import load_runtime_settings
from video_agent.localized_v2.worker import LocalizedWorker

LOG = logging.getLogger("localized-v2-worker")
T = TypeVar("T")


def english_registration(
    channels: dict[str, T],
    *,
    locale_of: Callable[[T], str] = lambda item: str(item.channel["locale"]),
) -> T:
    matches = [item for item in channels.values() if locale_of(item) == "en-US"]
    if len(matches) != 1:
        raise RuntimeError("localized V2 worker requires exactly one enabled en-US channel")
    return matches[0]


def resolve_brand_clips(
    channel: dict[str, Any],
    media_root: Path,
    *,
    probe: Callable[[Path, Path], BrandClip] = probe_brand_clip,
) -> dict[str, BrandClip]:
    root = media_root.resolve()
    clips: dict[str, BrandClip] = {}
    for name, field in (
        ("intro", "introClip"),
        ("disclaimer", "disclaimerClip"),
        ("outro", "outroClip"),
    ):
        relative = Path(str(channel["brand"][field]))
        candidate = (root / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or not candidate.is_relative_to(root):
            raise ValueError("localized V2 brand clip escaped the media root")
        clips[name] = probe(candidate, root)
    return clips


def build_production_worker(
    *,
    repo_root: Path,
    paths: RuntimePaths,
    queue: LocalizedQueue,
    settings,
    registration: EnabledChannel,
    worker_id: str,
) -> LocalizedWorker:
    namespace = "localized-v2:en-us"
    browser = BrowserProviderConfig(
        endpoint=settings.browser_worker_url,
        profile_root=paths.browser_profile,
        session_namespace=namespace,
    )
    text_provider = BrowserStructuredProvider(
        browser,
        runtime_paths=paths,
        expected_endpoint=settings.browser_worker_url,
        legacy_endpoints=frozenset({"http://127.0.0.1:8001"}),
    )
    image_provider = BrowserImageProvider(
        browser,
        runtime_paths=paths,
        expected_endpoint=settings.browser_worker_url,
    )
    visual_provider = StockVideoProvider.build(
        paths,
        image_provider,
        channel_id=str(registration.channel["channelId"]),
    )
    assets = LocalizedAssetPipeline(
        visual_provider,
        runtime_paths=paths,
        browser_worker_url=settings.browser_worker_url,
        legacy_browser_endpoints=frozenset({"http://127.0.0.1:8001"}),
    )
    prompt_runner = LocalizedPromptRunner(paths, repo_root / "schemas", text_provider)
    content = LocalizedOrchestrator(
        prompt_runner,
        LocalizedTTS(
            {("kokoro", "a"): KokoroBackend()},
            VoiceCapabilityRegistry(registration.inventory.voices),
        ),
    )
    runner = LocalizedMediaOrchestrator(
        content,
        assets,
        resolve_brand_clips(registration.channel, paths.root / "media"),
        queue,
        renderer=RemotionRenderer(repo_root / "remotion", repo_root / "schemas"),
    )
    return LocalizedWorker(
        worker_id,
        paths,
        queue,
        runner,
        lease_seconds=settings.lease_seconds,
    )


@contextmanager
def worker_process_lock(paths: RuntimePaths) -> Iterator[None]:
    lock_path = paths.process_state / "worker.lock"
    pid_path = paths.process_state / "worker.pid"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("localized V2 worker is already running") from exc
        pid_path.write_text(f"{os.getpid()}\n", encoding="ascii")
        try:
            yield
        finally:
            pid_path.unlink(missing_ok=True)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_forever(worker: LocalizedWorker, *, poll_seconds: float = 1.0) -> None:
    stopping = False

    def stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopping:
        try:
            worked = worker.run_once()
        except Exception:
            LOG.exception("localized V2 worker attempt failed")
            worked = True
        if not worked:
            time.sleep(max(0.1, poll_seconds))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    repo_root = Path.cwd().resolve()
    settings = load_runtime_settings(
        repo_root / "configs" / "localized-v2" / "runtime.yaml",
        repo_root=repo_root,
    )
    paths = RuntimePaths.build(settings.root, legacy_jobs_root=repo_root / "jobs")
    paths.initialize()
    provider_env = os.environ.get("LOCALIZED_V2_PROVIDER_ENV")
    if provider_env:
        load_stock_provider_credentials(Path(provider_env))
    elif not (os.environ.get("PEXELS_API_KEY") or os.environ.get("PIXABAY_API_KEY")):
        raise RuntimeError("localized V2 stock provider credentials are not configured")
    queue = LocalizedQueue(paths.queue_db, busy_timeout_ms=settings.busy_timeout_ms)
    channels = load_dashboard_channels(
        channel_root=repo_root / "configs" / "localized-v2" / "channels",
        locale_root=repo_root / "configs" / "localized-v2" / "locales",
        schema_root=repo_root / "schemas",
        settings=settings,
    )
    registration = english_registration(channels)
    worker_id = f"localized-v2-en-{os.getpid()}"
    worker = build_production_worker(
        repo_root=repo_root,
        paths=paths,
        queue=queue,
        settings=settings,
        registration=registration,
        worker_id=worker_id,
    )
    with worker_process_lock(paths):
        run_forever(worker)


if __name__ == "__main__":
    main()
