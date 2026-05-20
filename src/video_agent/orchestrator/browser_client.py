from __future__ import annotations

import os

import httpx


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
    the Browser Appliance. ``request_timeout`` is the outer HTTP read
    timeout (seconds); the inner per-prompt timeout is passed to the
    worker as ``response_timeout_ms`` so the worker's Playwright wait
    matches the HTTP wait.
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

    async def chatgpt_send(
        self,
        prompt: str,
        *,
        response_timeout_ms: int = 180_000,
    ) -> str:
        return await self._send("chatgpt", prompt, response_timeout_ms)

    async def gemini_send(
        self,
        prompt: str,
        *,
        response_timeout_ms: int = 180_000,
    ) -> str:
        return await self._send("gemini", prompt, response_timeout_ms)

    async def _send(self, site: str, prompt: str, ms: int) -> str:
        async with httpx.AsyncClient(timeout=self.request_timeout) as http:
            response = await http.post(
                f"{self.base_url}/{site}/send",
                json={"prompt": prompt, "response_timeout_ms": ms},
            )
        if response.status_code == 200:
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
            f"browser-worker {site}/send returned HTTP {response.status_code}",
            status_code=response.status_code,
            detail=detail,
        )
