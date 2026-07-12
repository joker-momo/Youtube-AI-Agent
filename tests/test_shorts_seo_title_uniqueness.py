from __future__ import annotations

import json
from pathlib import Path

import pytest

LIVE_DUPLICATE_A = "Si tienes más de 45, revisa tu sal"
LIVE_DUPLICATE_B = "Si tienes más de 45, revisa la sal"


def _write_sibling(job: Path, short_id: str, title: str) -> None:
    seo_dir = job / "shorts" / short_id / "json"
    seo_dir.mkdir(parents=True, exist_ok=True)
    (seo_dir / "short_seo.json").write_text(
        json.dumps({"short_id": short_id, "title": title}), encoding="utf-8"
    )


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
        "narration": "Seis errores pueden concentrar sal en una cena que parece saludable.",
        "cta": "Revisa tus combinaciones",
        "idea_contract": {"original_count": 6, "must_preserve_count": True},
    }


def _seo_payload(title: str) -> str:
    return json.dumps(
        {
            "title": title,
            "description": (
                "La sal puede acumularse en una cena aparentemente saludable. "
                "¿Qué combinación revisas primero? #salysalud #alimentacionsaludable"
            ),
            "hashtags": ["#salysalud", "#alimentacionsaludable"],
            "pinned_comment": "¿Qué revisas primero: el queso o las salsas?",
        }
    )


def test_live_tu_sal_and_la_sal_titles_are_canonical_duplicates() -> None:
    from video_agent.shorts.short_seo_builder import (
        _normalize_title_for_uniqueness,
        _title_uniqueness_issues,
    )

    assert _normalize_title_for_uniqueness(LIVE_DUPLICATE_A) == (
        _normalize_title_for_uniqueness(LIVE_DUPLICATE_B)
    )
    issues = _title_uniqueness_issues(LIVE_DUPLICATE_B, [LIVE_DUPLICATE_A])
    assert issues
    assert LIVE_DUPLICATE_A in issues[0]


def test_meaningful_action_difference_is_not_a_duplicate() -> None:
    from video_agent.shorts.short_seo_builder import _title_uniqueness_issues

    assert _title_uniqueness_issues(
        "Si tienes más de 45, reduce la sal", [LIVE_DUPLICATE_A]
    ) == []


def test_sibling_discovery_is_parent_scoped_deterministic_and_excludes_current(
    tmp_path: Path,
) -> None:
    from video_agent.shorts.short_seo_builder import _collect_sibling_short_titles

    parent = tmp_path / "parent-a"
    _write_sibling(parent, "short-b", "¿Café por la tarde? La verdad científica")
    _write_sibling(parent, "short-a", LIVE_DUPLICATE_A)
    _write_sibling(parent, "current", "Título propio anterior")
    malformed = parent / "shorts" / "broken" / "json"
    malformed.mkdir(parents=True)
    (malformed / "short_seo.json").write_text("not-json", encoding="utf-8")
    other = tmp_path / "parent-b"
    _write_sibling(other, "short-z", "No pertenece al parent A")

    assert _collect_sibling_short_titles(parent, current_short_id="current") == [
        LIVE_DUPLICATE_A,
        "¿Café por la tarde? La verdad científica",
    ]


def test_prompt_lists_used_titles_and_forbids_cosmetic_paraphrases() -> None:
    from video_agent.shorts.prompts import short_seo_prompt

    prompt = short_seo_prompt(
        _cfg(), _plan(), _script(), used_titles=[LIVE_DUPLICATE_A, LIVE_DUPLICATE_B]
    )
    low = prompt.lower()
    assert LIVE_DUPLICATE_A in prompt
    assert LIVE_DUPLICATE_B in prompt
    assert "used" in low or "already" in low
    assert "cosmetic" in low or "near-duplicate" in low


def test_builder_retries_duplicate_and_persists_unique_second_title(tmp_path: Path) -> None:
    from video_agent.shorts.short_seo_builder import build_short_seo

    _write_sibling(tmp_path, "existing", LIVE_DUPLICATE_A)
    prompts: list[str] = []

    def llm_fn(prompt: str) -> str:
        prompts.append(prompt)
        if len(prompts) == 1:
            return _seo_payload(LIVE_DUPLICATE_B)
        return _seo_payload("¡Error al cenar con demasiada sal!")

    seo = build_short_seo(tmp_path, "candidate", _plan(), _script(), _cfg(), llm_fn)

    assert seo["title"] == "¡Error al cenar con demasiada sal!"
    assert len(prompts) == 2
    assert LIVE_DUPLICATE_A in prompts[1]
    assert "duplicate" in prompts[1].lower() or "similar" in prompts[1].lower()


def test_stubborn_duplicate_is_never_published_after_retry_exhaustion(tmp_path: Path) -> None:
    from video_agent.shorts.short_seo_builder import build_short_seo

    _write_sibling(tmp_path, "existing", LIVE_DUPLICATE_A)

    with pytest.raises(ValueError, match="(?i)(unique|duplicate|similar)"):
        build_short_seo(
            tmp_path,
            "candidate",
            _plan(),
            _script(),
            _cfg(),
            lambda prompt: _seo_payload(LIVE_DUPLICATE_B),
        )


def test_builder_refreshes_siblings_before_persisting_to_close_stale_snapshot(
    tmp_path: Path,
) -> None:
    from video_agent.shorts.short_seo_builder import build_short_seo

    calls = 0

    def llm_fn(prompt: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            racing_title = "¡Error al cenar con demasiada sal!"
            # Simulate another Short finishing SEO after this attempt's initial
            # sibling read but before the candidate is persisted.
            _write_sibling(tmp_path, "racing-sibling", racing_title)
            return _seo_payload(racing_title)
        return _seo_payload("¡Error al combinar seis fuentes de sal!")

    seo = build_short_seo(tmp_path, "candidate", _plan(), _script(), _cfg(), llm_fn)

    assert calls == 2
    assert seo["title"] == "¡Error al combinar seis fuentes de sal!"


def test_rerender_excludes_its_own_previous_title(tmp_path: Path) -> None:
    from video_agent.shorts.short_seo_builder import build_short_seo

    _write_sibling(tmp_path, "candidate", LIVE_DUPLICATE_A)
    seo = build_short_seo(
        tmp_path,
        "candidate",
        {**_plan(), "title": "Audita la sal en cinco pasos"},
        {**_script(), "hook": "Revisa tu sal"},
        _cfg(),
        lambda prompt: _seo_payload(LIVE_DUPLICATE_A),
    )
    assert seo["title"] == LIVE_DUPLICATE_A
