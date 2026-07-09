"""Deterministic text-presence QA gate over a poster's read-back text."""
from __future__ import annotations

import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def qa_poster(
    png_path: Path,
    plan: dict[str, Any],
    *,
    read_text_fn: Callable[[Path], str],
) -> dict[str, Any]:
    """Verify the poster contains the title + every item label (accent-insensitive)."""
    try:
        read = _norm(read_text_fn(Path(png_path)))
    except Exception as exc:
        return {"verdict": "qa_unavailable", "missing": [], "error": str(exc)}
    required: list[str] = []
    title = str(plan.get("title") or "").strip()
    if title:
        required.append(title)
    for it in plan.get("items") or []:
        if isinstance(it, dict) and str(it.get("label") or "").strip():
            required.append(str(it["label"]).strip())
    missing = [r for r in required if _norm(r) not in read]
    return {"verdict": "pass" if not missing else "fail", "missing": missing}
