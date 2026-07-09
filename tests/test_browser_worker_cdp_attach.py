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
