from __future__ import annotations

import os
import re
import socket
from urllib.parse import ParseResult, urlparse, urlunparse

from fastapi import FastAPI, HTTPException

app = FastAPI(title="video-agent-browser-worker", version="0.1.0")


def _cdp_url() -> str:
    return os.environ.get("CHROME_CDP_URL", "http://host.docker.internal:9222")


def _resolve_cdp_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname != "host.docker.internal":
        return url

    host_ip = socket.gethostbyname(parsed.hostname)
    netloc = host_ip
    if parsed.port is not None:
        netloc = f"{host_ip}:{parsed.port}"
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth = f"{auth}:{parsed.password}"
        netloc = f"{auth}@{netloc}"
    return urlunparse(
        ParseResult(
            scheme=parsed.scheme,
            netloc=netloc,
            path=parsed.path,
            params=parsed.params,
            query=parsed.query,
            fragment=parsed.fragment,
        )
    )


def _is_logged_out_url(site: str, url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if site == "chatgpt":
        return "auth.openai.com" in host or "/auth/login" in path or path == "/login"
    if site == "gemini":
        return "accounts.google.com" in host or "signin" in path
    return False


def _login_required_message(site: str) -> str:
    label = {"chatgpt": "ChatGPT", "gemini": "Gemini"}.get(site, site)
    return (
        f"Login required for {label} in the dedicated Chrome CDP profile. "
        "Open the Chrome window, sign in manually, then retry."
    )


def _target_url(site: str) -> str:
    targets = {
        "chatgpt": "https://chatgpt.com/",
        "gemini": "https://gemini.google.com/app",
    }
    if site not in targets:
        raise ValueError(f"Unsupported auth site: {site}")
    return targets[site]


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "browser-worker"}


@app.get("/chrome")
async def chrome() -> dict:
    """Diagnostic: connect to host Chrome over CDP and report counts.

    Returns 503 with the underlying error if the CDP endpoint is
    unreachable, so the caller can decide whether to retry or instruct
    the user to launch the host Chrome profile.
    """
    from playwright.async_api import async_playwright

    requested_url = _cdp_url()
    url = _resolve_cdp_url(requested_url)
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(url)
            try:
                contexts = browser.contexts
                pages = sum(len(ctx.pages) for ctx in contexts)
                return {
                    "ok": True,
                    "cdp_url": url,
                    "requested_cdp_url": requested_url,
                    "contexts": len(contexts),
                    "pages": pages,
                }
            finally:
                await browser.close()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"cdp_url": requested_url, "resolved_cdp_url": url, "error": str(exc)},
        ) from exc


@app.get("/auth/{site}/status")
async def auth_status(site: str) -> dict:
    from playwright.async_api import async_playwright

    try:
        target_url = _target_url(site)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    requested_url = _cdp_url()
    cdp_url = _resolve_cdp_url(requested_url)
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(cdp_url)
            try:
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(1500)
                current_url = page.url
                logged_out = _is_logged_out_url(site, current_url)
                if not logged_out:
                    login_cue = page.get_by_role("button", name=re.compile(r"^(log in|sign in)$", re.I))
                    try:
                        logged_out = await login_cue.first.is_visible(timeout=1000)
                    except Exception:
                        logged_out = False
                return {
                    "ok": True,
                    "site": site,
                    "target_url": target_url,
                    "current_url": current_url,
                    "login_required": logged_out,
                    "logged_in": not logged_out,
                    "message": _login_required_message(site) if logged_out else "Logged in.",
                }
            finally:
                await browser.close()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"cdp_url": requested_url, "resolved_cdp_url": cdp_url, "error": str(exc)},
        ) from exc
