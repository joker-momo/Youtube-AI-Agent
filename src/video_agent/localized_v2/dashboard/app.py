from __future__ import annotations

import hmac
import ipaddress
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from video_agent.localized_v2.dashboard.api import build_router
from video_agent.localized_v2.dashboard.service import DashboardError, DashboardService

STATIC_ROOT = Path(__file__).parent / "static"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    details: dict | None = None,
) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status_code)


def _apply_security_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_local_authority(authority: str) -> bool:
    try:
        parsed = urlsplit(f"//{authority}")
        return (
            parsed.hostname is not None
            and _is_loopback(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.path
        )
    except ValueError:
        return False


def _is_local_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme == "http"
            and parsed.hostname is not None
            and _is_loopback(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def create_app(
    service: DashboardService,
    *,
    bind_host: str,
    allowed_hosts: set[str],
    allowed_origins: set[str],
) -> FastAPI:
    if not _is_loopback(bind_host):
        raise ValueError("localized V2 dashboard must bind to a loopback address")
    if not allowed_hosts or not allowed_origins:
        raise ValueError("localized V2 dashboard requires explicit local host and origin sets")
    if not all(_is_local_authority(host) for host in allowed_hosts):
        raise ValueError("localized V2 allowed hosts must be explicit local authorities")
    if not all(_is_local_origin(origin) for origin in allowed_origins):
        raise ValueError("localized V2 allowed origins must be explicit local HTTP origins")

    app = FastAPI(
        title="Localized V2 Operator",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.csrf_token = secrets.token_urlsafe(32)
    app.state.allowed_hosts = frozenset(allowed_hosts)
    app.state.allowed_origins = frozenset(allowed_origins)

    @app.middleware("http")
    async def local_security_boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in request.app.state.allowed_hosts:
            return _apply_security_headers(
                _error(400, "INVALID_HOST", "Unexpected Host header.")
            )
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin", "")
            token = request.headers.get("x-csrf-token", "")
            if (
                origin not in request.app.state.allowed_origins
                or not token
                or not hmac.compare_digest(token, request.app.state.csrf_token)
            ):
                return _apply_security_headers(
                    _error(
                        403,
                        "CSRF_REJECTED",
                        "Mutation requires the local dashboard origin and CSRF token.",
                    )
                )
        response = await call_next(request)
        return _apply_security_headers(response)

    @app.exception_handler(DashboardError)
    async def dashboard_error(_request: Request, exc: DashboardError) -> JSONResponse:
        return _error(
            exc.status_code,
            exc.code,
            exc.message,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in error["loc"] if part != "body"),
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        return _error(
            422,
            "VALIDATION_ERROR",
            "The request did not match the localized V2 contract.",
            details={"fields": fields},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return _error(exc.status_code, code, "The requested V2 resource is unavailable.")

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _error(
            500,
            "INTERNAL_ERROR",
            "The localized V2 service could not complete the request.",
        )

    app.include_router(build_router(service))
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="localized-v2-static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_ROOT / "index.html", media_type="text/html")

    return app
