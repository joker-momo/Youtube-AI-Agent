from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.orchestrator.browser_client import BrowserClient


class BrowserClientLLMProvider:
    def __init__(self, client: BrowserClient) -> None:
        self.client = client

    async def generate_text(self, messages: list[str], *, site: str = "chatgpt") -> str:
        return await self.client.run_session(site, messages)


class BrowserClientImageProvider:
    def __init__(self, client: BrowserClient) -> None:
        self.client = client

    async def generate_image(
        self,
        prompt: str,
        *,
        output_path: Path | None = None,
    ) -> dict[str, Any]:
        out_path = str(output_path) if output_path is not None else "generated.png"
        project_name = output_path.parent.name if output_path is not None else "browser-client-image"
        return await self.client.generate_image(
            prompt,
            project_name=project_name,
            out_path=out_path,
        )
