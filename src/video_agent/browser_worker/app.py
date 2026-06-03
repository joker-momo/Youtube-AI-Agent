from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


def _assets_root() -> Path:
    """Root directory the worker is allowed to write generated assets into."""
    return Path(os.environ.get("WORKER_ASSETS_ROOT", "/app/jobs")).resolve()


def _safe_asset_path(out_path: str) -> Path:
    """Resolve ``out_path`` and ensure it stays inside ``_assets_root``.

    Raises HTTPException(400) on traversal or absolute paths outside the root.
    """
    if not out_path:
        raise HTTPException(status_code=400, detail="out_path required")
    root = _assets_root()

    # If the path is absolute and contains 'jobs/' (e.g. host absolute path),
    # convert it to a relative path starting from the segment after 'jobs/'
    # so it maps correctly into the container's root.
    if Path(out_path).is_absolute() and "jobs/" in out_path:
        parts = out_path.split("jobs/", 1)
        out_path = parts[1]

    candidate = (root / out_path).resolve() if not Path(out_path).is_absolute() else Path(out_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"out_path must be inside {root}",
        ) from exc
    return candidate


_SESSION_LOCKS: dict[str, asyncio.Lock] = {}


def _site_lock(site: str) -> asyncio.Lock:
    lock = _SESSION_LOCKS.get(site)
    if lock is None:
        lock = asyncio.Lock()
        _SESSION_LOCKS[site] = lock
    return lock

from video_agent.browser_worker.drivers import (
    BrowserDriverError,
    ChatGPTDriver,
    GeminiDriver,
    LoginRequiredError,
    QuotaExceededError,
    clear_browser_data_keep_login,
    human_pause,
    save_trace_screenshot,
)

app = FastAPI(title="video-agent-browser-worker", version="0.2.0")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _cleanup_trace_dir() -> None:
    root = Path(os.environ.get("BROWSER_TRACE_DIR", "/data/trace"))
    if not root.exists():
        return

    retention_days = max(0, _int_env("BROWSER_TRACE_RETENTION_DAYS", 3))
    max_bytes = max(1, _int_env("BROWSER_TRACE_MAX_MB", 512)) * 1024 * 1024
    cutoff = time.time() - (retention_days * 24 * 60 * 60)

    files = [path for path in root.rglob("*") if path.is_file()]
    for path in files:
        try:
            if retention_days and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            pass

    files = [path for path in root.rglob("*") if path.is_file()]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    total = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total += size
        if total > max_bytes:
            path.unlink(missing_ok=True)


@app.on_event("startup")
def cleanup_runtime_artifacts() -> None:
    _cleanup_trace_dir()


def _driver_error_detail(
    exc: BrowserDriverError, *, login_required: bool = False
) -> dict[str, object]:
    detail: dict[str, object] = {
        "error": str(exc),
        "screenshot": exc.screenshot_path or "",
    }
    if exc.diagnostic_path:
        detail["diagnostic"] = exc.diagnostic_path
    if exc.layout_warning:
        detail["layout_warning"] = True
    if login_required:
        detail["login_required"] = True
    if isinstance(exc, QuotaExceededError):
        detail["quota_exhausted"] = True
    return detail


def _cdp_url() -> str:
    """CDP endpoint for a native Chromium instance on the host Mac.

    Default ``http://127.0.0.1:9222`` expects a Chromium launched with
    ``--remote-debugging-port=9222`` via ``scripts/launch_chromium_mac.sh``.
    Override with ``CHROME_CDP_URL`` if Chromium runs elsewhere.
    """
    return os.environ.get("CHROME_CDP_URL", "http://127.0.0.1:9222")


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
        f"Login required for {label} in the native Chromium profile. "
        "Bring the Chromium window forward, sign in once, then retry."
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
        "chatgpt": "https://chatgpt.com/?temporary-chat=true",
        "gemini": "https://gemini.google.com/app",
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
    url = _cdp_url()
    try:
        from playwright.async_api import async_playwright

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
    if site not in {"chatgpt", "gemini"}:
        raise HTTPException(status_code=404, detail=f"Unsupported site: {site}")
    async with _site_lock(site):
        return await _open_session_locked(site)


async def _open_session_locked(site: str) -> str:
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
        else:
            raise HTTPException(status_code=404, detail=f"Unsupported site: {site}")
        try:
            await driver.open()
        except LoginRequiredError as exc:
            await page.close()
            await browser.close()
            await pw_ctx.__aexit__(None, None, None)
            raise HTTPException(
                status_code=409,
                detail=_driver_error_detail(exc, login_required=True),
            ) from exc
        except QuotaExceededError as exc:
            await page.close()
            await browser.close()
            await pw_ctx.__aexit__(None, None, None)
            raise HTTPException(
                status_code=429,
                detail=_driver_error_detail(exc),
            ) from exc
        except (BrowserDriverError, Exception) as exc:
            print(f"[browser] Error during driver.open() for {site}: {exc}. Clearing browser data & retrying once...", flush=True)
            try:
                await clear_browser_data_keep_login(context)
                try:
                    await page.close()
                except Exception:
                    pass
                page = await context.new_page()
                await human_pause(page, min_ms=300, max_ms=900)
                if site == "chatgpt":
                    driver = ChatGPTDriver(page)
                elif site == "gemini":
                    driver = GeminiDriver(page)
                await page.goto(_target_url(site), wait_until="domcontentloaded", timeout=30_000)
                await driver.open()
            except Exception as retry_exc:
                await page.close()
                await browser.close()
                await pw_ctx.__aexit__(None, None, None)
                if isinstance(retry_exc, LoginRequiredError):
                    raise HTTPException(
                        status_code=409,
                        detail=_driver_error_detail(retry_exc, login_required=True),
                    ) from retry_exc
                elif isinstance(retry_exc, QuotaExceededError):
                    raise HTTPException(
                        status_code=429,
                        detail=_driver_error_detail(retry_exc),
                    ) from retry_exc
                elif isinstance(retry_exc, BrowserDriverError):
                    raise HTTPException(
                        status_code=502,
                        detail=_driver_error_detail(retry_exc),
                    ) from retry_exc
                else:
                    raise HTTPException(
                        status_code=502,
                        detail={"error": f"{type(retry_exc).__name__}: {retry_exc}"},
                    ) from retry_exc
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
            detail=_driver_error_detail(exc, login_required=True),
        ) from exc
    except QuotaExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail=_driver_error_detail(exc),
        ) from exc
    except (BrowserDriverError, Exception) as exc:
        print(f"[browser] Error in _send_in_session for {entry['site']}: {exc}. Clearing browser data & retrying once...", flush=True)
        try:
            await clear_browser_data_keep_login(page.context)
            try:
                await page.close()
            except Exception:
                pass
            page = await entry["browser"].contexts[0].new_page()
            await human_pause(page, min_ms=300, max_ms=900)
            if entry["site"] == "chatgpt":
                driver = ChatGPTDriver(page)
            elif entry["site"] == "gemini":
                driver = GeminiDriver(page)
            # Update session entry
            entry["page"] = page
            entry["driver"] = driver
            await driver.open()
            return await driver.send_message(prompt, response_timeout_ms=timeout_ms)
        except Exception as retry_exc:
            if isinstance(retry_exc, LoginRequiredError):
                raise HTTPException(
                    status_code=409,
                    detail=_driver_error_detail(retry_exc, login_required=True),
                ) from retry_exc
            elif isinstance(retry_exc, QuotaExceededError):
                raise HTTPException(
                    status_code=429,
                    detail=_driver_error_detail(retry_exc),
                ) from retry_exc
            elif isinstance(retry_exc, BrowserDriverError):
                raise HTTPException(
                    status_code=502,
                    detail=_driver_error_detail(retry_exc),
                ) from retry_exc
            else:
                try:
                    shot = await save_trace_screenshot(page, prefix=f"{entry['site']}-uncaught")
                except Exception:
                    shot = ""
                raise HTTPException(
                    status_code=502,
                    detail={"error": f"{type(retry_exc).__name__}: {retry_exc}", "screenshot": shot},
                ) from retry_exc


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
                else:  # defensive; routes only call known sites
                    raise HTTPException(
                        status_code=404, detail=f"Unsupported site: {site}"
                    )
                text = await driver.send(prompt, response_timeout_ms=timeout_ms)
                return {"site": site, "raw_response": text}
            except LoginRequiredError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=_driver_error_detail(exc, login_required=True),
                ) from exc
            except QuotaExceededError as exc:
                raise HTTPException(
                    status_code=429,
                    detail=_driver_error_detail(exc),
                ) from exc
            except (BrowserDriverError, Exception) as exc:
                if isinstance(exc, HTTPException):
                    raise
                print(f"[browser] Error in _drive for {site}: {exc}. Clearing browser data & retrying once...", flush=True)
                try:
                    await clear_browser_data_keep_login(context)
                    try:
                        await page.close()
                    except Exception:
                        pass
                    page = await context.new_page()
                    await human_pause(page, min_ms=300, max_ms=900)
                    if site == "chatgpt":
                        driver = ChatGPTDriver(page)
                    elif site == "gemini":
                        driver = GeminiDriver(page)
                    await page.goto(_target_url(site), wait_until="domcontentloaded", timeout=30_000)
                    text = await driver.send(prompt, response_timeout_ms=timeout_ms)
                    return {"site": site, "raw_response": text}
                except Exception as retry_exc:
                    if isinstance(retry_exc, LoginRequiredError):
                        raise HTTPException(
                            status_code=409,
                            detail=_driver_error_detail(retry_exc, login_required=True),
                        ) from retry_exc
                    elif isinstance(retry_exc, QuotaExceededError):
                        raise HTTPException(
                            status_code=429,
                            detail=_driver_error_detail(retry_exc),
                        ) from retry_exc
                    elif isinstance(retry_exc, BrowserDriverError):
                        raise HTTPException(
                            status_code=502,
                            detail=_driver_error_detail(retry_exc),
                        ) from retry_exc
                    else:
                        try:
                            shot = await save_trace_screenshot(
                                page, prefix=f"{site}-uncaught"
                            )
                        except Exception:
                            shot = ""
                        raise HTTPException(
                            status_code=502,
                            detail={
                                "error": f"{type(retry_exc).__name__}: {retry_exc}",
                                "screenshot": shot,
                            },
                        ) from retry_exc
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



class ImagePromptRequest(BaseModel):
    prompt: str
    project_name: str
    out_path: str  # path relative to WORKER_ASSETS_ROOT, or absolute path inside it
    response_timeout_ms: int = 240_000


@app.post("/chatgpt/image")
async def chatgpt_image(payload: ImagePromptRequest) -> dict:
    """One-shot ChatGPT image generation via the Projects workflow.

    Opens a new Chromium page, creates a project named
    ``payload.project_name``, sends ``payload.prompt`` as an image-gen
    request, downloads the resulting image to ``payload.out_path``,
    and closes the page. Each call creates a fresh project so images
    stay organised per video / per scene.
    """
    from playwright.async_api import async_playwright

    from video_agent.browser_worker.drivers import ChatGPTImageDriver

    safe_out = _safe_asset_path(payload.out_path)
    safe_out.parent.mkdir(parents=True, exist_ok=True)
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
            await human_pause(page, min_ms=400, max_ms=900)
            try:
                driver = ChatGPTImageDriver(page)
                result = await driver.generate_image(
                    payload.prompt,
                    project_name=payload.project_name,
                    out_path=safe_out,
                    response_timeout_ms=payload.response_timeout_ms,
                )
                return result
            except LoginRequiredError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=_driver_error_detail(exc, login_required=True),
                ) from exc
            except (BrowserDriverError, Exception) as exc:
                if isinstance(exc, HTTPException):
                    raise
                print(f"[browser] Error in chatgpt_image: {exc}. Clearing browser data & retrying once...", flush=True)
                try:
                    await clear_browser_data_keep_login(context)
                    try:
                        await page.close()
                    except Exception:
                        pass
                    page = await context.new_page()
                    await human_pause(page, min_ms=400, max_ms=900)
                    driver = ChatGPTImageDriver(page)
                    result = await driver.generate_image(
                        payload.prompt,
                        project_name=payload.project_name,
                        out_path=safe_out,
                        response_timeout_ms=payload.response_timeout_ms,
                    )
                    return result
                except Exception as retry_exc:
                    if isinstance(retry_exc, LoginRequiredError):
                        raise HTTPException(
                            status_code=409,
                            detail=_driver_error_detail(retry_exc, login_required=True),
                        ) from retry_exc
                    elif isinstance(retry_exc, BrowserDriverError):
                        raise HTTPException(
                            status_code=502,
                            detail=_driver_error_detail(retry_exc),
                        ) from retry_exc
                    else:
                        raise HTTPException(
                            status_code=502,
                            detail={"error": f"{type(retry_exc).__name__}: {retry_exc}"},
                        ) from retry_exc
            finally:
                try:
                    await human_pause(page, min_ms=400, max_ms=900)
                except Exception:
                    pass
                try:
                    await page.close()
                except Exception:
                    pass
        finally:
            await browser.close()


class BatchImagePromptRequest(BaseModel):
    prompts: list[str]
    project_name: str
    out_paths: list[str]
    response_timeout_ms: int = 240_000


@app.post("/chatgpt/image/batch")
async def chatgpt_image_batch(payload: BatchImagePromptRequest) -> dict:
    """Sequential ChatGPT image generation in a single project chat session."""
    from playwright.async_api import async_playwright

    from video_agent.browser_worker.drivers import ChatGPTImageDriver

    safe_out_paths = [_safe_asset_path(p) for p in payload.out_paths]
    for p in safe_out_paths:
        p.parent.mkdir(parents=True, exist_ok=True)

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
            await human_pause(page, min_ms=400, max_ms=900)
            try:
                driver = ChatGPTImageDriver(page)
                results = await driver.generate_images(
                    payload.prompts,
                    project_name=payload.project_name,
                    out_paths=safe_out_paths,
                    response_timeout_ms=payload.response_timeout_ms,
                )
                return {"ok": True, "results": results}
            except LoginRequiredError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=_driver_error_detail(exc, login_required=True),
                ) from exc
            except (BrowserDriverError, Exception) as exc:
                if isinstance(exc, HTTPException):
                    raise
                print(f"[browser] Error in chatgpt_image_batch: {exc}. Clearing browser data & retrying once...", flush=True)
                try:
                    await clear_browser_data_keep_login(context)
                    try:
                        await page.close()
                    except Exception:
                        pass
                    page = await context.new_page()
                    await human_pause(page, min_ms=400, max_ms=900)
                    driver = ChatGPTImageDriver(page)
                    results = await driver.generate_images(
                        payload.prompts,
                        project_name=payload.project_name,
                        out_paths=safe_out_paths,
                        response_timeout_ms=payload.response_timeout_ms,
                    )
                    return {"ok": True, "results": results}
                except Exception as retry_exc:
                    if isinstance(retry_exc, LoginRequiredError):
                        raise HTTPException(
                            status_code=409,
                            detail=_driver_error_detail(retry_exc, login_required=True),
                        ) from retry_exc
                    elif isinstance(retry_exc, BrowserDriverError):
                        raise HTTPException(
                            status_code=502,
                            detail=_driver_error_detail(retry_exc),
                        ) from retry_exc
                    else:
                        raise HTTPException(
                            status_code=502,
                            detail={"error": f"{type(retry_exc).__name__}: {retry_exc}"},
                        ) from retry_exc
            finally:
                try:
                    await human_pause(page, min_ms=400, max_ms=900)
                except Exception:
                    pass
                try:
                    await page.close()
                except Exception:
                    pass
        finally:
            await browser.close()


@app.post("/chatgpt/send")
async def chatgpt_send(payload: SendPromptRequest) -> dict:
    return await _drive("chatgpt", payload.prompt, payload.response_timeout_ms)


@app.post("/gemini/send")
async def gemini_send(payload: SendPromptRequest) -> dict:
    return await _drive("gemini", payload.prompt, payload.response_timeout_ms)


_SITE_DOMAINS = {
    "chatgpt": "chatgpt.com",
    "gemini": "google.com",
}


@app.delete("/auth/{site}/cookies", status_code=200)
async def auth_clear_cookies(site: str) -> dict:
    """Clear all cookies for the given site domain in the persistent browser context.

    Useful when HTTP 431 (Request Header Fields Too Large) breaks navigation
    because accumulated cookies bloat the request headers.
    """
    from playwright.async_api import async_playwright

    domain = _SITE_DOMAINS.get(site)
    if not domain:
        raise HTTPException(status_code=404, detail=f"Unsupported site: {site}")

    cdp_url = _cdp_url()
    try:
        ws_endpoint = await _resolve_browser_ws(cdp_url)
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(ws_endpoint)
            try:
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                before = await context.cookies()
                before_count = sum(1 for c in before if domain in c.get("domain", ""))
                await context.clear_cookies(domain=f".{domain}")
                await context.clear_cookies(domain=domain)
                after = await context.cookies()
                after_count = sum(1 for c in after if domain in c.get("domain", ""))
                return {
                    "ok": True,
                    "site": site,
                    "domain": domain,
                    "cleared": before_count - after_count,
                    "remaining": after_count,
                }
            finally:
                await browser.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"cdp_url": cdp_url, "error": str(exc)}) from exc


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
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"cdp_url": cdp_url, "error": str(exc)},
        ) from exc
