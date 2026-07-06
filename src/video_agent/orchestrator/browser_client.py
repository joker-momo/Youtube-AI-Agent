from __future__ import annotations

import logging
import os

import httpx

_log = logging.getLogger(__name__)


class BrowserClientError(RuntimeError):
    """Raised when the browser-worker returns a non-2xx response."""

    def __init__(self, message: str, *, status_code: int, detail: object) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class LoginRequiredFromWorker(BrowserClientError):
    """Browser-worker reported the runtime profile is signed out."""


class BrowserClient:
    """Thin async HTTP client for the ``browser-worker`` FastAPI service.

    The orchestrator stages use this to drive ChatGPT and Gemini through
    the Browser Appliance. ``request_timeout`` is the baseline HTTP read
    timeout (seconds). Per-prompt calls extend that timeout beyond the
    worker's ``response_timeout_ms`` so the worker has time to return a
    structured error instead of the orchestrator dropping the connection.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        request_timeout: float = 300.0,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("BROWSER_WORKER_URL", "http://browser-worker:8001")
        ).rstrip("/")
        self.request_timeout = request_timeout

    def _timeout_for_response(self, response_timeout_ms: int) -> float:
        return max(self.request_timeout, response_timeout_ms / 1000.0 + 30.0)

    def _wrap_transport_error(self, op: str, exc: httpx.HTTPError) -> BrowserClientError:
        return BrowserClientError(
            f"browser-worker {op} request failed: {exc}",
            status_code=502,
            detail={"error": str(exc), "type": exc.__class__.__name__},
        )

    async def _post(self, op: str, *, json: object | None = None, timeout: float):
        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                return await http.post(f"{self.base_url}/{op}", json=json)
        except httpx.HTTPError as exc:
            raise self._wrap_transport_error(op, exc) from exc

    async def _delete(self, op: str, *, timeout: float = 30.0):
        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                return await http.delete(f"{self.base_url}/{op}")
        except httpx.HTTPError as exc:
            raise self._wrap_transport_error(op, exc) from exc

    async def chatgpt_send(
        self,
        prompt: str,
        *,
        response_timeout_ms: int = 300_000,
    ) -> str:
        return await self._send("chatgpt", prompt, response_timeout_ms)

    async def gemini_send(
        self,
        prompt: str,
        *,
        response_timeout_ms: int = 300_000,
    ) -> str:
        return await self._send("gemini", prompt, response_timeout_ms)

    async def generate_image(
        self,
        prompt: str,
        *,
        project_name: str,
        out_path: str,
        response_timeout_ms: int = 360_000,
        aspect_ratio: str = "16:9",
        attachment_path: str | None = None,
    ) -> dict:
        """Drive ChatGPT image generation via /chatgpt/image.

        Returns the worker's payload: ``{src, local_path, project_name, bytes}``.
        "Raise LoginRequiredFromWorker on signed-out profile or
        BrowserClientError on driver/HTTP failure.
        """
        body = {
            "prompt": prompt,
            "project_name": project_name,
            "out_path": out_path,
            "response_timeout_ms": response_timeout_ms,
            "aspect_ratio": aspect_ratio,
            "attachment_path": attachment_path,
        }
        response = await self._post(
            "chatgpt/image",
            json=body,
            timeout=self._timeout_for_response(response_timeout_ms),
        )
        if response.status_code in (200, 201):
            return response.json()
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        if response.status_code == 409 and isinstance(detail, dict) and detail.get(
            "login_required"
        ):
            raise LoginRequiredFromWorker(
                detail.get("error", "Login required"),
                status_code=response.status_code,
                detail=detail,
            )
        raise BrowserClientError(
            f"browser-worker /chatgpt/image returned HTTP {response.status_code}",
            status_code=response.status_code,
            detail=detail,
        )

    async def generate_images(
        self,
        prompts: list[str],
        *,
        project_name: str,
        out_paths: list[str],
        response_timeout_ms: int = 360_000,
        aspect_ratio: str = "16:9",
        attachment_path: str | None = None,
    ) -> dict:
        """Drive ChatGPT batch image generation via /chatgpt/image/batch.

        Returns the worker's payload: ``{ok: True, results: [...]}``.
        """
        body = {
            "prompts": prompts,
            "project_name": project_name,
            "out_paths": out_paths,
            "response_timeout_ms": response_timeout_ms,
            "aspect_ratio": aspect_ratio,
            "attachment_path": attachment_path,
        }
        response = await self._post(
            "chatgpt/image/batch",
            json=body,
            timeout=self._timeout_for_response(response_timeout_ms * len(prompts)),
        )
        if response.status_code in (200, 201):
            return response.json()
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        if response.status_code == 409 and isinstance(detail, dict) and detail.get(
            "login_required"
        ):
            raise LoginRequiredFromWorker(
                detail.get("error", "Login required"),
                status_code=response.status_code,
                detail=detail,
            )
        raise BrowserClientError(
            f"browser-worker /chatgpt/image/batch returned HTTP {response.status_code}",
            status_code=response.status_code,
            detail=detail,
        )

    async def _send(self, site: str, prompt: str, ms: int) -> str:
        response = await self._post(
            f"{site}/send",
            json={"prompt": prompt, "response_timeout_ms": ms},
            timeout=self._timeout_for_response(ms),
        )
        return self._unwrap(site, "send", response)

    def _unwrap(self, site: str, op: str, response) -> str:
        if response.status_code in (200, 201):
            return response.json()["raw_response"]
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        if response.status_code == 409 and isinstance(detail, dict) and detail.get(
            "login_required"
        ):
            raise LoginRequiredFromWorker(
                detail.get("error", "Login required"),
                status_code=response.status_code,
                detail=detail,
            )
        raise BrowserClientError(
            f"browser-worker {site}/{op} returned HTTP {response.status_code}",
            status_code=response.status_code,
            detail=detail,
        )

    # ------------------------------------------------------------------
    # Session lifecycle: one temp chat per stage, multiple sends allowed.
    # ------------------------------------------------------------------

    async def open_session(self, site: str) -> str:
        response = await self._post(f"{site}/sessions", timeout=self.request_timeout)
        if response.status_code in (200, 201):
            return response.json()["session_id"]
        # Reuse _unwrap's error handling.
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        if response.status_code == 409 and isinstance(detail, dict) and detail.get(
            "login_required"
        ):
            raise LoginRequiredFromWorker(
                detail.get("error", "Login required"),
                status_code=response.status_code,
                detail=detail,
            )
        raise BrowserClientError(
            f"browser-worker {site}/sessions returned HTTP {response.status_code}",
            status_code=response.status_code,
            detail=detail,
        )

    async def send_in_session(
        self,
        site: str,
        session_id: str,
        prompt: str,
        *,
        response_timeout_ms: int = 300_000,
    ) -> str:
        timeout = self._timeout_for_response(response_timeout_ms)
        response = await self._post(
            f"{site}/sessions/{session_id}/send",
            json={"prompt": prompt, "response_timeout_ms": response_timeout_ms},
            timeout=timeout,
        )
        return self._unwrap(site, f"sessions/{session_id}/send", response)

    async def close_session(self, site: str, session_id: str) -> None:
        response = await self._delete(f"{site}/sessions/{session_id}")
        if response.status_code in (200, 204, 404):
            return  # 404 = already closed; treat as success for idempotency
        raise BrowserClientError(
            f"browser-worker close session returned HTTP {response.status_code}",
            status_code=response.status_code,
            detail=response.text,
        )

    async def auth_clear_cookies(self, site: str) -> dict:
        """Clear all cookies for ``site`` in the persistent browser context.

        Used to recover from ChatGPT provider errors ("Something went wrong…")
        without touching the user's real browser data. The next ``chatgpt_send``
        re-navigates to a fresh temporary chat, so cookie reset + re-send is the
        primary provider-error recovery path."""
        response = await self._delete(f"auth/{site}/cookies")
        if response.status_code in (200, 204):
            try:
                return response.json()
            except Exception:
                return {"ok": True, "site": site}
        raise BrowserClientError(
            f"browser-worker clear cookies returned HTTP {response.status_code}",
            status_code=response.status_code,
            detail=response.text,
        )

    async def run_session(
        self,
        site: str,
        messages: list[str],
        *,
        response_timeout_ms: int = 300_000,
        attempts: int = 3,
    ) -> str:
        """Open a temp chat, send each ``messages`` in order, return last response.

        Always closes the session in a finally so a partial failure
        does not leak runtime tabs.

        Useful for one-shot stage routes. For multi-stage pipelines
        that should share a single temp chat across stages, use
        ``open_persistent_session`` instead.
        """
        if not messages:
            raise BrowserClientError(
                "run_session requires at least one message",
                status_code=400,
                detail={},
            )

        import asyncio
        last_exc: BrowserClientError | None = None
        for idx in range(attempts):
            session_id = None
            try:
                session_id = await self.open_session(site)
                last = ""
                for prompt in messages:
                    last = await self.send_in_session(
                        site,
                        session_id,
                        prompt,
                        response_timeout_ms=response_timeout_ms,
                    )
                return last
            except BrowserClientError as exc:
                last_exc = exc
                if exc.status_code < 500 or idx == attempts - 1:
                    raise
                _log.warning(
                    "run_session(%s) attempt %d/%d failed with HTTP %d: %s. Retrying...",
                    site, idx + 1, attempts, exc.status_code, exc
                )
                await asyncio.sleep(1.0 + idx * 0.5)
            finally:
                if session_id is not None:
                    try:
                        await self.close_session(site, session_id)
                    except Exception as exc:
                        _log.warning("close_session(%s, %s) failed: %s", site, session_id, exc)

    async def open_persistent_session(self, site: str):
        """Open a long-lived temp chat; return ``(sender, closer)``.

        ``sender(messages)`` sends each message into the same tab and
        returns the last assistant response. ``closer()`` releases the
        tab. The caller MUST invoke ``closer()`` in a finally so a
        partial failure never leaks the runtime page.

        This is what the V3 pipeline uses so all three ChatGPT stages
        (script_promote, scenes_promote, seo_promote) share one tab,
        and similarly for the three Gemini QA stages.
        """
        session_id = await self.open_session(site)

        async def sender(messages, *, response_timeout_ms: int = 300_000) -> str:
            if not messages:
                raise BrowserClientError(
                    "persistent sender requires at least one message",
                    status_code=400,
                    detail={},
                )
            last = ""
            for prompt in messages:
                last = await self.send_in_session(
                    site,
                    session_id,
                    prompt,
                    response_timeout_ms=response_timeout_ms,
                )
            return last

        async def closer() -> None:
            try:
                await self.close_session(site, session_id)
            except Exception as exc:
                _log.warning("close_session(%s, %s) failed: %s", site, session_id, exc)

        return sender, closer
