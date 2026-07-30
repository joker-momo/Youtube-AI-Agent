from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from .dashboard_support import make_dashboard


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.integration
def test_dashboard_real_browser_desktop_and_narrow(tmp_path: Path) -> None:
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    context = make_dashboard(
        tmp_path,
        allowed_hosts={f"127.0.0.1:{port}"},
        allowed_origins={origin},
    )
    server = uvicorn.Server(
        uvicorn.Config(context.app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on(
                "console",
                lambda message: errors.append(message.text)
                if message.type in {"error", "warning"}
                else None,
            )
            page.goto(origin, wait_until="networkidle")
            assert page.get_by_role("heading", name="Localized V2 operator").is_visible()
            assert page.get_by_text("Worker offline", exact=False).is_visible()
            page.get_by_label("Channel").select_option("healthy-life-en")
            hostile = "<img src=x onerror=document.body.dataset.pwned=1>"
            page.get_by_label("Video topic").fill(hostile)
            page.get_by_role("button", name="Create queued job").click()
            page.get_by_text(hostile, exact=True).first.wait_for()
            assert page.locator("img[src='x']").count() == 0
            assert page.locator("body").get_attribute("data-pwned") is None
            assert "job=" in page.url
            page.reload(wait_until="networkidle")
            page.get_by_text(hostile, exact=True).first.wait_for()
            assert page.get_by_role("button", name="Cancel job").is_visible()
            page.get_by_label("Status").select_option("COMPLETED")
            page.get_by_text("No localized V2 jobs match this filter.").wait_for()
            assert page.get_by_text("Select a job to inspect its progress.").is_visible()
            page.get_by_label("Status").select_option("")
            page.get_by_text(hostile, exact=True).first.wait_for()
            page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement !== document.body")
            for width in (320, 768, 1024, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                layout = page.evaluate(
                    """() => ({
                      clientWidth: document.documentElement.clientWidth,
                      scrollWidth: document.documentElement.scrollWidth,
                      offenders: [...document.querySelectorAll('*')]
                        .filter((node) =>
                          node.getBoundingClientRect().right >
                            document.documentElement.clientWidth + 1 ||
                          node.scrollWidth > node.clientWidth + 1)
                        .map((node) => ({
                          tag: node.tagName,
                          id: node.id,
                          className: node.className,
                          right: node.getBoundingClientRect().right,
                          clientWidth: node.clientWidth,
                          scrollWidth: node.scrollWidth,
                        }))
                        .slice(0, 10),
                    })"""
                )
                assert layout["scrollWidth"] <= layout["clientWidth"], json.dumps(layout)
                assert page.get_by_role("button", name="Cancel job").is_visible()
            assert errors == []
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
