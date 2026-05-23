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
        }
        async with httpx.AsyncClient(
            timeout=self._timeout_for_response(response_timeout_ms)
        ) as http:
            response = await http.post(f"{self.base_url}/chatgpt/image", json=body)
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
    ) -> dict:
        """Drive ChatGPT batch image generation via /chatgpt/image/batch.

        Returns the worker's payload: ``{ok: True, results: [...]}``.
        """
        body = {
            "prompts": prompts,
            "project_name": project_name,
            "out_paths": out_paths,
            "response_timeout_ms": response_timeout_ms,
        }
        async with httpx.AsyncClient(
            timeout=self._timeout_for_response(response_timeout_ms * len(prompts))
        ) as http:
            response = await http.post(f"{self.base_url}/chatgpt/image/batch", json=body)
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
        async with httpx.AsyncClient(timeout=self._timeout_for_response(ms)) as http:
            response = await http.post(
                f"{self.base_url}/{site}/send",
                json={"prompt": prompt, "response_timeout_ms": ms},
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
        async with httpx.AsyncClient(timeout=self.request_timeout) as http:
            response = await http.post(f"{self.base_url}/{site}/sessions")
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
        async with httpx.AsyncClient(timeout=timeout) as http:
            response = await http.post(
                f"{self.base_url}/{site}/sessions/{session_id}/send",
                json={"prompt": prompt, "response_timeout_ms": response_timeout_ms},
            )
        return self._unwrap(site, f"sessions/{session_id}/send", response)

    async def close_session(self, site: str, session_id: str) -> None:
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.delete(
                f"{self.base_url}/{site}/sessions/{session_id}"
            )
        if response.status_code in (200, 204, 404):
            return  # 404 = already closed; treat as success for idempotency
        raise BrowserClientError(
            f"browser-worker close session returned HTTP {response.status_code}",
            status_code=response.status_code,
            detail=response.text,
        )

    # ------------------------------------------------------------------
    # vidIQ keyword scoring helpers.
    # ------------------------------------------------------------------

    async def vidiq_score(
        self,
        keyword: str,
        *,
        session_id: str,
        response_timeout_ms: int = 30_000,
    ) -> dict:
        """Score a single keyword via the vidIQ session."""
        timeout = self._timeout_for_response(response_timeout_ms)
        async with httpx.AsyncClient(timeout=timeout) as http:
            response = await http.post(
                f"{self.base_url}/vidiq/sessions/{session_id}/score",
                json={"keyword": keyword, "response_timeout_ms": response_timeout_ms},
            )
        if response.status_code in (200, 201):
            return response.json()
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise BrowserClientError(
            f"browser-worker /vidiq/score returned HTTP {response.status_code}",
            status_code=response.status_code,
            detail=detail,
        )

    async def vidiq_score_batch(
        self,
        keywords: list[str],
        *,
        session_id: str,
        response_timeout_ms: int = 60_000,
    ) -> list[dict]:
        """Score a list of keywords via the vidIQ session (sequential in worker)."""
        timeout = self._timeout_for_response(response_timeout_ms * len(keywords))
        async with httpx.AsyncClient(timeout=timeout) as http:
            response = await http.post(
                f"{self.base_url}/vidiq/sessions/{session_id}/score_batch",
                json={"keywords": keywords, "response_timeout_ms": response_timeout_ms},
            )
        if response.status_code in (200, 201):
            data = response.json()
            # Worker returns {"session_id": ..., "results": [...]}
            return data["results"] if isinstance(data, dict) and "results" in data else data
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise BrowserClientError(
            f"browser-worker /vidiq/score_batch returned HTTP {response.status_code}",
            status_code=response.status_code,
            detail=detail,
        )

    async def run_vidiq_scores(self, keywords: list[str]) -> list[dict]:
        """Open a vidIQ session, score all keywords, close. Return scores."""
        session_id = await self.open_session("vidiq")
        try:
            return await self.vidiq_score_batch(keywords, session_id=session_id)
        finally:
            try:
                await self.close_session("vidiq", session_id)
            except Exception as exc:
                _log.warning("close_session(vidiq, %s) failed: %s", session_id, exc)

    async def run_session(
        self,
        site: str,
        messages: list[str],
        *,
        response_timeout_ms: int = 300_000,
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
        session_id = await self.open_session(site)
        try:
            last = ""
            for prompt in messages:
                last = await self.send_in_session(
                    site,
                    session_id,
                    prompt,
                    response_timeout_ms=response_timeout_ms,
                )
            return last
        finally:
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
