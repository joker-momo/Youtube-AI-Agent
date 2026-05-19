from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException

app = FastAPI(title="video-agent-browser-worker", version="0.1.0")


def _cdp_url() -> str:
    return os.environ.get("CHROME_CDP_URL", "http://host.docker.internal:9222")


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

    url = _cdp_url()
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(url)
            try:
                contexts = browser.contexts
                pages = sum(len(ctx.pages) for ctx in contexts)
                return {
                    "ok": True,
                    "cdp_url": url,
                    "contexts": len(contexts),
                    "pages": pages,
                }
            finally:
                await browser.close()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"cdp_url": url, "error": str(exc)},
        ) from exc
