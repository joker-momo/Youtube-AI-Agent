"""Tests for SEO description normalization preserving YouTube chapter timestamps."""

from __future__ import annotations

from video_agent.operator import _normalize_youtube_description


def test_normalize_youtube_description_preserves_timestamp_lines():
    raw = (
        "Dormir mejor después de los 45 puede empezar antes.\n\n"
        "00:00 - La noche empieza antes\n"
        "01:30 - Activadores de la tarde\n"
        "03:00 - Cena que acompaña\n\n"
        "Suscríbete al canal."
    )
    normalized = _normalize_youtube_description(raw)
    assert (
        "00:00 - La noche empieza antes\n"
        "01:30 - Activadores de la tarde\n"
        "03:00 - Cena que acompaña"
        in normalized
    )
    # Old bug: timestamps got collapsed into one line.
    assert "La noche empieza antes 01:30" not in normalized


def test_normalize_youtube_description_handles_windows_newlines():
    raw = "Intro paragraph.\r\n\r\n00:00 - Inicio\r\n01:30 - Tema\r\n\r\nCierre."
    normalized = _normalize_youtube_description(raw)
    assert "\r" not in normalized
    assert "00:00 - Inicio\n01:30 - Tema" in normalized


def test_normalize_youtube_description_collapses_internal_whitespace_inside_paragraph():
    raw = "Esta   frase   tiene    espacios   extra."
    normalized = _normalize_youtube_description(raw)
    assert normalized.strip() == "Esta frase tiene espacios extra."


def test_normalize_youtube_description_caps_blank_lines_between_sections():
    raw = "Primera sección.\n\n\n\n\nSegunda sección."
    normalized = _normalize_youtube_description(raw)
    # Max one blank line between paragraphs.
    assert "\n\n\n" not in normalized
    assert "Primera sección.\n\nSegunda sección." in normalized


def test_normalize_youtube_description_terminates_with_newline():
    raw = "Single paragraph without trailing newline"
    normalized = _normalize_youtube_description(raw)
    assert normalized.endswith("\n")


def test_normalize_youtube_description_handles_empty_input():
    assert _normalize_youtube_description("") == "\n"


def test_normalize_youtube_description_strips_leading_trailing_whitespace_per_line():
    raw = "   00:00 - Intro   \n   01:30 - Tema   "
    normalized = _normalize_youtube_description(raw)
    assert "00:00 - Intro\n01:30 - Tema" in normalized
