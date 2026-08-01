from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from video_agent.localized_v2.job_state import JobInput

from .dashboard_support import DASHBOARD_BASE_URL, make_dashboard


def _client(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    context = make_dashboard(tmp_path)
    client = TestClient(context.app, base_url=DASHBOARD_BASE_URL)
    session = client.get("/api/v2/session").json()
    return client, {
        "Origin": DASHBOARD_BASE_URL,
        "X-CSRF-Token": session["csrfToken"],
    }


def test_health_and_channels_report_only_v2_readiness(tmp_path: Path) -> None:
    client, _headers = _client(tmp_path)

    health = client.get("/api/v2/health")
    channels = client.get("/api/v2/channels")

    assert health.status_code == 200
    assert health.json() == {
        "service": "READY",
        "queue": "READY",
        "worker": "OFFLINE",
    }
    assert channels.json() == {
        "data": [
            {
                "channelId": "healthy-life-en",
                "locale": "en-US",
                "name": "Healthy Life 45+",
                "mode": "production",
            }
        ]
    }


def test_create_job_without_worker_stays_queued(tmp_path: Path) -> None:
    client, headers = _client(tmp_path)

    response = client.post(
        "/api/v2/jobs",
        headers=headers,
        json={"channelId": "healthy-life-en", "topic": "A calm daily habit"},
    )

    assert response.status_code == 201
    snapshot = response.json()
    assert snapshot["status"] == "QUEUED"
    assert snapshot["locale"] == "en-US"
    assert snapshot["input"]["topic"] == "A calm daily habit"


def test_paginated_job_list_is_newest_first_with_stable_tie_break(
    tmp_path: Path,
) -> None:
    context = make_dashboard(tmp_path)
    for job_id in ("job-a", "job-c", "job-b"):
        context.queue.create_job(
            JobInput(
                job_id=job_id,
                channel_id="healthy-life-en",
                locale="en-US",
                topic=job_id,
                channel_snapshot={"channelId": "healthy-life-en"},
                locale_snapshot={"locale": "en-US"},
            )
        )
    with context.queue._connect() as connection:
        connection.execute("UPDATE jobs SET created_at = '2026-01-01T00:00:00+00:00'")
    client = TestClient(context.app, base_url=DASHBOARD_BASE_URL)

    first_page = client.get("/api/v2/jobs?page=1&pageSize=2").json()
    second_page = client.get("/api/v2/jobs?page=2&pageSize=2").json()

    assert [job["jobId"] for job in first_page["data"]] == ["job-c", "job-b"]
    assert [job["jobId"] for job in second_page["data"]] == ["job-a"]
    assert first_page["pagination"] == {
        "page": 1,
        "pageSize": 2,
        "totalItems": 3,
        "totalPages": 2,
    }


def test_detail_events_and_state_survive_app_restart(tmp_path: Path) -> None:
    context = make_dashboard(tmp_path)
    client = TestClient(context.app, base_url=DASHBOARD_BASE_URL)
    csrf = client.get("/api/v2/session").json()["csrfToken"]
    created = client.post(
        "/api/v2/jobs",
        headers={"Origin": DASHBOARD_BASE_URL, "X-CSRF-Token": csrf},
        json={"channelId": "healthy-life-en", "topic": "Persistent state"},
    ).json()
    restarted = make_dashboard(tmp_path)
    second_client = TestClient(restarted.app, base_url=DASHBOARD_BASE_URL)

    detail = second_client.get(f"/api/v2/jobs/{created['jobId']}")
    events = second_client.get(f"/api/v2/jobs/{created['jobId']}/events")

    assert detail.status_code == 200
    assert detail.json()["topic"] == "Persistent state"
    assert [event["type"] for event in events.json()["data"]] == ["JOB_CREATED"]


def test_invalid_state_actions_share_error_envelope(tmp_path: Path) -> None:
    client, headers = _client(tmp_path)
    created = client.post(
        "/api/v2/jobs",
        headers=headers,
        json={"channelId": "healthy-life-en", "topic": "Queued job"},
    ).json()

    retry = client.post(
        f"/api/v2/jobs/{created['jobId']}/retry-attempts", headers=headers
    )
    resume = client.post(
        f"/api/v2/jobs/{created['jobId']}/resume-attempts", headers=headers
    )
    cancel = client.post(
        f"/api/v2/jobs/{created['jobId']}/cancellation", headers=headers
    )
    cancel_again = client.post(
        f"/api/v2/jobs/{created['jobId']}/cancellation", headers=headers
    )

    assert retry.status_code == 409
    assert resume.status_code == 409
    assert retry.json()["error"]["code"] == "INVALID_JOB_STATE"
    assert resume.json()["error"]["code"] == "INVALID_JOB_STATE"
    assert cancel.status_code == 200
    assert cancel_again.status_code == 200
    assert cancel_again.json()["status"] == "CANCELLED"


def test_unknown_status_filter_is_rejected(tmp_path: Path) -> None:
    client, _headers = _client(tmp_path)

    response = client.get("/api/v2/jobs?status=not-a-real-state")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_channel_preserves_not_enabled_error_contract(tmp_path: Path) -> None:
    client, headers = _client(tmp_path)

    response = client.post(
        "/api/v2/jobs",
        headers=headers,
        json={"channelId": "unknown-channel", "topic": "A calm daily habit"},
    )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "CHANNEL_NOT_ENABLED",
        "message": (
            "The selected localized V2 channel is not available for "
            "production or qualification."
        ),
        "details": {"channelId": "unknown-channel"},
    }
