"""Channel-agnostic visual DNA loader and length classification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def default_visual_dna() -> dict[str, Any]:
    """Minimal fallback so callers can always rely on the standard structure."""
    return {
        "schema_version": "5.0",
        "source_policy": {
            "external_stock_providers": ["pexels"],
            "allow_other_external_stock_providers": False,
        },
        "query_policy": {
            "pexels_query_language": "en",
            "max_query_terms": 14,
            "max_queries_per_scene": 4,
            "max_api_queries_per_scene": 2,
            "dedupe_queries": True,
        },
        "token_policy": {
            "preserve_numeric_terms": [],
            "preserve_short_terms": [],
            "stopwords": {"en": [], "es": []},
        },
        "synonyms": {"tokens": {}, "phrases": {}},
        "role_keywords": {"es": {}, "en": {}},
        "video_length_profiles": {
            "short": {
                "max_scenes": 24,
                "min_distinct_visual_buckets": 3,
                "min_distinct_shot_types": 3,
                "min_local_graphic_cards": 0,
            },
            "long": {
                "min_scenes": 25,
                "min_distinct_visual_buckets": 6,
                "min_distinct_shot_types": 5,
                "min_local_graphic_cards": 4,
            },
        },
        "visual_buckets": {
            "persona_moment": {"weight": 1.0, "keyword_triggers": {"es": [], "en": []}, "pexels_queries_en": []},
        },
        "shot_types": {},
        "negative_patterns": {"strong_phrases_en": [], "weak_terms_en": []},
        "role_to_buckets": {},
        "default_bucket_sequence": ["persona_moment"],
        "default_shot_sequence": ["medium"],
    }


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"visual-dna file {path} did not contain a mapping")
    return data


def load_visual_dna(channel_config: dict, channel_id: str, repo_root: Path | None = None) -> dict[str, Any]:
    """Resolve visual-dna.yaml from explicit path, channel fallback, or built-in default."""
    visuals = channel_config.get("visuals", {}) if channel_config else {}
    explicit = visuals.get("visual_dna_path") if isinstance(visuals, dict) else None
    root = Path(repo_root) if repo_root else Path.cwd()

    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = root / path
        if path.exists():
            return _load_yaml(path)

    fallback = root / "configs" / channel_id / "visual-dna.yaml"
    if fallback.exists():
        return _load_yaml(fallback)

    return default_visual_dna()


def classify_video_length(scene_count: int, visual_dna: dict[str, Any]) -> str:
    """Return 'short' or 'long' based on the short profile's max_scenes."""
    short_max = int(visual_dna.get("video_length_profiles", {}).get("short", {}).get("max_scenes", 24))
    return "short" if scene_count <= short_max else "long"
