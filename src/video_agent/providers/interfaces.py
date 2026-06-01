from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class LLMProvider(Protocol):
    async def generate_text(self, messages: list[str], *, site: str = "chatgpt") -> str:
        ...


class ImageProvider(Protocol):
    async def generate_image(
        self,
        prompt: str,
        *,
        output_path: Path | None = None,
    ) -> dict[str, Any]:
        ...


class TTSProvider(Protocol):
    def synthesize(self, text: str, output_path: Path, **kwargs: Any) -> dict[str, Any]:
        ...


class Renderer(Protocol):
    def render(self, job_dir: Path, channel_path: Path, **kwargs: Any) -> Path | None:
        ...
