"""CDP attach failures surface as a structured 503, never a generic 500.

Codex bridge 20260709-015254: /chatgpt/image and /chatgpt/image/batch returned
HTTP 500 during ChatGPT image regeneration because `connect_over_cdp` hung for
the full Playwright default (180000ms) and its timeout propagated uncaught. Only
the earlier `_resolve_browser_ws` step was wrapped in a 503. The shared
`_attach_cdp_or_503` helper now bounds the attach and raises a structured 503 for
BOTH the resolve and the connect stages.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from video_agent.browser_worker import app


class _FakeChromium:
    def __init__(self, exc: Exception):
        self._exc = exc
        self.timeout_seen: int | None = None

    async def connect_over_cdp(self, ws_endpoint: str, timeout: int | None = None):
        self.timeout_seen = timeout
        raise self._exc


class _FakePw:
    def __init__(self, exc: Exception):
        self.chromium = _FakeChromium(exc)


def test_connect_timeout_is_bounded_well_below_playwright_default():
    # The 180s default is exactly what caused the 3-minute hang -> 500.
    assert app._CDP_CONNECT_TIMEOUT_MS < 180_000


def test_attach_raises_503_when_resolve_ws_fails(monkeypatch):
    async def _boom(_url: str) -> str:
        raise RuntimeError("runtime container down")

    monkeypatch.setattr(app, "_resolve_browser_ws", _boom)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(app._attach_cdp_or_503(_FakePw(RuntimeError("unused")), "http://127.0.0.1:9222"))
    assert ei.value.status_code == 503
    assert ei.value.detail["stage"] == "resolve_ws"
    assert "runtime container down" in ei.value.detail["error"]


def test_attach_raises_503_and_bounds_timeout_when_connect_times_out(monkeypatch):
    async def _ok(_url: str) -> str:
        return "ws://127.0.0.1:9222/devtools/browser/abc"

    monkeypatch.setattr(app, "_resolve_browser_ws", _ok)
    pw = _FakePw(TimeoutError("BrowserType.connect_over_cdp: Timeout 180000ms exceeded"))

    with pytest.raises(HTTPException) as ei:
        asyncio.run(app._attach_cdp_or_503(pw, "http://127.0.0.1:9222"))
    assert ei.value.status_code == 503
    assert ei.value.detail["stage"] == "connect_over_cdp"
    assert "Timeout" in ei.value.detail["error"]
    # The attach was called with our bounded timeout, not the 180s default.
    assert pw.chromium.timeout_seen == app._CDP_CONNECT_TIMEOUT_MS


def test_attach_returns_browser_on_success(monkeypatch):
    sentinel = object()

    async def _ok(_url: str) -> str:
        return "ws://127.0.0.1:9222/devtools/browser/abc"

    class _GoodChromium:
        async def connect_over_cdp(self, ws_endpoint: str, timeout: int | None = None):
            return sentinel

    class _GoodPw:
        chromium = _GoodChromium()

    monkeypatch.setattr(app, "_resolve_browser_ws", _ok)
    result = asyncio.run(app._attach_cdp_or_503(_GoodPw(), "http://127.0.0.1:9222"))
    assert result is sentinel


# ── 20260709 REOPEN: reliable attach (retry + re-resolve), full route coverage,
# and a smoke command. Codex: "the helper fast-fails selected routes, but the
# reliable CDP/image path ... is not complete." ───────────────────────────────

class _FlakyChromium:
    """Fails the first ``fail_times`` attach attempts, then returns a browser.

    Models the observed 'listener briefly appears then disappears' flap: a stale
    ws endpoint is refused, but a fresh re-resolve + retry succeeds."""
    def __init__(self, fail_times: int, browser):
        self.fail_times = fail_times
        self._browser = browser
        self.calls = 0

    async def connect_over_cdp(self, ws_endpoint: str, timeout: int | None = None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError(f"CDP listener gone (attempt {self.calls}) {ws_endpoint}")
        return self._browser


class _FlakyPw:
    def __init__(self, fail_times: int, browser):
        self.chromium = _FlakyChromium(fail_times, browser)


def test_attach_retries_transient_flap_and_reresolves_ws(monkeypatch):
    monkeypatch.setattr(app, "_CDP_RETRY_BACKOFF_SEC", 0.0)
    resolves = {"n": 0}

    async def _fresh_ws(_url: str) -> str:
        resolves["n"] += 1
        return f"ws://127.0.0.1:9222/devtools/browser/gen-{resolves['n']}"  # a NEW id each time

    monkeypatch.setattr(app, "_resolve_browser_ws", _fresh_ws)
    sentinel = object()
    pw = _FlakyPw(fail_times=2, browser=sentinel)  # flaps twice, third attempt wins

    result = asyncio.run(app._attach_cdp_or_503(pw, "http://127.0.0.1:9222"))
    assert result is sentinel
    assert pw.chromium.calls == 3
    # ws was RE-RESOLVED for every attempt — never reusing a stale endpoint id.
    assert resolves["n"] == 3


def test_attach_gives_up_after_bounded_attempts_with_attempt_count(monkeypatch):
    monkeypatch.setattr(app, "_CDP_RETRY_BACKOFF_SEC", 0.0)

    async def _ok(_url: str) -> str:
        return "ws://127.0.0.1:9222/devtools/browser/abc"

    monkeypatch.setattr(app, "_resolve_browser_ws", _ok)
    pw = _FlakyPw(fail_times=99, browser=object())  # never recovers

    with pytest.raises(app.HTTPException) as ei:
        asyncio.run(app._attach_cdp_or_503(pw, "http://127.0.0.1:9222"))
    assert ei.value.status_code == 503
    assert ei.value.detail["stage"] == "connect_over_cdp"
    assert ei.value.detail["attempts"] == app._CDP_ATTACH_ATTEMPTS
    assert pw.chromium.calls == app._CDP_ATTACH_ATTEMPTS
    assert app._CDP_ATTACH_ATTEMPTS >= 2


def test_auth_routes_go_through_the_bounded_helper(monkeypatch):
    """Route coverage: the auth endpoints must fast-fail through the shared
    helper, not hang on a raw 180s connect_over_cdp."""
    from fastapi.testclient import TestClient

    called = {"attach": 0}

    async def _fake_attach(pw, cdp_url):
        called["attach"] += 1
        raise app.HTTPException(status_code=503, detail={"stage": "connect_over_cdp", "attempts": 3})

    monkeypatch.setattr(app, "_attach_cdp_or_503", _fake_attach)
    client = TestClient(app.app)

    r1 = client.get("/auth/chatgpt/status")
    assert r1.status_code == 503 and r1.json()["detail"]["stage"] == "connect_over_cdp"
    r2 = client.request("DELETE", "/auth/chatgpt/cookies")
    assert r2.status_code == 503 and r2.json()["detail"]["stage"] == "connect_over_cdp"
    assert called["attach"] == 2, "auth routes bypassed the shared attach helper"


def test_cdp_attach_health_reports_structured_ok_and_degraded(monkeypatch):
    """Smoke command: a structured health result Codex can run before/after the
    real image gate, without parsing an opaque 500."""
    # degraded path
    async def _attach_fail(pw, cdp_url):
        raise app.HTTPException(status_code=503, detail={"stage": "connect_over_cdp", "attempts": 3, "error": "ConnectionError"})

    monkeypatch.setattr(app, "_attach_cdp_or_503", _attach_fail)
    degraded = asyncio.run(app.cdp_attach_health())
    assert degraded["ok"] is False
    assert degraded["stage"] == "connect_over_cdp"

    # healthy path
    class _Ctx:
        pass

    class _Browser:
        contexts = [_Ctx(), _Ctx()]
        async def close(self):
            return None

    async def _attach_ok(pw, cdp_url):
        return _Browser()

    monkeypatch.setattr(app, "_attach_cdp_or_503", _attach_ok)
    healthy = asyncio.run(app.cdp_attach_health())
    assert healthy["ok"] is True
    assert healthy["contexts"] == 2
