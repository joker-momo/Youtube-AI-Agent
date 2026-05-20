from __future__ import annotations

import os
import re
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from video_agent.browser_worker.drivers import (
    BrowserDriverError,
    ChatGPTDriver,
    ClaudeDriver,
    GeminiDriver,
    LoginRequiredError,
    VidIQDriver,
    human_pause,
    save_trace_screenshot,
)

app = FastAPI(title="video-agent-browser-worker", version="0.2.0")


def _cdp_url() -> str:
    """CDP endpoint for the Browser Appliance runtime container.

    Defaults to ``http://browser-runtime:9222``; the runtime container
    exposes CDP on the internal ``appliance_net`` Docker network only,
    so this URL is reachable from the worker but never from the host.
    """
    return os.environ.get("CHROME_CDP_URL", "http://browser-runtime:9222")


def _is_logged_out_url(site: str, url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if site == "chatgpt":
        return "auth.openai.com" in host or "/auth/login" in path or path == "/login"
    if site == "gemini":
        return "accounts.google.com" in host or "signin" in path
    if site == "claude":
        return (
            "claude.ai/login" in url.lower()
            or "claude.ai/sign-in" in url.lower()
            or "/signin" in path
            or "/auth/" in path
        )
    return False


def _login_required_message(site: str) -> str:
    label = {"chatgpt": "ChatGPT", "gemini": "Gemini", "claude": "Claude"}.get(site, site)
    return (
        f"Login required for {label} in the browser-runtime profile. "
        "Open http://localhost:7900 (noVNC), sign in once, then retry."
    )


async def _resolve_browser_ws(base_cdp_url: str) -> str:
    """Fetch Chromium's CDP entrypoint and rewrite the host.

    Chromium 119+ refuses to bind CDP on anything but loopback, even when
    ``--remote-debugging-address`` is passed. The runtime container uses
    socat to publish the loopback port on 0.0.0.0 inside its network
    namespace, but Chromium's ``/json/version`` response still advertises
    ``ws://127.0.0.1:<port>/...`` as the websocket endpoint. Playwright
    would follow that literally and fail because 127.0.0.1 in the worker
    container is the worker itself. We rewrite the host/port of the
    advertised websocket URL to match ``base_cdp_url`` so the connection
    actually reaches the runtime.
    """
    # Chromium's CDP HTTP server enforces ``Host: localhost`` to defend
    # against DNS rebinding. The runtime container reaches that server
    # via the docker network, so requests would arrive with
    # ``Host: browser-runtime`` and be rejected with 500. Forcing the
    # header back to localhost matches what Chromium expects.
    async with httpx.AsyncClient(
        timeout=5.0,
        headers={"Host": "localhost"},
    ) as http:
        response = await http.get(f"{base_cdp_url.rstrip('/')}/json/version")
        response.raise_for_status()
        payload = response.json()
    ws_url = payload.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("webSocketDebuggerUrl missing from /json/version")
    base = urlparse(base_cdp_url)
    rewritten = urlparse(ws_url)._replace(netloc=base.netloc)
    return urlunparse(rewritten)


def _target_url(site: str) -> str:
    targets = {
        "chatgpt": "https://chatgpt.com/",
        "gemini": "https://gemini.google.com/app",
        "claude": "https://claude.ai/new",
    }
    if site not in targets:
        raise ValueError(f"Unsupported auth site: {site}")
    return targets[site]


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "browser-worker"}


@app.get("/runtime")
async def runtime() -> dict:
    """Diagnostic: connect to the Browser Appliance runtime over CDP.

    Returns 503 with the underlying error when the runtime container is
    not reachable (typically because ``docker compose up browser-runtime``
    has not been started or Chromium is still booting).
    """
    from playwright.async_api import async_playwright

    url = _cdp_url()
    try:
        async with async_playwright() as pw:
            ws_endpoint = await _resolve_browser_ws(url)
            browser = await pw.chromium.connect_over_cdp(ws_endpoint)
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


class SendPromptRequest(BaseModel):
    prompt: str
    response_timeout_ms: int = 300_000


class OpenSessionResponse(BaseModel):
    session_id: str
    site: str


# In-memory session registry. Each entry owns a Playwright connection,
# a Page, and a driver instance. The caller is responsible for closing
# sessions; orphaned entries hold a CDP connection so the orchestrator
# should always DELETE in a try/finally.
import uuid as _uuid

_SESSIONS: dict[str, dict] = {}


async def _connect_runtime():
    """Returns (playwright_ctx, browser, ws_endpoint). Caller must
    `await playwright_ctx.__aexit__` to release the connection.
    """
    from playwright.async_api import async_playwright

    cdp_url = _cdp_url()
    ws_endpoint = await _resolve_browser_ws(cdp_url)
    pw_ctx = async_playwright()
    pw = await pw_ctx.__aenter__()
    browser = await pw.chromium.connect_over_cdp(ws_endpoint)
    return pw_ctx, browser


async def _open_session(site: str) -> str:
    """Create a new session: connect runtime, open page, run driver.open()."""
    if site not in {"chatgpt", "gemini", "claude", "vidiq"}:
        raise HTTPException(status_code=404, detail=f"Unsupported site: {site}")
    try:
        pw_ctx, browser = await _connect_runtime()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"cdp_url": _cdp_url(), "error": str(exc)},
        ) from exc

    try:
        context = (
            browser.contexts[0]
            if browser.contexts
            else await browser.new_context()
        )
        page = await context.new_page()
        await human_pause(page, min_ms=300, max_ms=900)
        if site == "chatgpt":
            driver = ChatGPTDriver(page)
        elif site == "gemini":
            driver = GeminiDriver(page)
        elif site == "claude":
            driver = ClaudeDriver(page)
        else:
            driver = VidIQDriver(page)
        try:
            await driver.open()
        except LoginRequiredError as exc:
            await page.close()
            await browser.close()
            await pw_ctx.__aexit__(None, None, None)
            raise HTTPException(
                status_code=409,
                detail={
                    "error": str(exc),
                    "screenshot": exc.screenshot_path or "",
                    "login_required": True,
                },
            ) from exc
        except BrowserDriverError as exc:
            await page.close()
            await browser.close()
            await pw_ctx.__aexit__(None, None, None)
            raise HTTPException(
                status_code=502,
                detail={"error": str(exc), "screenshot": exc.screenshot_path or ""},
            ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        await browser.close()
        await pw_ctx.__aexit__(None, None, None)
        raise HTTPException(
            status_code=502,
            detail={"error": f"{type(exc).__name__}: {exc}"},
        ) from exc

    sid = _uuid.uuid4().hex
    _SESSIONS[sid] = {
        "site": site,
        "page": page,
        "browser": browser,
        "pw_ctx": pw_ctx,
        "driver": driver,
    }
    return sid


async def _close_session(session_id: str) -> bool:
    entry = _SESSIONS.pop(session_id, None)
    if entry is None:
        return False
    try:
        await human_pause(entry["page"], min_ms=400, max_ms=1100)
    except Exception:
        pass
    try:
        await entry["page"].close()
    except Exception:
        pass
    try:
        await entry["browser"].close()
    except Exception:
        pass
    try:
        await entry["pw_ctx"].__aexit__(None, None, None)
    except Exception:
        pass
    return True


async def _send_in_session(session_id: str, prompt: str, timeout_ms: int) -> str:
    entry = _SESSIONS.get(session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown session: {session_id}")
    driver = entry["driver"]
    page = entry["page"]
    try:
        return await driver.send_message(prompt, response_timeout_ms=timeout_ms)
    except LoginRequiredError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": str(exc),
                "screenshot": exc.screenshot_path or "",
                "login_required": True,
            },
        ) from exc
    except BrowserDriverError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": str(exc), "screenshot": exc.screenshot_path or ""},
        ) from exc
    except Exception as exc:
        try:
            shot = await save_trace_screenshot(page, prefix=f"{entry['site']}-uncaught")
        except Exception:
            shot = ""
        raise HTTPException(
            status_code=502,
            detail={"error": f"{type(exc).__name__}: {exc}", "screenshot": shot},
        ) from exc


async def _drive(site: str, prompt: str, timeout_ms: int) -> dict:
    """Open a page on the runtime, run the driver, close, return text."""
    from playwright.async_api import async_playwright

    cdp_url = _cdp_url()
    try:
        ws_endpoint = await _resolve_browser_ws(cdp_url)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"cdp_url": cdp_url, "error": str(exc)},
        ) from exc

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(ws_endpoint)
        try:
            context = (
                browser.contexts[0]
                if browser.contexts
                else await browser.new_context()
            )
            page = await context.new_page()
            # Brief "tab just opened" beat — humans don't drive a tab
            # the instant Ctrl+T finishes; let the chrome paint settle.
            await human_pause(page, min_ms=300, max_ms=900)
            try:
                if site == "chatgpt":
                    driver = ChatGPTDriver(page)
                elif site == "gemini":
                    driver = GeminiDriver(page)
                elif site == "claude":
                    driver = ClaudeDriver(page)
                else:  # defensive; routes only call known sites
                    raise HTTPException(
                        status_code=404, detail=f"Unsupported site: {site}"
                    )
                text = await driver.send(prompt, response_timeout_ms=timeout_ms)
                return {"site": site, "raw_response": text}
            except LoginRequiredError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": str(exc),
                        "screenshot": exc.screenshot_path or "",
                        "login_required": True,
                    },
                ) from exc
            except BrowserDriverError as exc:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": str(exc),
                        "screenshot": exc.screenshot_path or "",
                    },
                ) from exc
            except HTTPException:
                raise
            except Exception as exc:
                # Playwright TimeoutError, navigation failures, etc. We
                # want a structured response with a screenshot rather
                # than a bare 500.
                try:
                    shot = await save_trace_screenshot(
                        page, prefix=f"{site}-uncaught"
                    )
                except Exception:
                    shot = ""
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": f"{type(exc).__name__}: {exc}",
                        "screenshot": shot,
                    },
                ) from exc
            finally:
                # Beat before closing the tab — a person glances at the
                # final state, doesn't slam Ctrl+W the moment the reply
                # finishes streaming.
                try:
                    await human_pause(page, min_ms=400, max_ms=1100)
                except Exception:
                    pass
                try:
                    await page.close()
                except Exception:
                    pass
        finally:
            await browser.close()


@app.post("/chatgpt/sessions", response_model=OpenSessionResponse)
async def chatgpt_open_session() -> dict:
    sid = await _open_session("chatgpt")
    return {"session_id": sid, "site": "chatgpt"}


@app.post("/chatgpt/sessions/{session_id}/send")
async def chatgpt_session_send(session_id: str, payload: SendPromptRequest) -> dict:
    raw = await _send_in_session(session_id, payload.prompt, payload.response_timeout_ms)
    return {"site": "chatgpt", "session_id": session_id, "raw_response": raw}


@app.delete("/chatgpt/sessions/{session_id}", status_code=204)
async def chatgpt_close_session(session_id: str):
    closed = await _close_session(session_id)
    if not closed:
        raise HTTPException(status_code=404, detail=f"Unknown session: {session_id}")
    return None


@app.post("/gemini/sessions", response_model=OpenSessionResponse)
async def gemini_open_session() -> dict:
    sid = await _open_session("gemini")
    return {"session_id": sid, "site": "gemini"}


@app.post("/gemini/sessions/{session_id}/send")
async def gemini_session_send(session_id: str, payload: SendPromptRequest) -> dict:
    raw = await _send_in_session(session_id, payload.prompt, payload.response_timeout_ms)
    return {"site": "gemini", "session_id": session_id, "raw_response": raw}


@app.delete("/gemini/sessions/{session_id}", status_code=204)
async def gemini_close_session(session_id: str):
    closed = await _close_session(session_id)
    if not closed:
        raise HTTPException(status_code=404, detail=f"Unknown session: {session_id}")
    return None


@app.post("/claude/sessions", response_model=OpenSessionResponse)
async def claude_open_session() -> dict:
    sid = await _open_session("claude")
    return {"session_id": sid, "site": "claude"}


@app.post("/claude/sessions/{session_id}/send")
async def claude_session_send(session_id: str, payload: SendPromptRequest) -> dict:
    raw = await _send_in_session(session_id, payload.prompt, payload.response_timeout_ms)
    return {"site": "claude", "session_id": session_id, "raw_response": raw}


@app.delete("/claude/sessions/{session_id}", status_code=204)
async def claude_close_session(session_id: str):
    closed = await _close_session(session_id)
    if not closed:
        raise HTTPException(status_code=404, detail=f"Unknown session: {session_id}")
    return None


class KeywordRequest(BaseModel):
    keyword: str
    response_timeout_ms: int = 30_000


class KeywordsRequest(BaseModel):
    keywords: list[str]
    response_timeout_ms: int = 30_000


@app.post("/vidiq/sessions", response_model=OpenSessionResponse)
async def vidiq_open_session() -> dict:
    sid = await _open_session("vidiq")
    return {"session_id": sid, "site": "vidiq"}


@app.post("/vidiq/sessions/{session_id}/score")
async def vidiq_session_score(session_id: str, payload: KeywordRequest) -> dict:
    entry = _SESSIONS.get(session_id)
    if entry is None or entry["site"] != "vidiq":
        raise HTTPException(status_code=404, detail=f"Unknown vidiq session: {session_id}")
    driver: VidIQDriver = entry["driver"]
    try:
        return await driver.score_keyword(
            payload.keyword, response_timeout_ms=payload.response_timeout_ms
        )
    except LoginRequiredError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": str(exc),
                "screenshot": exc.screenshot_path or "",
                "login_required": True,
            },
        ) from exc
    except BrowserDriverError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": str(exc), "screenshot": exc.screenshot_path or ""},
        ) from exc


@app.post("/vidiq/sessions/{session_id}/score_batch")
async def vidiq_session_score_batch(
    session_id: str, payload: KeywordsRequest
) -> dict:
    entry = _SESSIONS.get(session_id)
    if entry is None or entry["site"] != "vidiq":
        raise HTTPException(status_code=404, detail=f"Unknown vidiq session: {session_id}")
    driver: VidIQDriver = entry["driver"]
    results = await driver.score_keywords(payload.keywords)
    return {"session_id": session_id, "results": results}


@app.delete("/vidiq/sessions/{session_id}", status_code=204)
async def vidiq_close_session(session_id: str):
    closed = await _close_session(session_id)
    if not closed:
        raise HTTPException(status_code=404, detail=f"Unknown session: {session_id}")
    return None


@app.post("/chatgpt/send")
async def chatgpt_send(payload: SendPromptRequest) -> dict:
    return await _drive("chatgpt", payload.prompt, payload.response_timeout_ms)


@app.post("/gemini/send")
async def gemini_send(payload: SendPromptRequest) -> dict:
    return await _drive("gemini", payload.prompt, payload.response_timeout_ms)


@app.post("/claude/send")
async def claude_send(payload: SendPromptRequest) -> dict:
    return await _drive("claude", payload.prompt, payload.response_timeout_ms)


@app.get("/auth/{site}/status")
async def auth_status(site: str) -> dict:
    from playwright.async_api import async_playwright

    try:
        target_url = _target_url(site)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    cdp_url = _cdp_url()
    try:
        ws_endpoint = await _resolve_browser_ws(cdp_url)
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(ws_endpoint)
            try:
                context = (
                    browser.contexts[0]
                    if browser.contexts
                    else await browser.new_context()
                )
                page = await context.new_page()
                await human_pause(page, min_ms=300, max_ms=900)
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                await human_pause(page, min_ms=1200, max_ms=2200)
                current_url = page.url
                logged_out = _is_logged_out_url(site, current_url)
                if not logged_out:
                    login_cue = page.get_by_role(
                        "button", name=re.compile(r"^(log in|sign in)$", re.I)
                    )
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
                    "message": (
                        _login_required_message(site)
                        if logged_out
                        else "Logged in."
                    ),
                }
            finally:
                await browser.close()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"cdp_url": cdp_url, "error": str(exc)},
        ) from exc
