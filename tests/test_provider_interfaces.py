from __future__ import annotations

import asyncio
from pathlib import Path

from video_agent.providers.browser_client_adapter import (
    BrowserClientImageProvider,
    BrowserClientLLMProvider,
)
from video_agent.providers.interfaces import ImageProvider, LLMProvider, Renderer, TTSProvider


class FakeBrowserClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def run_session(self, site: str, messages: list[str]) -> str:
        self.calls.append(("run_session", site, messages))
        return "text"

    async def generate_image(self, prompt: str, *, project_name: str, out_path: str) -> dict:
        self.calls.append(("generate_image", prompt, project_name, out_path))
        return {"local_path": out_path, "project_name": project_name}


def test_provider_protocols_import_cleanly():
    assert LLMProvider
    assert ImageProvider
    assert TTSProvider
    assert Renderer


def test_browser_client_llm_adapter_delegates_to_run_session():
    client = FakeBrowserClient()

    result = asyncio.run(BrowserClientLLMProvider(client).generate_text(["hello"], site="claude"))

    assert result == "text"
    assert client.calls == [("run_session", "claude", ["hello"])]


def test_browser_client_image_adapter_derives_worker_paths(tmp_path: Path):
    client = FakeBrowserClient()
    output = tmp_path / "thumb.jpg"

    result = asyncio.run(BrowserClientImageProvider(client).generate_image("prompt", output_path=output))

    assert result["local_path"] == str(output)
    assert client.calls == [("generate_image", "prompt", output.parent.name, str(output))]
