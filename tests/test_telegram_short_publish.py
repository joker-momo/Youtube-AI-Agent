"""Tests for the Shorts → Telegram publish-package handoff.

After a Short renders, ``notify_short_rendered`` must ship short.mp4 (original
bytes, caption = SEO title) and a copy-paste publish text (title/description/
hashtags/pinned comment in tap-to-copy <pre> blocks) so the operator can post
from a phone. No cover image — Shorts have no cover deliverable. No real
Telegram API is ever hit — sends are monkeypatched.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from video_agent.notifications.telegram import (
    _short_publish_text,
    notify_short_rendered,
)

SEO = {
    "title": "¿Pan integral de verdad?",
    "description": "Mira la etiqueta antes de comprar.",
    "tags": ["pan", "salud 45"],
    "pinned_comment": "¿Tú miras la etiqueta?",
}


def _make_short(tmp_path, *, with_video=True, with_seo=True):
    short_dir = tmp_path / "shorts" / "short-01"
    (short_dir / "json").mkdir(parents=True)
    (short_dir / "outputs").mkdir(parents=True)
    if with_seo:
        (short_dir / "json" / "short_seo.json").write_text(
            json.dumps(SEO, ensure_ascii=False), encoding="utf-8"
        )
    if with_video:
        (short_dir / "outputs" / "short.mp4").write_bytes(b"fake-mp4")
    return tmp_path


def test_publish_text_has_copyable_blocks():
    text = _short_publish_text("short-01", SEO)
    assert "short-01" in text
    assert "<pre>¿Pan integral de verdad?</pre>" in text
    assert "Mira la etiqueta antes de comprar." in text
    # Tags become hashtags; spaces collapsed so YouTube accepts them.
    assert "#pan" in text
    assert "#salud45" in text
    assert "¿Tú miras la etiqueta?" in text


def test_publish_text_without_seo_warns():
    text = _short_publish_text("short-01", {})
    assert "short_seo.json missing" in text


def test_notify_short_rendered_sends_original_video_and_text_no_cover(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEO_AGENT_DISABLE_TELEGRAM", raising=False)
    job_dir = _make_short(tmp_path)
    sent = {"video": [], "photo": [], "text": []}

    async def fake_video_doc(path, caption=""):
        sent["video"].append((path.name, caption))

    async def fake_photo(path, caption=""):
        sent["photo"].append((path.name, caption))

    async def fake_send(text):
        sent["text"].append(text)

    with (
        patch("video_agent.notifications.telegram._send_video_document", new=fake_video_doc),
        patch("video_agent.notifications.telegram._send_photo_file", new=fake_photo),
        patch("video_agent.notifications.telegram.send", new=fake_send),
    ):
        asyncio.run(notify_short_rendered(job_dir, "short-01"))

    assert sent["video"] == [("short.mp4", sent["video"][0][1])]
    assert "¿Pan integral de verdad?" in sent["video"][0][1]
    assert sent["photo"] == []  # Shorts have no cover — nothing else uploaded
    assert any("Publish package" in t for t in sent["text"])


def test_short_video_goes_as_document_never_compressed(tmp_path, monkeypatch):
    """The Short master must go through sendDocument (original bytes) — never
    through _send_video_file (which Telegram re-encodes and which compresses
    files over 50 MB)."""
    import inspect

    from video_agent.notifications import telegram as tg

    src = inspect.getsource(tg.notify_short_rendered)
    assert "_send_video_document" in src
    assert "_send_video_file" not in src

    doc_src = inspect.getsource(tg._send_video_document)
    assert "_compress_video" not in doc_src
    assert "send_document" in doc_src


def test_send_video_document_over_cap_sends_path_not_compressed(tmp_path, monkeypatch):
    """>50 MB: never degrade the publish master — send the disk path instead."""
    from video_agent.notifications import telegram as tg

    monkeypatch.delenv("VIDEO_AGENT_DISABLE_TELEGRAM", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    big = tmp_path / "short.mp4"
    big.write_bytes(b"x")
    monkeypatch.setattr(tg, "_TELEGRAM_FILE_LIMIT", 0)  # force over-cap branch

    texts: list[str] = []

    async def fake_send(text):
        texts.append(text)

    fake_bot = object()
    with (
        patch("video_agent.notifications.telegram.send", new=fake_send),
        patch("video_agent.notifications.telegram._bot", return_value=fake_bot),
    ):
        asyncio.run(tg._send_video_document(big, caption="cap"))

    assert any(str(big) in t for t in texts)


def test_notify_short_rendered_missing_video_sends_warning(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEO_AGENT_DISABLE_TELEGRAM", raising=False)
    job_dir = _make_short(tmp_path, with_video=False)
    texts: list[str] = []

    async def fake_send(text):
        texts.append(text)

    with (
        patch(
            "video_agent.notifications.telegram._send_video_document", new=AsyncMock()
        ) as video_mock,
        patch("video_agent.notifications.telegram._send_photo_file", new=AsyncMock()),
        patch("video_agent.notifications.telegram.send", new=fake_send),
    ):
        asyncio.run(notify_short_rendered(job_dir, "short-01"))

    video_mock.assert_not_awaited()
    assert any("short.mp4 not found" in t for t in texts)


def test_notify_short_rendered_disabled_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_AGENT_DISABLE_TELEGRAM", "1")
    job_dir = _make_short(tmp_path)
    with (
        patch(
            "video_agent.notifications.telegram._send_video_document", new=AsyncMock()
        ) as video_mock,
        patch("video_agent.notifications.telegram.send", new=AsyncMock()) as send_mock,
    ):
        asyncio.run(notify_short_rendered(job_dir, "short-01"))
    video_mock.assert_not_awaited()
    send_mock.assert_not_awaited()


def test_render_paths_call_publish_handoff():
    """Both render completion paths (worker render-only job + builder autopilot)
    must hand off to notify_short_rendered_sync."""
    import inspect

    from video_agent.orchestrator import worker
    from video_agent.shorts.builder.stages import media

    assert "notify_short_rendered_sync" in inspect.getsource(worker._run_short_render_job)
    assert "notify_short_rendered_sync" in inspect.getsource(media._stage_performance_memory)
