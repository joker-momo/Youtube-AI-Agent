"""Tests for server-side YouTube chapter timestamp recomputation."""

from __future__ import annotations

from video_agent.operator import (
    _compute_chapter_timestamps,
    _normalize_seo_candidate,
    _rewrite_description_chapters,
)


def _scene(idx: int, duration: float, on_screen_text: str = "", narration: str = "") -> dict:
    return {
        "id": f"scene-{idx:02d}",
        "duration_sec": duration,
        "on_screen_text": on_screen_text,
        "narration": narration,
    }


def _scene_doc(total: float, scenes: list[dict]) -> dict:
    return {"total_duration_sec": total, "scenes": scenes}


def test_compute_chapter_timestamps_first_chapter_is_zero():
    doc = _scene_doc(120, [_scene(i, 12, f"Topic {i}") for i in range(1, 11)])
    chapters = _compute_chapter_timestamps(doc, None)
    assert chapters[0][0] == "00:00"


def test_compute_chapter_timestamps_never_exceeds_total_duration():
    total = 493  # 8 min 13 s — matches the real bug report
    doc = _scene_doc(total, [_scene(i, total / 48, f"Topic {i}") for i in range(1, 49)])

    chapters = _compute_chapter_timestamps(doc, None)
    for ts, _ in chapters:
        m, s = ts.split(":")
        seconds = int(m) * 60 + int(s)
        assert seconds <= total, f"chapter {ts} exceeds total duration {total}"


def test_compute_chapter_timestamps_uses_script_sections_when_available():
    doc = _scene_doc(600, [_scene(i, 60, f"Scene {i}") for i in range(1, 11)])
    script = {
        "sections": [
            {"title": "Introducción"},
            {"title": "Activadores de la tarde"},
            {"title": "Cena ligera"},
            {"title": "Pendientes"},
            {"title": "Cierre"},
        ]
    }
    chapters = _compute_chapter_timestamps(doc, script)
    titles = [t for _, t in chapters]
    assert "Introducción" in titles
    assert "Cena ligera" in titles


def test_compute_chapter_timestamps_handles_missing_durations():
    chapters = _compute_chapter_timestamps({"scenes": []}, None)
    assert chapters == []
    chapters = _compute_chapter_timestamps(None, None)
    assert chapters == []


def test_rewrite_description_chapters_replaces_inline_run():
    desc = (
        "Intro paragraph.\n\n"
        "00:00 - Wrong A 01:30 - Wrong B 03:00 - Wrong C 13:20 - Way too far\n\n"
        "Suscríbete."
    )
    chapters = [("00:00", "Inicio"), ("01:30", "Activadores"), ("04:00", "Cierre")]
    out = _rewrite_description_chapters(desc, chapters)
    assert "00:00 - Inicio" in out
    assert "01:30 - Activadores" in out
    assert "04:00 - Cierre" in out
    # Old fabricated timestamps must be gone.
    assert "13:20" not in out
    # Each chapter on its own line.
    assert "00:00 - Inicio\n01:30 - Activadores\n04:00 - Cierre" in out


def test_normalize_seo_candidate_replaces_fabricated_timestamps_with_real_ones():
    # Simulates the production bug: 9-minute video, ChatGPT wrote 13:20 chapter.
    scenes = []
    cursor = 0.0
    total = 540.0  # exactly 9 minutes
    for i in range(1, 49):
        scenes.append(_scene(i, total / 48, f"Tema {i}"))
        cursor += total / 48
    doc = _scene_doc(total, scenes)

    candidate = {
        "title": "Cómo dormir mejor",
        "description": (
            "Dormir mejor puede empezar antes.\n\n"
            "00:00 - Intro 01:30 - Activadores 13:20 - Plan mínimo\n\n"
            "Suscríbete."
        ),
        "tags": ["a"],
        "language": "es-ES",
        "ai_disclosure": True,
        "thumbnail_path": "thumbnail.jpg",
    }
    normalized = _normalize_seo_candidate(candidate, scene_doc=doc)
    desc = normalized["description"]
    # Real total = 540s = 09:00 — no chapter may exceed that.
    for token in desc.split():
        if ":" in token and token.replace(":", "").isdigit():
            m, s = token.split(":")
            assert int(m) * 60 + int(s) <= total, f"chapter {token} exceeds {total}"
    # Each chapter line must stand on its own row (preserves YouTube parsing).
    chapter_lines = [line for line in desc.splitlines() if line.strip().startswith(("00:", "01:", "02:", "03:", "04:", "05:", "06:", "07:", "08:", "09:"))]
    assert len(chapter_lines) >= 3
    # Bug fingerprint string must be gone.
    assert "13:20" not in desc


def test_normalize_seo_candidate_without_scene_doc_keeps_existing_chapters():
    """Backward compatibility: callers that don't pass scenes still get plain normalization."""
    candidate = {
        "title": "Sin escenas",
        "description": "Intro.\n\n00:00 - A\n01:30 - B\n\nCTA.",
        "tags": ["a"],
        "language": "es-ES",
        "ai_disclosure": True,
        "thumbnail_path": "thumbnail.jpg",
    }
    normalized = _normalize_seo_candidate(candidate)  # scene_doc defaults to None
    assert "00:00 - A" in normalized["description"]
    assert "01:30 - B" in normalized["description"]
