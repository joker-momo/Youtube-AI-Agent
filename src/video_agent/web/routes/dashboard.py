"""Dashboard page route.

Extracted from ``_legacy.py`` — serves the main dashboard HTML.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_DASHBOARD_HTML_PATH = Path(__file__).resolve().parents[1] / "dashboard.html"


def _load_dashboard_html() -> str:
    return _DASHBOARD_HTML_PATH.read_text(encoding="utf-8")


@router.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return _load_dashboard_html()
