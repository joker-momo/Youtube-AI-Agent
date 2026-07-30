from __future__ import annotations

import json
from pathlib import Path

from video_agent.localized_v2.assets import (
    AssetResponse,
    LocalizedAssetPipeline,
)
from video_agent.localized_v2.audio.capabilities import VoiceCapabilityRegistry
from video_agent.localized_v2.audio.tts import LocalizedTTS
from video_agent.localized_v2.brand_assets import BrandClip
from video_agent.localized_v2.job_state import JobInput
from video_agent.localized_v2.orchestrator import (
    LocalizedMediaOrchestrator,
    LocalizedOrchestrator,
    LocalizedPromptRunner,
)
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.queue import LocalizedQueue
from video_agent.localized_v2.worker import LocalizedWorker

from .audio_fixtures import FakeTTSBackend
from .locale_fixtures import snapshots
from .test_localized_orchestrator import FakeLocalizedProvider

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"
MP4 = b"\x00\x00\x00\x18ftypisom000000000000"
PNG = b"\x89PNG\r\n\x1a\nlocalized-v2-image"


class FakeAssetProvider:
    name = "fake-direct-assets"
    transport = "direct"
    browser_config = None

    def background(self, scene: dict, _context: dict) -> AssetResponse:
        return AssetResponse(
            200,
            "video/mp4",
            MP4,
            f"https://assets.example/{scene['id']}/background",
        )

    def graphic(self, scene: dict, _context: dict) -> AssetResponse:
        return AssetResponse(
            200,
            "image/png",
            PNG,
            f"https://assets.example/{scene['id']}/graphic",
        )

    def thumbnail(self, _seo: dict, _context: dict) -> AssetResponse:
        return AssetResponse(
            200,
            "image/png",
            PNG,
            "https://assets.example/thumbnail",
        )


def test_full_v2_worker_promotes_assets_branding_and_render_props(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy-jobs"
    legacy.mkdir()
    paths = RuntimePaths.build(tmp_path / "runtime-v2", legacy_jobs_root=legacy)
    paths.initialize()
    queue = LocalizedQueue(paths.queue_db)
    channel, locale = snapshots("en-US")
    queue.create_job(
        JobInput(
            job_id="media-job",
            channel_id=channel["channelId"],
            locale="en-US",
            topic="A realistic walking habit",
            channel_snapshot=channel,
            locale_snapshot=locale,
        )
    )
    prompt_runner = LocalizedPromptRunner(
        paths,
        SCHEMA_ROOT,
        FakeLocalizedProvider(),
    )
    content = LocalizedOrchestrator(
        prompt_runner,
        LocalizedTTS(
            {"kokoro": FakeTTSBackend(duration_sec=0.25)},
            VoiceCapabilityRegistry(frozenset({("kokoro", "a", "af_heart")})),
        ),
    )
    brand_source = tmp_path / "brand-source"
    clips: dict[str, BrandClip] = {}
    for name, duration in (("intro", 2.0), ("disclaimer", 4.0), ("outro", 3.0)):
        path = brand_source / f"{name}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(MP4)
        clips[name] = BrandClip(path, duration)
    runner = LocalizedMediaOrchestrator(
        content,
        LocalizedAssetPipeline(FakeAssetProvider()),
        clips,
        queue,
    )
    worker = LocalizedWorker("media-worker", paths, queue, runner)

    assert worker.run_once()

    assert queue.completed_stages("media-job") == (
        "idea",
        "script",
        "scenes",
        "seo",
        "qa",
        "audio",
        "timing",
        "assets",
        "branding",
        "render_props",
    )
    artifacts = queue.list_artifacts("media-job")
    render_path = next(
        Path(item["path"])
        for item in artifacts
        if item["name"] == "render-props.json"
    )
    props = json.loads(render_path.read_text(encoding="utf-8"))
    assert queue.get_job("media-job")["status"] == "COMPLETED"
    assert props["render"]["subtitles"]["enabled"] is False
    assert props["audio"]["music"] is None
    assert props["branding"]["disclaimer_sec"] == 4.0
    assert props["scenes"][0]["asset_refs"]["background"].endswith(
        "opening-background.mp4"
    )
    assert not any(
        forbidden in item["name"]
        for item in artifacts
        for forbidden in ("whisper", "subtitle", "music", "word_segments")
    )
