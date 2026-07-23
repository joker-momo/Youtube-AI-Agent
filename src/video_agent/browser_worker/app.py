from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


def _assets_root() -> Path:
    """Root directory the worker is allowed to write generated assets into."""
    val = os.environ.get("WORKER_ASSETS_ROOT")
    if val:
        return Path(val).resolve()
    # Try finding repo jobs directory relative to this file
    repo_jobs = Path(__file__).resolve().parents[3] / "jobs"
    if repo_jobs.exists():
        return repo_jobs.resolve()
    return Path("/app/jobs").resolve()



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
    elif out_path.startswith("jobs/"):
        # Relative paths from repo root also carry the 'jobs/' segment; the
        # assets root itself IS the jobs directory, so keep it un-doubled
        # (a poster once landed in a stray 'jobs/jobs/…' tree, 2026-07-12).
        out_path = out_path[len("jobs/"):]

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


# Playwright's default connect_over_cdp timeout is 180_000 ms. When the runtime
# listener dies mid-session the attach hangs for that full 3 minutes and then
# surfaces as a generic 500. Bound it and raise a structured 503 so the caller
# (and Codex verification) sees the real cause instead of an opaque 500.
_CDP_CONNECT_TIMEOUT_MS = 45_000
# Bound for best-effort page/tab cleanup so a wedged target cannot hang a request.
_PAGE_CLEANUP_TIMEOUT_SEC = 10.0
# The observed failure (bridge 20260709) was a FLAP: after a browser/CDP restart
# the listener briefly appears then disappears, so the first attach hits a stale
# ws endpoint and errors. A small bounded retry that RE-RESOLVES the ws endpoint
# each attempt rides over that transient window; the endpoint id changes when the
# runtime relaunches, so reusing a cached one would just retry a dead socket.
_CDP_ATTACH_ATTEMPTS = 3
_CDP_RETRY_BACKOFF_SEC = 0.5


async def _attach_cdp_or_503(pw, cdp_url: str):
    """Resolve the runtime ws endpoint and attach over CDP, with bounded retries.

    Re-resolves the ws endpoint on every attempt and bounds the connect timeout.
    Raises HTTPException(503) with structured detail (including which stage failed
    and how many attempts were made) on exhaustion, never a bare 500 and never the
    180s hang.
    """
    last_detail: dict[str, Any] = {"cdp_url": cdp_url, "stage": "resolve_ws", "error": "not attempted"}
    for attempt in range(1, _CDP_ATTACH_ATTEMPTS + 1):
        try:
            ws_endpoint = await _resolve_browser_ws(cdp_url)
        except Exception as exc:
            last_detail = {
                "cdp_url": cdp_url, "stage": "resolve_ws", "attempt": attempt,
                "error": f"{type(exc).__name__}: {exc}",
            }
        else:
            try:
                return await pw.chromium.connect_over_cdp(ws_endpoint, timeout=_CDP_CONNECT_TIMEOUT_MS)
            except Exception as exc:
                last_detail = {
                    "cdp_url": cdp_url, "ws_endpoint": ws_endpoint,
                    "stage": "connect_over_cdp", "attempt": attempt,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        if attempt < _CDP_ATTACH_ATTEMPTS:
            await asyncio.sleep(_CDP_RETRY_BACKOFF_SEC)
    raise HTTPException(status_code=503, detail={**last_detail, "attempts": _CDP_ATTACH_ATTEMPTS})


async def cdp_attach_health() -> dict[str, Any]:
    """Structured CDP-attach smoke check (bridge 20260709).

    Attaches through the shared bounded helper and reports a machine-readable
    verdict instead of an opaque 500, so an operator (or Codex, before running the
    real image gate) can tell at a glance whether the browser runtime is reachable.
    """
    cdp_url = _cdp_url()
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as pw:
            browser = await _attach_cdp_or_503(pw, cdp_url)
            try:
                contexts = len(browser.contexts)
            finally:
                await browser.close()
        return {"ok": True, "cdp_url": cdp_url, "contexts": contexts}
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
        return {"ok": False, "cdp_url": cdp_url, **detail}
    except Exception as exc:  # noqa: BLE001 - smoke check never raises
        return {"ok": False, "cdp_url": cdp_url, "stage": "unknown", "error": f"{type(exc).__name__}: {exc}"}


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
            # Bounded attach + structured 503 so this health probe fails fast
            # instead of hanging for the 180s Playwright default.
            browser = await _attach_cdp_or_503(pw, url)
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
    except HTTPException:
        raise  # already a structured 503 from _attach_cdp_or_503
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
    pw_ctx = async_playwright()
    pw = await pw_ctx.__aenter__()
    try:
        browser = await _attach_cdp_or_503(pw, cdp_url)
    except BaseException:
        # release the just-opened Playwright context so a failed attach does not
        # leak the driver process
        await pw_ctx.__aexit__(None, None, None)
        raise
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
    except HTTPException:
        # Structured 503 from _attach_cdp_or_503 (stage / attempt / attempts).
        # Re-raise verbatim — flattening it to {"error": str(exc)} was exactly the
        # bridge-20260709 reopen: the session path lost the CDP failure detail.
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"cdp_url": _cdp_url(), "stage": "connect_runtime", "error": f"{type(exc).__name__}: {exc}"},
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
                if isinstance(retry_exc, HTTPException):
                    raise  # preserve a structured error (e.g. CDP 503), never flatten
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

    async with async_playwright() as pw:
        browser = await _attach_cdp_or_503(pw, cdp_url)
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



def _safe_attachment_path(raw: str | None) -> Path | None:
    """Resolve an optional reference-image path, confined to the repo tree.

    Attachments are operator-provided brand assets (e.g. the thumbnail persona
    photo under configs/), so unlike out_path they may live outside the jobs
    root — but never outside the repository. Raises HTTPException(400) on
    traversal or a missing file."""
    if not raw:
        return None
    from video_agent.contracts import repo_root

    root = repo_root().resolve()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"attachment_path must stay inside the repository: {raw}",
        ) from None
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail=f"attachment_path not found: {raw}")
    return candidate


class ImagePromptRequest(BaseModel):
    prompt: str
    project_name: str
    out_path: str  # path relative to WORKER_ASSETS_ROOT, or absolute path inside it
    response_timeout_ms: int = 240_000
    aspect_ratio: str = "16:9"
    attachment_path: str | None = None  # persona/identity reference image (repo-confined)


async def _generate_image_via_gemini(
    context,
    *,
    prompt: str,
    project_name: str,
    out_path: Path,
    response_timeout_ms: int,
    aspect_ratio: str,
) -> dict:
    """Fallback image gen: same prompt/out_path, via Gemini instead of ChatGPT.

    Used when ChatGPT fails twice (initial attempt + the existing
    clear-data-and-retry pass) — most often the ChatGPT Free-tier account
    hitting its image-generation quota (bug-511). Runs on a fresh page in the
    SAME browser context; no conversation cleanup needed (Gemini temporary
    chat never saves history).
    """
    from video_agent.browser_worker.drivers import GeminiImageDriver

    page = await context.new_page()
    try:
        await human_pause(page, min_ms=400, max_ms=900)
        driver = GeminiImageDriver(page)
        return await driver.generate_image(
            prompt,
            project_name=project_name,
            out_path=out_path,
            response_timeout_ms=response_timeout_ms,
            aspect_ratio=aspect_ratio,
        )
    finally:
        # BOUNDED cleanup (bridge 20260722 r2): if the client disconnected and the
        # page/CDP target is wedged, page.close() can hang indefinitely and leak the
        # request. Cap it so cleanup always returns; a leaked tab is recoverable, a
        # hung worker request is not.
        try:
            await asyncio.wait_for(page.close(), timeout=_PAGE_CLEANUP_TIMEOUT_SEC)
        except Exception:
            pass


def _gemini_fallback_error_detail(gemini_exc: Exception, chatgpt_exc: Exception) -> dict:
    detail = (
        _driver_error_detail(gemini_exc)
        if isinstance(gemini_exc, BrowserDriverError)
        else {"error": f"{type(gemini_exc).__name__}: {gemini_exc}"}
    )
    detail["chatgpt_error"] = str(chatgpt_exc)
    detail["gemini_fallback_attempted"] = True
    return detail


@app.post("/chatgpt/image")
async def chatgpt_image(payload: ImagePromptRequest) -> dict:
    """One-shot ChatGPT image generation via a normal chat conversation.

    Opens a new Chromium page, starts a non-temporary ChatGPT conversation,
    sends ``payload.prompt`` as an image-gen request, downloads the resulting
    image to ``payload.out_path``, deletes the conversation to avoid history
    clutter, and closes the page.
    """
    from playwright.async_api import async_playwright

    from video_agent.browser_worker.drivers import ChatGPTImageDriver

    safe_out = _safe_asset_path(payload.out_path)
    safe_out.parent.mkdir(parents=True, exist_ok=True)
    cdp_url = _cdp_url()

    async with async_playwright() as pw:
        browser = await _attach_cdp_or_503(pw, cdp_url)
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
                    aspect_ratio=payload.aspect_ratio,
                    attachment_path=_safe_attachment_path(payload.attachment_path),
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
                        aspect_ratio=payload.aspect_ratio,
                        attachment_path=_safe_attachment_path(payload.attachment_path),
                    )
                    return result
                except Exception as retry_exc:
                    if isinstance(retry_exc, LoginRequiredError):
                        raise HTTPException(
                            status_code=409,
                            detail=_driver_error_detail(retry_exc, login_required=True),
                        ) from retry_exc
                    # ChatGPT failed twice (initial + retry) — fall back to
                    # Gemini image gen (bug-511) instead of failing the Short.
                    print(
                        f"[browser] ChatGPT image gen failed twice ({retry_exc}); "
                        "falling back to Gemini...",
                        flush=True,
                    )
                    try:
                        result = await _generate_image_via_gemini(
                            context,
                            prompt=payload.prompt,
                            project_name=payload.project_name,
                            out_path=safe_out,
                            response_timeout_ms=payload.response_timeout_ms,
                            aspect_ratio=payload.aspect_ratio,
                        )
                        return result
                    except Exception as gemini_exc:
                        if isinstance(gemini_exc, LoginRequiredError):
                            raise HTTPException(
                                status_code=409,
                                detail=_driver_error_detail(gemini_exc, login_required=True),
                            ) from gemini_exc
                        raise HTTPException(
                            status_code=502,
                            detail=_gemini_fallback_error_detail(gemini_exc, retry_exc),
                        ) from gemini_exc
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
    aspect_ratio: str = "16:9"
    attachment_path: str | None = None  # persona/identity reference image (repo-confined)


async def _generate_images_via_gemini(
    context,
    *,
    prompts: list[str],
    project_name: str,
    out_paths: list[Path],
    response_timeout_ms: int,
    aspect_ratio: str,
) -> list[dict]:
    """Batch fallback: generate every prompt via Gemini, one page, sequentially."""
    from video_agent.browser_worker.drivers import GeminiImageDriver

    page = await context.new_page()
    try:
        await human_pause(page, min_ms=400, max_ms=900)
        driver = GeminiImageDriver(page)
        results = []
        for prompt, out_path in zip(prompts, out_paths, strict=True):
            results.append(
                await driver.generate_image(
                    prompt,
                    project_name=project_name,
                    out_path=out_path,
                    response_timeout_ms=response_timeout_ms,
                    aspect_ratio=aspect_ratio,
                )
            )
        return results
    finally:
        try:
            await page.close()
        except Exception:
            pass


@app.post("/chatgpt/image/batch")
async def chatgpt_image_batch(payload: BatchImagePromptRequest) -> dict:
    """Sequential ChatGPT image generation in one normal chat, then delete it."""
    from playwright.async_api import async_playwright

    from video_agent.browser_worker.drivers import ChatGPTImageDriver

    safe_out_paths = [_safe_asset_path(p) for p in payload.out_paths]
    for p in safe_out_paths:
        p.parent.mkdir(parents=True, exist_ok=True)

    cdp_url = _cdp_url()

    async with async_playwright() as pw:
        browser = await _attach_cdp_or_503(pw, cdp_url)
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
                    aspect_ratio=payload.aspect_ratio,
                    attachment_path=_safe_attachment_path(payload.attachment_path),
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
                        aspect_ratio=payload.aspect_ratio,
                    )
                    return {"ok": True, "results": results}
                except Exception as retry_exc:
                    if isinstance(retry_exc, LoginRequiredError):
                        raise HTTPException(
                            status_code=409,
                            detail=_driver_error_detail(retry_exc, login_required=True),
                        ) from retry_exc
                    # ChatGPT batch failed twice — fall back to Gemini for the
                    # whole batch (bug-511), rather than failing the Short.
                    print(
                        f"[browser] ChatGPT image batch failed twice ({retry_exc}); "
                        "falling back to Gemini...",
                        flush=True,
                    )
                    try:
                        results = await _generate_images_via_gemini(
                            context,
                            prompts=payload.prompts,
                            project_name=payload.project_name,
                            out_paths=safe_out_paths,
                            response_timeout_ms=payload.response_timeout_ms,
                            aspect_ratio=payload.aspect_ratio,
                        )
                        return {"ok": True, "results": results}
                    except Exception as gemini_exc:
                        if isinstance(gemini_exc, LoginRequiredError):
                            raise HTTPException(
                                status_code=409,
                                detail=_driver_error_detail(gemini_exc, login_required=True),
                            ) from gemini_exc
                        raise HTTPException(
                            status_code=502,
                            detail=_gemini_fallback_error_detail(gemini_exc, retry_exc),
                        ) from gemini_exc
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

# Auth/login cookies that MUST be preserved so clearing cookies does NOT log the
# user out of ChatGPT / Gemini. We drop only the non-auth bloat (the cookies that
# cause HTTP 431 / provider errors) and re-add these afterwards.
_AUTH_COOKIE_SUBSTRINGS = {
    # ChatGPT (NextAuth session + OpenAI device/session + Cloudflare clearance).
    "chatgpt": ("next-auth", "oai-did", "oai-sc", "cf_clearance", "_account"),
    # Google account / Gemini login cookies. The "SID" family covers SID, HSID,
    # SSID, APISID, SAPISID, __Secure-1PSID/3PSID, *PSIDTS, *SIDCC, LSID, OSID…
    "gemini": ("sid", "nid", "lsid", "__secure-", "__host-", "sapisid", "apisid"),
}


def _is_auth_cookie(site: str, name: str) -> bool:
    low = str(name or "").lower()
    return any(token in low for token in _AUTH_COOKIE_SUBSTRINGS.get(site, ()))


@app.delete("/auth/{site}/cookies", status_code=200)
async def auth_clear_cookies(site: str, preserve_session: bool = True) -> dict:
    """Clear cookies for ``site`` while PRESERVING the current login session.

    By default (``preserve_session=True``) only non-auth cookies are removed — the
    login/session cookies for ChatGPT and Gemini are kept, so the controlled
    browser stays signed in. This clears the bloat that causes HTTP 431 / provider
    errors without forcing a re-login. Pass ``preserve_session=false`` to wipe
    every cookie for the domain (full logout).
    """
    from playwright.async_api import async_playwright

    domain = _SITE_DOMAINS.get(site)
    if not domain:
        raise HTTPException(status_code=404, detail=f"Unsupported site: {site}")

    cdp_url = _cdp_url()
    try:
        async with async_playwright() as pw:
            browser = await _attach_cdp_or_503(pw, cdp_url)
            try:
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                before = await context.cookies()
                domain_cookies = [c for c in before if domain in c.get("domain", "")]
                before_count = len(domain_cookies)

                preserved: list[dict] = []
                if preserve_session:
                    preserved = [
                        c for c in domain_cookies
                        if _is_auth_cookie(site, c.get("name", ""))
                    ]

                # Wipe the domain, then re-add the auth cookies we want to keep.
                await context.clear_cookies(domain=f".{domain}")
                await context.clear_cookies(domain=domain)
                if preserved:
                    await context.add_cookies(preserved)

                after = await context.cookies()
                after_count = sum(1 for c in after if domain in c.get("domain", ""))
                return {
                    "ok": True,
                    "site": site,
                    "domain": domain,
                    "preserve_session": preserve_session,
                    "cleared": before_count - after_count,
                    "preserved": len(preserved),
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
        async with async_playwright() as pw:
            browser = await _attach_cdp_or_503(pw, cdp_url)
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
