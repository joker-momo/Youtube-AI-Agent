from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CreateJobRequest(StrictModel):
    channel_id: str = Field(
        alias="channelId",
        min_length=3,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9-]+$",
    )
    topic: str = Field(min_length=3, max_length=240)


class ErrorBody(StrictModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorEnvelope(StrictModel):
    error: ErrorBody


class SessionResponse(StrictModel):
    csrf_token: str = Field(alias="csrfToken")


class ChannelSummary(StrictModel):
    channel_id: str = Field(alias="channelId")
    locale: str
    name: str
    mode: str = Field(pattern=r"^(production|qualification)$")


class ChannelListResponse(StrictModel):
    data: list[ChannelSummary]


class HealthResponse(StrictModel):
    service: str
    queue: str
    worker: str
