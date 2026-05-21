"""Tests for Telegram notification helpers.

We never hit the real Telegram API — all sends are intercepted via
monkeypatching `_send_message` or by leaving TELEGRAM_BOT_TOKEN unset
so the function exits immediately.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from video_agent.notifications.telegram import (
    _configured,
    notify_batch_done,
    notify_job_done_with_files,
    notify_job_failed,
    notify_job_started,
    notify_stage_done,
    send,
)


# ---------------------------------------------------------------------------
# _configured
# ---------------------------------------------------------------------------


def test_configured_true(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    assert _configured() is True


def test_configured_false_missing_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    assert _configured() is False


def test_configured_false_missing_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert _configured() is False


# ---------------------------------------------------------------------------
# send — no-op when unconfigured
# ---------------------------------------------------------------------------


def test_send_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    # Should not raise even if httpx is broken
    asyncio.run(send("hello"))


# ---------------------------------------------------------------------------
# send — calls API when configured
# ---------------------------------------------------------------------------


def test_send_posts_to_api(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")

    captured = []

    async def fake_send(text, *, parse_mode="HTML"):
        captured.append(text)

    with patch("video_agent.notifications.telegram._send_message", fake_send):
        asyncio.run(send("test message"))

    assert captured == ["test message"]


# ---------------------------------------------------------------------------
# High-level helpers — check text content
# ---------------------------------------------------------------------------


def _run_capture(coro):
    """Run a notify_* coroutine, capture what _send_message received."""
    captured = []

    async def fake_send(text, **_):
        captured.append(text)

    async def wrapper():
        with patch("video_agent.notifications.telegram._send_message", fake_send):
            await coro

    asyncio.run(wrapper())
    return captured


def test_notify_job_started():
    msgs = _run_capture(notify_job_started("job-xyz"))
    assert len(msgs) == 1
    assert "job-xyz" in msgs[0]
    assert "started" in msgs[0].lower()


def test_notify_job_done_with_files_sends_no_text(tmp_path):
    # notify_job_done_with_files sends ONLY photo + video (no text message).
    # _send_photo_file / _send_video_file are NOT intercepted by _run_capture
    # (they don't call _send_message), so msgs should be empty.
    thumb = tmp_path / "thumbnail_1.jpg"
    thumb.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 100)

    msgs = _run_capture(
        notify_job_done_with_files(
            "job-xyz",
            job_dir=tmp_path,
            stages_done=["script", "render"],
            wall_seconds=143,
        )
    )
    # No text messages — only file uploads (not captured by _send_message mock)
    assert msgs == []


def test_notify_job_failed_contains_error():
    msgs = _run_capture(
        notify_job_failed("job-xyz", stopped_at="render", error="Render crashed")
    )
    assert len(msgs) == 1
    assert "job-xyz" in msgs[0]
    assert "render" in msgs[0]
    assert "Render crashed" in msgs[0]


def test_notify_stage_done_only_for_known_stages():
    important = _run_capture(notify_stage_done("job-xyz", "render", "video.mp4"))
    assert important  # render IS a notified stage

    minor = _run_capture(notify_stage_done("job-xyz", "script_promote"))
    assert not minor  # script_promote is NOT notified


def test_notify_batch_done_success():
    msgs = _run_capture(notify_batch_done(total=3, succeeded=3, failed=0))
    assert "✅" in msgs[0]
    assert "3/3" in msgs[0]


def test_notify_batch_done_partial_failure():
    msgs = _run_capture(
        notify_batch_done(total=3, succeeded=2, failed=1, failed_jobs=["bad-job"])
    )
    assert "⚠️" in msgs[0]
    assert "bad-job" in msgs[0]
