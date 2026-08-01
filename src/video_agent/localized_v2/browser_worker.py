from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request

from video_agent.browser_worker import app as shared_worker


def create_app(*, profile_root: Path, session_namespace: str) -> FastAPI:
    profile = profile_root.resolve()
    namespace = session_namespace.strip()
    if not namespace.startswith("localized-v2:"):
        raise ValueError("localized V2 browser worker requires a V2 session namespace")

    application = FastAPI(title="localized-v2-browser-worker", version="1.0.0")

    @application.get("/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "service": "localized-v2-browser-worker",
            "sessionNamespace": namespace,
            "profileRoot": str(profile),
        }

    @application.get("/runtime")
    async def runtime() -> dict:
        return await shared_worker.runtime()

    @application.post("/chatgpt/send")
    async def chatgpt_send(payload: shared_worker.SendPromptRequest) -> dict:
        return await shared_worker.chatgpt_send(payload)

    @application.post("/chatgpt/image")
    async def chatgpt_image(
        payload: shared_worker.ImagePromptRequest,
        request: Request,
    ) -> dict:
        return await shared_worker.chatgpt_image(payload, request)

    return application


_DEFAULT_PROFILE = (
    Path(__file__).resolve().parents[3]
    / "runtime"
    / "localized-v2"
    / "browser-profile"
)
app = create_app(
    profile_root=Path(
        os.environ.get("LOCALIZED_V2_BROWSER_PROFILE_ROOT", str(_DEFAULT_PROFILE))
    ),
    session_namespace=os.environ.get(
        "LOCALIZED_V2_SESSION_NAMESPACE", "localized-v2:en-us"
    ),
)
