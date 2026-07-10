"""bug-507: a 503 from the browser-worker (runtime booting after restart) must be
retried with backoff, not fail the whole render job in seconds.

Run-6 live repro: worker started while Chromium was still booting; every
/chatgpt/send returned the structured 503 and the run died in under a minute
with 'produced no rendered Short'. The 503 is transient by design (bug-497 made
attach fail fast) — the CLIENT side must absorb the boot window.
"""

from __future__ import annotations

import asyncio

from video_agent.orchestrator.browser_client import BrowserClientError
from video_agent.shorts.llm import chatgpt_send_with_recovery


class _BootingClient:
    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0
        self.sleeps: list[float] = []

    async def chatgpt_send(self, prompt: str, *, response_timeout_ms: int = 0) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise BrowserClientError(
                "browser-worker chatgpt/send returned HTTP 503",
                status_code=503,
                detail={"stage": "connect_over_cdp", "error": "boot"},
            )
        return "hola"


def test_503_is_retried_with_backoff_then_succeeds(monkeypatch):
    client = _BootingClient(fail_times=2)

    async def _fake_sleep(sec):
        client.sleeps.append(sec)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    out = asyncio.get_event_loop().run_until_complete(
        chatgpt_send_with_recovery(client, "ping")
    ) if False else asyncio.run(chatgpt_send_with_recovery(client, "ping"))
    assert out == "hola"
    assert client.calls == 3
    assert len(client.sleeps) == 2  # backed off between attempts


def test_503_gives_up_after_budget(monkeypatch):
    client = _BootingClient(fail_times=99)

    async def _fake_sleep(sec):
        client.sleeps.append(sec)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    import pytest
    with pytest.raises(BrowserClientError):
        asyncio.run(chatgpt_send_with_recovery(client, "ping"))
    assert client.calls >= 4  # several attempts before surfacing
