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
