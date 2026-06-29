"""Network-free unit tests for transcript_lab.fetcher.

Run:
    cd tools/transcript_lab
    python -m pytest tests/ -q
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fetcher import LANGUAGES, extract_video_id, parse_srt  # noqa: E402


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=30s",
        "https://www.youtube.com/watch?list=PL123&v=dQw4w9WgXcQ",
        "https://youtube.com/live/dQw4w9WgXcQ",
        "dQw4w9WgXcQ",
    ],
)
def test_extract_video_id_shapes(url: str) -> None:
    assert extract_video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize("bad", ["", "https://example.com", "not a url", "https://youtu.be/"])
def test_extract_video_id_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        extract_video_id(bad)


def test_language_priority_es_first() -> None:
    assert LANGUAGES[0] == "es"
    assert LANGUAGES == ["es", "en"]


SRT = """1
00:00:00,000 --> 00:00:02,000
Hola a todos

2
00:00:02,000 --> 00:00:04,500
bienvenidos al canal

3
00:00:04,500 --> 00:00:06,000
bienvenidos al canal
"""


def test_parse_srt_segments_and_dedup() -> None:
    segs = parse_srt(SRT)
    # third block is a duplicate of the second -> deduped
    assert [s.text for s in segs] == ["Hola a todos", "bienvenidos al canal"]
    assert segs[0].start == 0.0
    assert segs[1].start == 2.0


def test_parse_srt_strips_tags() -> None:
    srt = "1\n00:00:01,000 --> 00:00:02,000\n<b>texto</b> <i>limpio</i>\n"
    segs = parse_srt(srt)
    assert segs[0].text == "texto limpio"


def test_parse_srt_empty() -> None:
    assert parse_srt("") == []
