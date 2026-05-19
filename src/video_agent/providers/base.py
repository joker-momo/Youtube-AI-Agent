from __future__ import annotations

from typing import Any, Protocol


class ContentProvider(Protocol):
    def generate_script(self, channel_config: dict[str, Any], idea: dict[str, Any], job_id: str) -> dict[str, Any]:
        ...

    def generate_scenes(
        self,
        channel_config: dict[str, Any],
        idea: dict[str, Any],
        script: dict[str, Any],
        job_id: str,
    ) -> dict[str, Any]:
        ...

    def generate_seo(
        self,
        channel_config: dict[str, Any],
        idea: dict[str, Any],
        thumbnail_path: str,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        ...
