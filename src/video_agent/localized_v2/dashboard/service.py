from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from video_agent.localized_v2.job_state import JobInput
from video_agent.localized_v2.preflight import CapabilityInventory, run_preflight
from video_agent.localized_v2.queue import LocalizedQueue, QueueBusyError
from video_agent.localized_v2.runtime import LocalizedRuntime, PreflightRejected

JOB_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,127}$")
ALLOWED_ARTIFACT_SUFFIXES = frozenset(
    {".json", ".png", ".jpg", ".jpeg", ".webp", ".mp4", ".wav"}
)
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
REDACTED_KEYS = ("authorization", "cookie", "password", "secret", "token", "api_key")
JOB_STATUSES = frozenset(
    {
        "QUEUED",
        "RUNNING",
        "CANCEL_REQUESTED",
        "INTERRUPTED",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }
)
CANARY_CHECKS = frozenset(
    {"audio", "font", "render", "humanReview", "dashboardLifecycle"}
)


class DashboardError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True, slots=True)
class EnabledChannel:
    channel: dict[str, Any]
    locale_pack: dict[str, Any]
    inventory: CapabilityInventory


@dataclass(frozen=True, slots=True)
class ArtifactDownload:
    path: Path
    media_type: str
    filename: str


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "localized-video"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(part in key.lower() for part in REDACTED_KEYS)
                else _redact(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class DashboardService:
    def __init__(
        self,
        runtime: LocalizedRuntime,
        queue: LocalizedQueue,
        channels: dict[str, EnabledChannel],
    ):
        self.runtime = runtime
        self.queue = queue
        for channel_id, registration in channels.items():
            channel = registration.channel
            canary = channel.get("canary", {})
            checks = canary.get("checks", {})
            production_ready = (
                channel.get("enabled") is True
                and canary.get("status") == "APPROVED"
                and set(checks) == CANARY_CHECKS
                and all(result == "PASS" for result in checks.values())
                and len(canary.get("evidence", [])) >= 5
            )
            qualification_ready = (
                channel.get("enabled") is False
                and channel.get("qualification") is True
                and canary.get("status") == "PENDING"
            )
            if (
                channel.get("channelId") != channel_id
                or channel.get("locale") != registration.locale_pack.get("locale")
                or not isinstance(channel.get("voice"), dict)
                or not (production_ready or qualification_ready)
            ):
                raise ValueError(
                    "localized V2 dashboard accepts only production or qualification channels"
                )
        self.channels = dict(channels)

    def health(self) -> dict[str, str]:
        return {
            "service": "READY",
            "queue": "READY",
            "worker": "ONLINE" if self.queue.has_live_worker() else "OFFLINE",
        }

    def list_channels(self) -> list[dict[str, str]]:
        return [
            {
                "channelId": channel_id,
                "locale": registration.channel["locale"],
                "name": registration.channel["brand"]["name"],
                "mode": (
                    "production"
                    if registration.channel["enabled"]
                    else "qualification"
                ),
            }
            for channel_id, registration in sorted(self.channels.items())
        ]

    def create_job(
        self,
        channel_id: str,
        topic: str,
        description: str,
    ) -> dict[str, Any]:
        try:
            registration = self.channels[channel_id]
        except KeyError as exc:
            raise DashboardError(
                422,
                "CHANNEL_NOT_ENABLED",
                "The selected localized V2 channel is not available for production or qualification.",
                details={"channelId": channel_id},
            ) from exc
        topic = topic.strip()
        if not 3 <= len(topic) <= 240:
            raise DashboardError(
                422,
                "VALIDATION_ERROR",
                "Video topic must contain between 3 and 240 non-whitespace characters.",
            )
        description = description.strip()
        if not 10 <= len(description) <= 2000:
            raise DashboardError(
                422,
                "VALIDATION_ERROR",
                "Description must contain between 10 and 2000 non-whitespace characters.",
            )
        preflight = run_preflight(
            registration.channel,
            registration.locale_pack,
            registration.inventory,
        )
        now = datetime.now(UTC)
        job_id = (
            f"{_slug(topic)}-{registration.channel['locale'].lower()}-"
            f"{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        )
        request = JobInput(
            job_id=job_id,
            channel_id=channel_id,
            locale=registration.channel["locale"],
            topic=topic,
            description=description,
            channel_snapshot=registration.channel,
            locale_snapshot=registration.locale_pack,
        )
        try:
            return self.runtime.submit(request, preflight)
        except PreflightRejected as exc:
            raise DashboardError(
                422,
                "PREFLIGHT_FAILED",
                "The localized channel is missing required capabilities.",
                details={"failures": [item.to_dict() for item in exc.failures]},
            ) from exc
        except QueueBusyError as exc:
            raise DashboardError(
                503,
                exc.code,
                "The localized V2 queue is busy. Retry shortly.",
                details={"retryable": exc.retryable},
            ) from exc

    def list_jobs(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None,
    ) -> dict[str, Any]:
        normalized = status.upper() if status else None
        if normalized and normalized not in JOB_STATUSES:
            raise DashboardError(
                422,
                "VALIDATION_ERROR",
                "Unknown localized V2 job status.",
                details={"status": status},
            )
        return self.queue.list_jobs(page=page, page_size=page_size, status=normalized)

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self.queue.get_job(job_id)
        if job is None:
            raise DashboardError(404, "JOB_NOT_FOUND", "Localized V2 job not found.")
        return _redact(job)

    def list_events(self, job_id: str) -> list[dict[str, Any]]:
        self.get_job(job_id)
        return _redact(self.queue.list_events(job_id))

    def list_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        self.get_job(job_id)
        return [
            {
                "stage": item["stage"],
                "name": item["name"],
                "sha256": item["sha256"],
                "promotedAt": item["promoted_at"],
                "downloadUrl": f"/api/v2/jobs/{job_id}/artifacts/{item['name']}",
            }
            for item in self.queue.list_artifacts(job_id)
            if Path(item["name"]).suffix.lower() in ALLOWED_ARTIFACT_SUFFIXES
        ]

    def artifact_download(self, job_id: str, name: str) -> ArtifactDownload:
        self.get_job(job_id)
        if Path(name).name != name or Path(name).suffix.lower() not in ALLOWED_ARTIFACT_SUFFIXES:
            raise DashboardError(
                403,
                "ARTIFACT_NOT_ALLOWED",
                "This artifact is not available through the dashboard.",
            )
        matches = [
            item for item in self.queue.list_artifacts(job_id) if item["name"] == name
        ]
        if len(matches) != 1:
            raise DashboardError(
                404,
                "ARTIFACT_NOT_FOUND",
                "Localized V2 artifact not found.",
            )
        stored = Path(matches[0]["path"])
        allowed_root = (self.runtime.paths.jobs / job_id / "artifacts").resolve()
        resolved = stored.resolve()
        if (
            stored.is_symlink()
            or not resolved.is_relative_to(allowed_root)
            or not resolved.is_file()
            or resolved.stat().st_size > MAX_ARTIFACT_BYTES
        ):
            raise DashboardError(
                403,
                "ARTIFACT_NOT_ALLOWED",
                "This artifact is not available through the dashboard.",
            )
        actual_hash = _sha256(resolved)
        if actual_hash != matches[0]["sha256"]:
            raise DashboardError(
                409,
                "ARTIFACT_INTEGRITY_FAILED",
                "The promoted artifact failed its integrity check.",
            )
        media_type, _encoding = mimetypes.guess_type(resolved.name)
        return ArtifactDownload(
            path=resolved,
            media_type=media_type or "application/octet-stream",
            filename=name,
        )

    def cancel(self, job_id: str) -> dict[str, Any]:
        self.get_job(job_id)
        try:
            self.queue.request_cancel(job_id)
        except ValueError as exc:
            raise DashboardError(
                409,
                "INVALID_JOB_STATE",
                "The job cannot be cancelled from its current state.",
            ) from exc
        return self.get_job(job_id)

    def retry(self, job_id: str) -> dict[str, Any]:
        self.get_job(job_id)
        try:
            self.queue.retry(job_id)
        except ValueError as exc:
            raise DashboardError(
                409,
                "INVALID_JOB_STATE",
                "Only failed jobs can create a retry attempt.",
            ) from exc
        return self.get_job(job_id)

    def resume(self, job_id: str) -> dict[str, Any]:
        self.get_job(job_id)
        try:
            self.queue.resume(job_id)
        except ValueError as exc:
            raise DashboardError(
                409,
                "INVALID_JOB_STATE",
                "Only interrupted jobs can create a resume attempt.",
            ) from exc
        return self.get_job(job_id)
