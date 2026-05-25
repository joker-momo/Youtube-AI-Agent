"""Shared helpers for the /config/env routes.

Extracted from ``video_agent.web.routes._legacy`` so the route module no longer
owns env-editor business logic. The helpers honor the same ``ENABLE_ENV_EDITOR``
and ``ADMIN_TOKEN`` gates the legacy implementation used.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import HTTPException

from video_agent.contracts import repo_root


_SECRET_KEY_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE", "SESSION", "API")


def env_path() -> Path:
    return repo_root() / ".env"


def env_example_path() -> Path:
    return repo_root() / ".env.example"


def _compat_app_helper(name: str, fallback):
    """Defer to a monkey-patched override on ``video_agent.web.app`` if present.

    Some tests reach in and replace ``_env_path`` / ``_env_example_path`` on the
    app module to redirect reads/writes into ``tmp_path``. This shim preserves
    that hook even though the canonical helpers now live here.
    """
    app_module = sys.modules.get("video_agent.web.app")
    helper = getattr(app_module, name, None) if app_module is not None else None
    if helper is not None and helper is not fallback:
        return helper
    return fallback


def active_env_path() -> Path:
    return _compat_app_helper("_env_path", env_path)()


def active_env_example_path() -> Path:
    return _compat_app_helper("_env_example_path", env_example_path)()


def env_editor_enabled() -> bool:
    return os.environ.get("ENABLE_ENV_EDITOR", "").strip().lower() == "true"


def require_env_editor(x_admin_token: str | None) -> None:
    if not env_editor_enabled():
        raise HTTPException(status_code=403, detail="Environment editor is disabled.")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if expected and x_admin_token != expected:
        raise HTTPException(status_code=403, detail="Invalid admin token.")


def mask_env_value(key: str, value: str) -> str:
    if not any(marker in key.upper() for marker in _SECRET_KEY_MARKERS):
        return value
    if not value:
        return ""
    suffix = value[-4:] if len(value) >= 4 else value
    return f"********{suffix}"


def mask_env_content(content: str) -> str:
    lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            lines.append(line)
            continue
        key, value = line.split("=", 1)
        lines.append(f"{key}={mask_env_value(key.strip(), value)}")
    return "\n".join(lines) + ("\n" if content.endswith("\n") else "")
