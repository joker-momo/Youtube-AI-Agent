"""Routes for inspecting and editing the project ``.env`` file.

Previously these routes were mounted via the legacy filtered include. They now
live here directly and reuse helpers from ``video_agent.web.services.env_config``.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from video_agent.storage.atomic import atomic_write_text
from video_agent.web.services.env_config import (
    active_env_example_path,
    active_env_path,
    mask_env_content,
    require_env_editor,
)


router = APIRouter()


class EnvSaveRequest(BaseModel):
    content: str


@router.get("/config/env")
def get_env_config() -> dict:
    env_path_value = active_env_path()
    example_path = active_env_example_path()
    content = ""
    exists = env_path_value.exists()
    if exists:
        content = mask_env_content(env_path_value.read_text(encoding="utf-8"))
    return {
        "path": str(env_path_value),
        "exists": exists,
        "example_exists": example_path.exists(),
        "content": content,
    }


@router.post("/config/env")
def save_env_config(
    payload: EnvSaveRequest,
    x_admin_token: str | None = Header(default=None),
) -> dict:
    require_env_editor(x_admin_token)
    env_path_value = active_env_path()
    atomic_write_text(env_path_value, payload.content, encoding="utf-8")
    return {"ok": True, "path": str(env_path_value)}


@router.post("/config/env/bootstrap")
def bootstrap_env_config(x_admin_token: str | None = Header(default=None)) -> dict:
    require_env_editor(x_admin_token)
    env_path_value = active_env_path()
    example_path = active_env_example_path()
    if env_path_value.exists():
        return {"ok": True, "created": False, "reason": ".env already exists"}
    if not example_path.exists():
        raise HTTPException(status_code=404, detail=".env.example not found")
    atomic_write_text(env_path_value, example_path.read_text(encoding="utf-8"), encoding="utf-8")
    return {"ok": True, "created": True, "path": str(env_path_value)}
