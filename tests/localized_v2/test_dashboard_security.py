from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_agent.localized_v2.dashboard.__main__ import _authority
from video_agent.localized_v2.job_state import JobInput, PromotedArtifact

from .dashboard_support import DASHBOARD_BASE_URL, make_dashboard


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "127.0.0.1:8765"),
        ("localhost", "localhost:8765"),
        ("::1", "[::1]:8765"),
    ],
)
def test_loopback_authority_formatting(host: str, expected: str) -> None:
    assert _authority(host, 8765) == expected


def _csrf(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v2/session").json()["csrfToken"]
    return {"Origin": DASHBOARD_BASE_URL, "X-CSRF-Token": token}


def test_rejects_unexpected_host_foreign_origin_and_missing_csrf(tmp_path: Path) -> None:
    context = make_dashboard(tmp_path)
    client = TestClient(context.app, base_url=DASHBOARD_BASE_URL)
    body = {"channelId": "healthy-life-en", "topic": "Safe topic"}

    bad_host = client.get("/api/v2/health", headers={"Host": "evil.example"})
    missing = client.post("/api/v2/jobs", json=body)
    foreign = client.post(
        "/api/v2/jobs",
        headers={"Origin": "https://evil.example", "X-CSRF-Token": "invalid"},
        json=body,
    )

    assert bad_host.status_code == 400
    assert missing.status_code == 403
    assert foreign.status_code == 403
    assert bad_host.json()["error"]["code"] == "INVALID_HOST"
    assert missing.json()["error"]["code"] == "CSRF_REJECTED"
    assert "access-control-allow-origin" not in foreign.headers
    assert bad_host.headers["x-content-type-options"] == "nosniff"
    assert missing.headers["x-frame-options"] == "DENY"


def test_security_headers_and_no_permissive_cors(tmp_path: Path) -> None:
    client = TestClient(
        make_dashboard(tmp_path).app,
        base_url=DASHBOARD_BASE_URL,
    )

    response = client.get("/", headers={"Origin": "https://evil.example"})

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "access-control-allow-origin" not in response.headers


def test_hostile_text_is_data_and_input_is_bounded(tmp_path: Path) -> None:
    client = TestClient(
        make_dashboard(tmp_path).app,
        base_url=DASHBOARD_BASE_URL,
    )
    headers = _csrf(client)
    hostile = '<img src=x onerror="document.body.dataset.pwned=1">'

    created = client.post(
        "/api/v2/jobs",
        headers=headers,
        json={"channelId": "healthy-life-en", "topic": hostile},
    )
    oversized = client.post(
        "/api/v2/jobs",
        headers=headers,
        json={"channelId": "healthy-life-en", "topic": "x" * 241},
    )
    whitespace = client.post(
        "/api/v2/jobs",
        headers=headers,
        json={"channelId": "healthy-life-en", "topic": "   "},
    )

    assert created.status_code == 201
    assert created.json()["topic"] == hostile
    assert oversized.status_code == 422
    assert oversized.json()["error"]["code"] == "VALIDATION_ERROR"
    assert whitespace.status_code == 422
    assert whitespace.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("bind_host", ["0.0.0.0", "192.168.1.10", "example.com"])
def test_non_loopback_binding_is_rejected(tmp_path: Path, bind_host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        make_dashboard(tmp_path, bind_host=bind_host)


@pytest.mark.parametrize(
    ("allowed_hosts", "allowed_origins"),
    [
        ({"dashboard.example"}, {DASHBOARD_BASE_URL}),
        ({"127.0.0.1"}, {"https://dashboard.example"}),
        ({"127.0.0.1"}, {"http://127.0.0.1/path"}),
    ],
)
def test_nonlocal_security_allowlists_are_rejected(
    tmp_path: Path,
    allowed_hosts: set[str],
    allowed_origins: set[str],
) -> None:
    with pytest.raises(ValueError, match="local"):
        make_dashboard(
            tmp_path,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        )


def test_artifact_access_rejects_symlink_escape_and_unknown_type(tmp_path: Path) -> None:
    context = make_dashboard(tmp_path)
    context.queue.create_job(
        JobInput(
            job_id="job-a",
            channel_id="healthy-life-en",
            locale="en-US",
            topic="Artifacts",
            channel_snapshot={"channelId": "healthy-life-en"},
            locale_snapshot={"locale": "en-US"},
        )
    )
    lease = context.queue.claim_next("worker-a", lease_seconds=30)
    assert lease is not None
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret":"outside"}', encoding="utf-8")
    symlink = context.paths.jobs / "job-a" / "escape.json"
    symlink.parent.mkdir(parents=True, exist_ok=True)
    symlink.symlink_to(outside)
    executable = context.paths.jobs / "job-a" / "payload.exe"
    executable.write_bytes(b"MZ")
    context.queue.register_artifacts(
        lease.attempt_id,
        "worker-a",
        "script",
        (
            PromotedArtifact(
                name="escape.json",
                path=symlink,
                sha256=hashlib.sha256(outside.read_bytes()).hexdigest(),
            ),
            PromotedArtifact(
                name="payload.exe",
                path=executable,
                sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            ),
        ),
    )
    client = TestClient(context.app, base_url=DASHBOARD_BASE_URL)

    escaped = client.get("/api/v2/jobs/job-a/artifacts/escape.json")
    unknown = client.get("/api/v2/jobs/job-a/artifacts/payload.exe")

    assert escaped.status_code == 403
    assert unknown.status_code == 403
    assert escaped.json()["error"]["code"] == "ARTIFACT_NOT_ALLOWED"
    assert unknown.json()["error"]["code"] == "ARTIFACT_NOT_ALLOWED"


def test_artifact_download_requires_promoted_hash_integrity(tmp_path: Path) -> None:
    context = make_dashboard(tmp_path)
    context.queue.create_job(
        JobInput(
            job_id="job-integrity",
            channel_id="healthy-life-en",
            locale="en-US",
            topic="Artifact integrity",
            channel_snapshot={"channelId": "healthy-life-en"},
            locale_snapshot={"locale": "en-US"},
        )
    )
    lease = context.queue.claim_next("worker-a", lease_seconds=30)
    assert lease is not None
    artifact = context.paths.jobs / "job-integrity" / "artifacts" / "script" / "script.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b'{"ok":true}')
    context.queue.register_artifacts(
        lease.attempt_id,
        "worker-a",
        "script",
        (
            PromotedArtifact(
                name="script.json",
                path=artifact,
                sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            ),
        ),
    )
    client = TestClient(context.app, base_url=DASHBOARD_BASE_URL)

    valid = client.get("/api/v2/jobs/job-integrity/artifacts/script.json")
    artifact.write_bytes(b'{"tampered":true}')
    tampered = client.get("/api/v2/jobs/job-integrity/artifacts/script.json")

    assert valid.status_code == 200
    assert valid.content == b'{"ok":true}'
    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "ARTIFACT_INTEGRITY_FAILED"


def test_invalid_job_id_and_internal_errors_are_redacted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = make_dashboard(tmp_path)
    client = TestClient(
        context.app,
        base_url=DASHBOARD_BASE_URL,
        raise_server_exceptions=False,
    )
    invalid = client.get("/api/v2/jobs/not%20valid")

    def explode(*_args, **_kwargs):
        raise RuntimeError("/private/path apiToken=top-secret")

    monkeypatch.setattr(context.service, "list_jobs", explode)
    internal = client.get("/api/v2/jobs")

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
    assert internal.status_code == 500
    assert internal.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "The localized V2 service could not complete the request.",
        }
    }
    assert "top-secret" not in internal.text
