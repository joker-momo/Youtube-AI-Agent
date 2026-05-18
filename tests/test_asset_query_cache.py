from __future__ import annotations

from datetime import datetime, timedelta, timezone

from video_agent.assets.query_cache import QueryCache, normalize_query, query_cache_key


def test_normalize_query_removes_stop_words_and_sorts_terms():
    assert normalize_query("The calm woman in a kitchen, with morning light!") == "calm kitchen light morning woman"
    assert normalize_query("morning kitchen calm woman light") == "calm kitchen light morning woman"


def test_query_cache_returns_unexpired_response_and_tracks_hit_count(tmp_path):
    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    cache = QueryCache(tmp_path / "query_cache.db", now=lambda: now)
    filters = {"orientation": "landscape", "per_page": 5}

    cache.set("pexels", "Calm Woman Kitchen", filters, {"photos": [{"id": 123}]})

    assert cache.get("pexels", "kitchen calm woman", filters) == {"photos": [{"id": 123}]}
    row = cache.get_entry(query_cache_key("pexels", "Calm Woman Kitchen", filters))
    assert row is not None
    assert row["hit_count"] == 1


def test_query_cache_ignores_expired_response(tmp_path):
    current = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    cache = QueryCache(tmp_path / "query_cache.db", now=lambda: current)
    filters = {"orientation": "landscape"}
    cache.set("pixabay", "sleep wellness", filters, {"hits": [{"id": 1}]}, ttl_hours=1)

    current = current + timedelta(hours=2)

    assert cache.get("pixabay", "sleep wellness", filters) is None
