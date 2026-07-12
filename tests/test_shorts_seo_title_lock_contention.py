"""Fail-closed parent-title lock regression (Codex review round 1, AC7).

The final check-and-write must NOT proceed without owning the parent
``.seo-title.lock``: a writer that cannot acquire the lock in the bound must
raise and leave no SEO artifact, so two builders can never each pass a stale
snapshot and publish the same title. These live in their own file so the
protected acceptance suite (tests/test_shorts_seo_title_uniqueness.py) is
untouched.
"""

from __future__ import annotations

import fcntl
import json
from pathlib import Path

import pytest

from video_agent.shorts import paths


def _cfg() -> dict:
    return {"audience": {"age_range": [45, 75]}}


def _plan(short_id: str = "candidate") -> dict:
    return {
        "short_id": short_id,
        "format": "warning_list",
        "title": "6 errores con la sal en tu cena",
        "viewer_pain": "Una cena aparentemente saludable acumula sal",
        "practical_payoff": "Detectar seis combinaciones con demasiada sal",
    }


def _script() -> dict:
    return {
        "hook": "Cena saludable, demasiada sal",
        "narration": "Seis errores concentran sal en una cena que parece saludable.",
        "cta": "Revisa tus combinaciones",
        "idea_contract": {"original_count": 6, "must_preserve_count": True},
    }


def _seo_payload(title: str) -> str:
    return json.dumps(
        {
            "title": title,
            "description": (
                "La sal se acumula en una cena aparentemente saludable. "
                "¿Qué combinación revisas primero? #salysalud #alimentacionsaludable"
            ),
            "hashtags": ["#salysalud", "#alimentacionsaludable"],
            "pinned_comment": "¿Qué revisas primero: el queso o las salsas?",
        }
    )


def test_final_write_fails_closed_when_parent_lock_is_held(tmp_path: Path, monkeypatch) -> None:
    from video_agent.shorts import short_seo_builder as builder

    # Shrink the bound so the contended acquire fails fast instead of blocking
    # the test for the full production timeout.
    monkeypatch.setattr(builder, "_TITLE_LOCK_TIMEOUT_SEC", 0.2)

    lock_path = paths.shorts_dir(tmp_path) / ".seo-title.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock_path, "a+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        # A competing holder owns the lock: the builder must refuse to enter the
        # final check/write and must leave NO SEO artifact behind.
        with pytest.raises((TimeoutError, RuntimeError)):
            builder.build_short_seo(
                tmp_path, "candidate", _plan(), _script(), _cfg(),
                lambda prompt: _seo_payload("¡Error al cenar con demasiada sal!"),
            )
        seo_path = paths.short_json_dir(tmp_path, "candidate") / paths.SHORT_SEO_FILE
        assert not seo_path.exists()
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()

    # After the holder releases, the same build succeeds and writes the artifact.
    seo = builder.build_short_seo(
        tmp_path, "candidate", _plan(), _script(), _cfg(),
        lambda prompt: _seo_payload("¡Error al cenar con demasiada sal!"),
    )
    assert seo["title"] == "¡Error al cenar con demasiada sal!"
    assert (paths.short_json_dir(tmp_path, "candidate") / paths.SHORT_SEO_FILE).exists()


def test_two_same_title_competitors_yield_at_most_one_artifact(tmp_path: Path) -> None:
    from video_agent.shorts.short_seo_builder import build_short_seo

    title = "¡Error al cenar con demasiada sal!"

    # First builder publishes the title.
    build_short_seo(
        tmp_path, "candidate-1", _plan("candidate-1"), _script(), _cfg(),
        lambda prompt: _seo_payload(title),
    )
    # Second builder, same parent, keeps emitting the SAME title: the uniqueness
    # gate must exhaust and fail loudly — never a second artifact with that title.
    with pytest.raises(ValueError, match="(?i)(unique|duplicate|similar)"):
        build_short_seo(
            tmp_path, "candidate-2", _plan("candidate-2"), _script(), _cfg(),
            lambda prompt: _seo_payload(title),
        )

    published = [
        json.loads((p / "json" / paths.SHORT_SEO_FILE).read_text())["title"]
        for p in (tmp_path / "shorts").iterdir()
        if (p / "json" / paths.SHORT_SEO_FILE).exists()
    ]
    assert published.count(title) == 1
