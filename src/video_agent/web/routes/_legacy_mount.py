from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter

from video_agent.web.routes import _legacy


def include_legacy_routes(router: APIRouter, predicate: Callable[[object], bool]) -> None:
    """Attach selected legacy route objects to a new router module.

    This keeps public route ownership split by module while avoiding a risky
    rewrite of route handler internals in one pass.
    """
    for route in _legacy.router.routes:
        if predicate(route):
            router.routes.append(route)

