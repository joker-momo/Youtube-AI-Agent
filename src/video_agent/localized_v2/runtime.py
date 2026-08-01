from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from video_agent.localized_v2.contracts import CapabilityFailure, PreflightResult
from video_agent.localized_v2.job_state import (
    JobInput,
    create_job_snapshot,
    remove_job_snapshot,
)
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.queue import LocalizedQueue

SECRET_KEY_PARTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
)


class PreflightRejected(ValueError):
    def __init__(self, failures: tuple[CapabilityFailure, ...]):
        super().__init__("localized V2 capability preflight failed")
        self.failures = failures


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    root: Path
    host: str
    port: int
    browser_worker_url: str
    browser_cdp_url: str
    busy_timeout_ms: int
    lease_seconds: int


def load_runtime_settings(path: Path, *, repo_root: Path) -> RuntimeSettings:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("localized V2 runtime config must be an object")
    allowed = {
        "schemaVersion",
        "root",
        "host",
        "port",
        "browserWorkerUrl",
        "browserCdpUrl",
        "busyTimeoutMs",
        "leaseSeconds",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"unknown localized V2 runtime fields: {sorted(unknown)}")
    if payload.get("schemaVersion") != "localized-runtime-v2/v1":
        raise ValueError("unsupported localized V2 runtime schemaVersion")
    if payload.get("host") not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("localized V2 dashboard must bind to loopback")
    browser_worker = urlsplit(str(payload.get("browserWorkerUrl", "")))
    if (
        browser_worker.scheme != "http"
        or browser_worker.hostname not in {"127.0.0.1", "::1", "localhost"}
        or browser_worker.username
        or browser_worker.password
        or browser_worker.query
        or browser_worker.fragment
        or browser_worker.path not in {"", "/"}
        or browser_worker.port is None
        or browser_worker.port == int(payload["port"])
    ):
        raise ValueError(
            "localized V2 browser worker must use a separate loopback endpoint"
        )
    browser_cdp = urlsplit(str(payload.get("browserCdpUrl", "")))
    reserved_ports = {int(payload["port"]), browser_worker.port, 9222}
    if (
        browser_cdp.scheme != "http"
        or browser_cdp.hostname not in {"127.0.0.1", "::1", "localhost"}
        or browser_cdp.username
        or browser_cdp.password
        or browser_cdp.query
        or browser_cdp.fragment
        or browser_cdp.path not in {"", "/"}
        or browser_cdp.port is None
        or browser_cdp.port in reserved_ports
    ):
        raise ValueError(
            "localized V2 browser CDP must use a separate V2 loopback endpoint"
        )
    root = Path(str(payload["root"])).expanduser()
    if not root.is_absolute():
        root = repo_root / root
    return RuntimeSettings(
        root=root.resolve(),
        host=str(payload["host"]),
        port=int(payload["port"]),
        browser_worker_url=f"http://{browser_worker.hostname}:{browser_worker.port}",
        browser_cdp_url=f"http://{browser_cdp.hostname}:{browser_cdp.port}",
        busy_timeout_ms=int(payload["busyTimeoutMs"]),
        lease_seconds=int(payload["leaseSeconds"]),
    )


def _assert_no_secrets(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in SECRET_KEY_PARTS):
                raise ValueError(f"secret-like field cannot be persisted: {path}.{key}")
            _assert_no_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_secrets(child, f"{path}[{index}]")


class LocalizedRuntime:
    def __init__(self, paths: RuntimePaths, queue: LocalizedQueue):
        self.paths = paths
        self.queue = queue

    def submit(
        self,
        job_input: JobInput,
        preflight: PreflightResult,
    ) -> dict[str, Any]:
        if not preflight.ok:
            raise PreflightRejected(preflight.failures)
        _assert_no_secrets(job_input.to_dict())
        create_job_snapshot(self.paths, job_input)
        try:
            self.queue.create_job(job_input)
        except BaseException:
            remove_job_snapshot(self.paths, job_input.job_id)
            raise
        snapshot = self.queue.get_job(job_input.job_id)
        if snapshot is None:
            remove_job_snapshot(self.paths, job_input.job_id)
            raise RuntimeError("localized V2 queue did not persist the job")
        return snapshot
