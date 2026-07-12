from fastapi.testclient import TestClient

from video_agent.browser_worker.app import app


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "browser-worker"}


def test_is_auth_cookie_preserves_login_drops_bloat():
    from video_agent.browser_worker.app import _is_auth_cookie

    # ChatGPT: NextAuth session + OpenAI device + Cloudflare clearance preserved.
    assert _is_auth_cookie("chatgpt", "__Secure-next-auth.session-token")
    assert _is_auth_cookie("chatgpt", "__Host-next-auth.csrf-token")
    assert _is_auth_cookie("chatgpt", "oai-did")
    assert _is_auth_cookie("chatgpt", "cf_clearance")
    # Bloat / analytics dropped.
    assert not _is_auth_cookie("chatgpt", "_ga")
    assert not _is_auth_cookie("chatgpt", "intercom-session-xyz")

    # Gemini / Google login cookies preserved (SID family + Secure/Host prefixes).
    for name in ("SID", "HSID", "SSID", "APISID", "SAPISID",
                 "__Secure-1PSID", "__Secure-3PSIDTS", "NID", "LSID"):
        assert _is_auth_cookie("gemini", name), name
    assert not _is_auth_cookie("gemini", "_ga")
    assert not _is_auth_cookie("gemini", "CONSENT")


def test_safe_asset_path_relative_jobs_prefix_does_not_double(monkeypatch, tmp_path):
    """A relative out_path that already starts with 'jobs/' must map into the
    assets root directly — not to '<root>/jobs/…' (which shipped a poster into
    a stray 'jobs/jobs/…' tree on 2026-07-12)."""
    from video_agent.browser_worker import app as worker_app

    root = tmp_path / "jobs"
    root.mkdir()
    monkeypatch.setenv("WORKER_ASSETS_ROOT", str(root))

    resolved = worker_app._safe_asset_path("jobs/my-job/assets/poster.png")
    assert resolved == (root / "my-job/assets/poster.png").resolve()

    # Absolute host paths keep working.
    resolved_abs = worker_app._safe_asset_path(str(root / "my-job/assets/poster.png"))
    assert resolved_abs == (root / "my-job/assets/poster.png").resolve()
