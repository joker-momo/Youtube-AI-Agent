from __future__ import annotations

from pathlib import Path

import pytest

from video_agent.localized_v2.assets import AssetResponse
from video_agent.localized_v2.audio.production import KokoroBackend
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.production_assets import (
    BrowserImageProvider,
    StockVideoProvider,
    load_stock_provider_credentials,
)
from video_agent.localized_v2.providers import BrowserProviderConfig

MP4 = b"\x00\x00\x00\x18ftypisom000000000000"
PNG = b"\x89PNG\r\n\x1a\nlocalized-v2-image"


class FakeKokoroClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path, dict]] = []

    def synthesize(self, text: str, output_path: Path, config: dict) -> dict:
        self.calls.append((text, output_path, config))
        output_path.write_bytes(b"wav")
        return {"provider": "kokoro"}


class FakeStockService:
    def __init__(self, asset: dict | None) -> None:
        self.asset = asset
        self.calls: list[tuple[dict, str, str]] = []

    def get_scene_asset(self, scene: dict, channel_id: str, job_id: str):
        self.calls.append((scene, channel_id, job_id))
        return self.asset


class SequencedStockService(FakeStockService):
    def __init__(self, assets: list[dict | None]) -> None:
        super().__init__(None)
        self.assets = iter(assets)

    def get_scene_asset(self, scene: dict, channel_id: str, job_id: str):
        self.calls.append((scene, channel_id, job_id))
        return next(self.assets)


class FakeImageProvider:
    transport = "direct"
    browser_config = None

    def graphic(self, scene: dict, context: dict) -> AssetResponse:
        return AssetResponse(200, "image/png", PNG, "https://images.example/graphic")

    def thumbnail(self, seo: dict, context: dict) -> AssetResponse:
        return AssetResponse(200, "image/png", PNG, "https://images.example/thumbnail")


def test_kokoro_backend_maps_exact_v2_voice_contract(tmp_path: Path) -> None:
    client = FakeKokoroClient()
    backend = KokoroBackend(client=client, sample_rate=24_000)
    output = tmp_path / "scene.wav"

    backend.synthesize(
        "A calm opening.",
        language="a",
        voice_id="af_heart",
        speed=0.96,
        output_path=output,
    )

    assert output.read_bytes() == b"wav"
    assert client.calls == [
        (
            "A calm opening.",
            output,
            {
                "provider": "kokoro",
                "lang_code": "a",
                "voice_id": "af_heart",
                "speed": 0.96,
                "sample_rate": 24_000,
            },
        )
    ]


def test_stock_video_provider_uses_search_brief_and_returns_real_video(
    tmp_path: Path,
) -> None:
    local = tmp_path / "stock.mp4"
    local.write_bytes(MP4)
    service = FakeStockService(
        {
            "local_path": str(local),
            "media_type": "video",
            "original_url": "https://www.pexels.com/video/123/",
            "provider": "pexels",
            "provider_asset_id": "123",
        }
    )
    provider = StockVideoProvider(
        service,
        FakeImageProvider(),
        channel_id="healthy-aging-en-us",
        job_id="job-123",
    )
    scene = {
        "id": "opening",
        "visualType": "video",
        "visualPrompt": "An older adult walking in a sunny park",
        "searchBrief": {"language": "en", "queries": ["older adult walking park"]},
    }

    response = provider.background(scene, {"locale": "en-US"})

    assert response.body == MP4
    assert response.content_type == "video/mp4"
    assert response.source_url == "https://www.pexels.com/video/123/"
    sent, channel_id, job_id = service.calls[0]
    assert sent["visual_prompt"] == "older adult walking park"
    assert sent["asset_strategy"] == "stock_ok"
    assert channel_id == "healthy-aging-en-us"
    assert job_id == "job-123"


def test_stock_video_provider_tries_search_brief_queries_in_order(
    tmp_path: Path,
) -> None:
    local = tmp_path / "stock.mp4"
    local.write_bytes(MP4)
    service = SequencedStockService(
        [
            None,
            {
                "local_path": str(local),
                "media_type": "video",
                "original_url": "https://pixabay.com/videos/456/",
                "provider": "pixabay",
                "provider_asset_id": "456",
            },
        ]
    )
    provider = StockVideoProvider(service, FakeImageProvider())

    response = provider.background(
        {
            "id": "sodium",
            "visualType": "video",
            "visualPrompt": "An adult choosing a lower-sodium breakfast",
            "searchBrief": {
                "language": "en",
                "queries": [
                    "reading nutrition facts label",
                    "grocery shopping breakfast aisle",
                    "homemade healthy breakfast",
                ],
            },
        },
        {"channelId": "channel", "jobId": "job"},
    )

    assert response.body == MP4
    assert [call[0]["visual_prompt"] for call in service.calls] == [
        "reading nutrition facts label",
        "grocery shopping breakfast aisle",
    ]


@pytest.mark.parametrize(
    "asset",
    [
        None,
        {"local_path": "/missing.mp4", "media_type": "video", "original_url": "https://example.com/x"},
        {"local_path": "/tmp/photo.jpg", "media_type": "photo", "original_url": "https://example.com/x"},
    ],
)
def test_stock_video_provider_fails_closed_without_real_video(asset: dict | None) -> None:
    provider = StockVideoProvider(
        FakeStockService(asset),
        FakeImageProvider(),
        channel_id="healthy-aging-en-us",
        job_id="job-123",
    )

    with pytest.raises(RuntimeError, match="stock video"):
        provider.background(
            {
                "id": "opening",
                "visualType": "video",
                "visualPrompt": "Older adult walking",
                "searchBrief": {"language": "en", "queries": ["older adult walking"]},
            },
            {"locale": "en-US"},
        )


def test_stock_video_provider_delegates_graphics_and_thumbnail() -> None:
    provider = StockVideoProvider(
        FakeStockService(None),
        FakeImageProvider(),
        channel_id="healthy-aging-en-us",
        job_id="job-123",
    )

    assert provider.graphic({"id": "g"}, {}).source_url.endswith("/graphic")
    assert provider.thumbnail({"title": "Title"}, {}).source_url.endswith("/thumbnail")


class FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_browser_image_provider_verifies_identity_and_reads_only_v2_output(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    paths = RuntimePaths.build(tmp_path / "runtime-v2", legacy_jobs_root=legacy)
    paths.initialize()
    endpoint = "http://127.0.0.1:8793"
    config = BrowserProviderConfig(
        endpoint=endpoint,
        profile_root=paths.browser_profile,
        session_namespace="localized-v2:en-us",
    )
    posts: list[dict] = []
    post_timeouts: list[float] = []

    def get(_url: str, **_kwargs) -> FakeResponse:
        return FakeResponse(
            200,
            {
                "service": "localized-v2-browser-worker",
                "sessionNamespace": "localized-v2:en-us",
                "profileRoot": str(paths.browser_profile),
            },
        )

    def post(_url: str, *, json: dict, **_kwargs) -> FakeResponse:
        posts.append(json)
        post_timeouts.append(float(_kwargs["timeout"]))
        output = Path(json["out_path"])
        assert output.is_relative_to(paths.work)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(PNG)
        return FakeResponse(
            200,
            {
                "local_path": str(output),
                "src": "https://chatgpt.com/generated/image",
            },
        )

    provider = BrowserImageProvider(
        config,
        runtime_paths=paths,
        expected_endpoint=endpoint,
        get=get,
        post=post,
    )
    response = provider.graphic(
        {"id": "graphic-1", "visualPrompt": "A clear healthy aging checklist"},
        {"locale": "en-US", "topic": "healthy walking", "avoid": []},
    )
    cached = provider.graphic(
        {"id": "graphic-1", "visualPrompt": "A clear healthy aging checklist"},
        {"locale": "en-US", "topic": "healthy walking", "avoid": []},
    )

    assert response.body == PNG
    assert cached.body == PNG
    assert response.content_type == "image/png"
    assert len(posts) == 1
    assert post_timeouts[0] >= 780.0
    assert posts[0]["aspect_ratio"] == "16:9"
    assert "healthy aging checklist" in posts[0]["prompt"]
    assert not Path(posts[0]["out_path"]).exists()

    cache_file = next((paths.cache / "browser-images").glob("*.bin"))
    cache_file.write_bytes(b"corrupt cached image")
    recovered = provider.graphic(
        {"id": "graphic-1", "visualPrompt": "A clear healthy aging checklist"},
        {"locale": "en-US", "topic": "healthy walking", "avoid": []},
    )
    assert recovered.body == PNG
    assert len(posts) == 2


def test_stock_provider_exposes_browser_isolation_contract(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    paths = RuntimePaths.build(tmp_path / "runtime-v2", legacy_jobs_root=legacy)
    paths.initialize()
    config = BrowserProviderConfig(
        endpoint="http://127.0.0.1:8793",
        profile_root=paths.browser_profile,
        session_namespace="localized-v2:en-us",
    )

    class BrowserImages(FakeImageProvider):
        transport = "browser"
        browser_config = config

    provider = StockVideoProvider(
        FakeStockService(None),
        BrowserImages(),
        channel_id="healthy-aging-en-us",
        job_id="job-123",
    )

    assert provider.transport == "browser"
    assert provider.browser_config == config


def test_stock_credentials_load_only_allowlisted_keys_without_overwriting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / ".env"
    source.write_text(
        "PEXELS_API_KEY=pexels-key\n"
        "PIXABAY_API_KEY='pixabay-key'\n"
        "ADMIN_TOKEN=must-not-load\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PEXELS_API_KEY", "already-set")
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    loaded = load_stock_provider_credentials(source)

    assert loaded == frozenset({"PEXELS_API_KEY", "PIXABAY_API_KEY"})
    assert __import__("os").environ["PEXELS_API_KEY"] == "already-set"
    assert __import__("os").environ["PIXABAY_API_KEY"] == "pixabay-key"
    assert "ADMIN_TOKEN" not in __import__("os").environ


def test_stock_credentials_fail_closed_without_a_video_provider_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / ".env"
    source.write_text("ADMIN_TOKEN=not-a-stock-key\n", encoding="utf-8")
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="stock video credential"):
        load_stock_provider_credentials(source)
