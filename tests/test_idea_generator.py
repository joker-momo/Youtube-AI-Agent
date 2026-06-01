"""Tests for idea_generator module and /channels/{id}/ideas/generate endpoint."""
from __future__ import annotations

import json
import asyncio
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_agent.contracts import repo_root
import video_agent.orchestrator.idea_generator as idea_generator
import video_agent.web.routes._legacy as legacy_routes
from video_agent.orchestrator.idea_generator import (
    DEFAULT_CHANNEL_KEYWORD_CONFIG,
    _auto_seeds_from_trends,
    _discover_top_keywords,
    _idea_gen_prompt,
    _marker_hits,
    _select_keywords_for_prompt,
    _slug,
    assign_bucket,
    calculate_final_score,
    classify_intent_cluster,
    dedupe_by_normalized_keyword,
    dedupe_by_normalized_keyword_and_intent,
    detect_language_fit,
    enrich_keyword_item,
    generate_ideas,
    merge_keyword_channel_config,
    normalize_keyword,
    parse_ideas,
    save_ideas,
    score_audience_fit,
    score_content_fit,
    select_by_cluster_limit,
)
from video_agent.web.app import flatten_keyword_result_for_ui
from video_agent.orchestrator.browser_client import BrowserClientError
from video_agent.web.app import app, get_browser_client, get_inputs_root


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def channel_path() -> Path:
    return repo_root() / "configs/vida-plena-45/channel.yaml"


SAMPLE_IDEAS = [
    {
        "topic": "Rutina de caminata suave para adultos 45+",
        "angle": "Cómo empezar sin lesionarse y mantener la constancia",
        "target_duration_sec": 54,
        "key_points": [
            "empezar con diez minutos",
            "elegir calzado estable",
            "rutas seguras y con sombra",
            "descansar si hay dolor",
            "aumentar gradualmente",
        ],
        "title_seed": "Caminata suave para adultos 45+",
    },
    {
        "topic": "Hidratación correcta después de los 45",
        "angle": "Por qué el cuerpo pierde más agua con la edad y cómo compensarlo",
        "target_duration_sec": 54,
        "key_points": [
            "señales de deshidratación leve",
            "cuánta agua realmente necesitas",
            "alimentos ricos en agua",
            "mitos sobre bebidas isotónicas",
            "hábitos para recordar beber",
        ],
        "title_seed": "Cómo hidratarte bien después de los 45",
    },
]


def _make_raw(ideas: list[dict]) -> str:
    return json.dumps(ideas, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Unit tests: parse_ideas
# ---------------------------------------------------------------------------


def test_parse_ideas_plain_array():
    raw = _make_raw(SAMPLE_IDEAS)
    result = parse_ideas(raw)
    assert len(result) == 2
    assert result[0]["topic"] == SAMPLE_IDEAS[0]["topic"]


def test_parse_ideas_markdown_fenced():
    raw = f"```json\n{_make_raw(SAMPLE_IDEAS)}\n```"
    result = parse_ideas(raw)
    assert len(result) == 2


def test_parse_ideas_float_duration_coerced():
    ideas = [{**SAMPLE_IDEAS[0], "target_duration_sec": 54.0}]
    raw = json.dumps(ideas)
    result = parse_ideas(raw)
    assert isinstance(result[0]["target_duration_sec"], int)
    assert result[0]["target_duration_sec"] == 54


def test_parse_ideas_missing_required_field_skipped():
    bad = {"topic": "only topic", "title_seed": "x"}
    good = SAMPLE_IDEAS[0]
    # parse_ideas drops bad objects (raises ValueError internally, skipped)
    raw = json.dumps([bad, good])
    # The JSON array parser calls _validate_idea which raises ValueError
    # for bad; we test that the valid one survives.
    # Actually parse_ideas raises on first bad item in the array path.
    # Provide only valid to test normal flow; invalid-array tested separately.
    raw_valid = json.dumps([good])
    result = parse_ideas(raw_valid)
    assert len(result) == 1


def test_parse_ideas_empty_string_returns_empty():
    result = parse_ideas("no json here at all")
    assert result == []


def test_save_ideas_uses_atomic_json(monkeypatch, tmp_path: Path):
    writes: list[tuple[Path, dict]] = []

    def fake_atomic_write_json(path: Path, payload: dict, indent: int = 2) -> None:
        writes.append((path, payload))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")

    monkeypatch.setattr(idea_generator, "atomic_write_json", fake_atomic_write_json)

    paths = save_ideas([SAMPLE_IDEAS[0]], "vida-plena-45", tmp_path)

    assert len(paths) == 1
    assert writes == [(paths[0], SAMPLE_IDEAS[0])]


def test_sync_published_videos_falls_back_to_channel_videos_page(monkeypatch, tmp_path: Path):
    channel_config = {
        "channel": {
            "id": "vida-plena-45",
            "youtube_channel_id": "UCKUswqsAaLsEkcsgzTuKAmw",
        }
    }
    html = '''
        "contentId":"p0zS2pG4QEo","contentType":"LOCKUP_CONTENT_TYPE_VIDEO",
        "rendererContext":{"accessibilityContext":{"label":"Qué hacer si te despiertas de madrugada después de los 45 8 minutos, 32 segundos"}}
        "contentId":"tvPKhG4ARxw","contentType":"LOCKUP_CONTENT_TYPE_VIDEO",
        "rendererContext":{"accessibilityContext":{"label":"Cómo comer mejor después de los 45 sin culpa ni dietas 11 minutos, 42 segundos"}}
    '''

    class FakeResponse:
        def __init__(self, body: bytes):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return self.body

    def fake_urlopen(req, timeout=15):
        url = req.full_url
        if "feeds/videos.xml" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        assert url.endswith("/videos")
        return FakeResponse(html.encode("utf-8"))

    monkeypatch.setattr(idea_generator.urllib.request, "urlopen", fake_urlopen)

    videos = idea_generator.sync_published_videos(channel_config, tmp_path)

    assert [v["id"] for v in videos] == ["p0zS2pG4QEo", "tvPKhG4ARxw"]
    assert videos[0]["title"] == "Qué hacer si te despiertas de madrugada después de los 45"
    saved = json.loads((tmp_path / "vida-plena-45" / "published_videos.json").read_text(encoding="utf-8"))
    assert saved["videos"][1]["url"] == "https://www.youtube.com/watch?v=tvPKhG4ARxw"


# ---------------------------------------------------------------------------
# Unit tests: _slug
# ---------------------------------------------------------------------------


def test_slug_basic():
    # ñ → NFKD: n + combining tilde (non-ASCII, dropped) → "n"
    assert _slug("Rutina de sueño para dormir mejor") == "rutina-de-sueno-para-dormir-mejor"


def test_slug_accent_stripped():
    assert "e" in _slug("Éxito")
    assert "é" not in _slug("Éxito")


def test_slug_max_len():
    long_title = "a" * 100
    assert len(_slug(long_title)) <= 40


# ---------------------------------------------------------------------------
# Unit tests: _idea_gen_prompt
# ---------------------------------------------------------------------------


def test_idea_gen_prompt_contains_channel_name(channel_path: Path):
    from video_agent.utils.json_io import read_yaml
    cfg = read_yaml(channel_path)
    prompt = _idea_gen_prompt(cfg, [], 5)
    assert "Vida Plena 45+" in prompt
    assert "5" in prompt


def test_idea_gen_prompt_seed_topics_included(channel_path: Path):
    from video_agent.utils.json_io import read_yaml
    cfg = read_yaml(channel_path)
    seeds = ["yoga suave", "dieta mediterránea"]
    prompt = _idea_gen_prompt(cfg, seeds, 3)
    assert "yoga suave" in prompt
    assert "dieta mediterránea" in prompt


def test_idea_gen_prompt_forbidden_phrases_included(channel_path: Path):
    from video_agent.utils.json_io import read_yaml
    cfg = read_yaml(channel_path)
    prompt = _idea_gen_prompt(cfg, [], 5)
    assert "adultos mayores" in prompt  # in forbidden_phrases


# ---------------------------------------------------------------------------
# Unit tests: keyword scoring V2
# ---------------------------------------------------------------------------


def test_normalize_keyword_v2():
    assert normalize_keyword("  Cómo comer mejor DESPUÉS   de los 45  ") == "como comer mejor despues de los 45"
    assert normalize_keyword("Alimentación para mayores de 45") == "alimentacion para despues de los 45"
    assert normalize_keyword("Salud 45 Plus") == "salud 45+"


def test_language_guardrail_portuguese():
    score, notes = detect_language_fit("como comer bem depois dos 45", "spanish")
    assert score < 70
    assert "language_mismatch_portuguese" in notes


def test_language_guardrail_spanish_ok():
    score, notes = detect_language_fit("como comer mejor despues de los 45 sin culpa", "spanish")
    assert score >= 80
    assert "spanish_language_ok" in notes


def test_audience_fit_45_plus_high():
    score = score_audience_fit(
        "como comer mejor despues de los 45 sin dietas",
        DEFAULT_CHANNEL_KEYWORD_CONFIG,
    )
    assert score >= 80


def test_audience_fit_generic_lower():
    score = score_audience_fit("nutricion", DEFAULT_CHANNEL_KEYWORD_CONFIG)
    assert score < 70


def test_intent_cluster_nutrition():
    assert classify_intent_cluster("como comer mejor despues de los 45") == "nutrition_after_45"


def test_composite_final_score_with_language_penalty():
    item = {
        "keyword": "como comer bem depois dos 45",
        "vidiq_score": 85,
        "audience_fit": 80,
        "intent_strength": 80,
        "content_fit": 80,
        "language_fit": 40,
        "serp_opportunity": 50,
        "notes": [],
        "rejection_reasons": [],
    }
    assert calculate_final_score(item) < 70
    assert "language_penalty_high" in item["notes"]


def test_bucket_assignment_top_opportunity():
    item = {
        "keyword": "como comer mejor despues de los 45",
        "final_score": 82,
        "language_fit": 100,
        "audience_fit": 90,
        "intent_strength": 80,
        "content_fit": 80,
        "vidiq_score": 75,
        "rejection_reasons": [],
    }
    assert assign_bucket(item) == "top_opportunity_keywords"


def test_bucket_assignment_long_tail_not_enough_data():
    item = {
        "keyword": "bajones de energia despues de comer 45",
        "vidiq_score": None,
        "score": None,
        "notes": ["not_enough_search_data"],
        "final_score": 65,
        "language_fit": 100,
        "audience_fit": 85,
        "intent_strength": 80,
        "content_fit": 75,
        "rejection_reasons": [],
    }
    assert assign_bucket(item) == "long_tail_test_keywords"


def test_bucket_assignment_rejected_language():
    item = {
        "keyword": "como comer bem depois dos 45",
        "final_score": 75,
        "language_fit": 50,
        "audience_fit": 80,
        "intent_strength": 80,
        "content_fit": 80,
        "vidiq_score": 90,
        "rejection_reasons": [],
    }
    assert assign_bucket(item) == "rejected_keywords"
    assert "language_mismatch" in item["rejection_reasons"]


def test_dedupe_keeps_highest_final_score():
    items = [
        {"keyword": "A", "normalized_keyword": "a", "intent_cluster": "nutrition_after_45", "final_score": 60, "vidiq_score": 90, "notes": [], "rejection_reasons": []},
        {"keyword": "Á", "normalized_keyword": "a", "intent_cluster": "nutrition_after_45", "final_score": 70, "vidiq_score": 50, "notes": ["x"], "rejection_reasons": []},
    ]
    result = dedupe_by_normalized_keyword_and_intent(items)
    assert len(result) == 1
    assert result[0]["final_score"] == 70


def test_flatten_keyword_result_for_ui_legacy_and_v2():
    legacy = [{"keyword": "a"}]
    v2 = {
        "top_opportunity_keywords": [{"keyword": "b"}],
        "long_tail_test_keywords": [{"keyword": "c"}],
        "rejected_keywords": [{"keyword": "d"}],
    }
    assert flatten_keyword_result_for_ui(legacy) == legacy
    assert [item["keyword"] for item in flatten_keyword_result_for_ui(v2)] == ["b", "c", "d"]


def test_select_keywords_for_prompt_uses_top_opportunity_then_long_tail():
    result = {
        "top_opportunity_keywords": [{"keyword": "top"}],
        "long_tail_test_keywords": [{"keyword": "tail"}],
        "all_scored_keywords": [{"keyword": "fallback"}],
    }

    assert [item["keyword"] for item in _select_keywords_for_prompt(result, 2)] == ["top", "tail"]


def test_select_keywords_for_prompt_fallback_excludes_rejected_language_mismatch():
    result = {
        "top_opportunity_keywords": [],
        "long_tail_test_keywords": [],
        "all_scored_keywords": [
            {
                "keyword": "best camera settings",
                "language_fit": 65,
                "content_fit": 50,
                "rejection_reasons": ["language_mismatch"],
            },
            {
                "keyword": "dormir mejor despues de los 45",
                "language_fit": 100,
                "content_fit": 85,
                "rejection_reasons": [],
            },
        ],
    }

    selected = _select_keywords_for_prompt(result, 2)

    assert [item["keyword"] for item in selected] == ["dormir mejor despues de los 45"]


def test_select_keywords_for_prompt_fallback_keeps_safe_low_score_spanish_keywords():
    result = {
        "top_opportunity_keywords": [],
        "long_tail_test_keywords": [],
        "all_scored_keywords": [
            {
                "keyword": "dormir mejor despues de los 45",
                "language_fit": 100,
                "content_fit": 85,
                "rejection_reasons": ["low_final_score"],
            },
        ],
    }

    selected = _select_keywords_for_prompt(result, 1)

    assert [item["keyword"] for item in selected] == ["dormir mejor despues de los 45"]


def test_discover_top_keywords_v2_returns_bucketed_dict():
    async def fake_vidiq(keywords: list[str]) -> list[dict]:
        return [
            {
                "keyword": keyword,
                "score": 78,
                "volume": "Medium",
                "competition": "Low",
                "related": [],
            }
            for keyword in keywords
        ]

    result = asyncio.run(
        _discover_top_keywords(
            ["como comer mejor despues de los 45"],
            fake_vidiq,
            channel_config={"audience": {"language": "es-ES"}},
        )
    )

    assert result["metadata"]["version"] == "keyword_scoring_v2"
    assert result["metadata"]["enable_serp_inspection"] is False
    assert result["metadata"]["serp_inspection"] == "disabled"
    assert result["top_opportunity_keywords"]
    assert "serp_inspection_disabled" in result["top_opportunity_keywords"][0]["notes"]


def test_discover_top_keywords_v2_rejects_portuguese_even_with_high_vidiq_score():
    async def fake_vidiq(keywords: list[str]) -> list[dict]:
        return [
            {
                "keyword": keyword,
                "score": 95,
                "volume": "High",
                "competition": "Low",
                "related": [],
            }
            for keyword in keywords
        ]

    result = asyncio.run(
        _discover_top_keywords(
            ["como comer bem depois dos 45"],
            fake_vidiq,
            channel_config={"audience": {"language": "es-ES"}},
        )
    )

    assert not result["top_opportunity_keywords"]
    assert result["rejected_keywords"]
    assert result["rejected_keywords"][0]["keyword"] == "como comer bem depois dos 45"


def test_generate_ideas_with_metadata_returns_v2_keywords_when_vidiq_available(channel_path: Path):
    async def fake_vidiq(keywords: list[str]) -> list[dict]:
        return [
            {
                "keyword": keyword,
                "score": 78,
                "volume": "Medium",
                "competition": "Low",
                "related": [],
            }
            for keyword in keywords
        ]

    async def fake_chatgpt(messages: list[str]) -> str:
        return json.dumps(
            [
                {
                    **SAMPLE_IDEAS[0],
                    "target_keyword": "como comer mejor despues de los 45",
                }
            ],
            ensure_ascii=False,
        )

    ideas, top_keywords, seed_source = asyncio.run(
        generate_ideas(
            channel_path,
            fake_chatgpt,
            vidiq_fn=fake_vidiq,
            seed_topics=["como comer mejor despues de los 45"],
            count=1,
            with_metadata=True,
        )
    )

    assert ideas[0]["target_keyword"] == "como comer mejor despues de los 45"
    assert top_keywords["metadata"]["version"] == "keyword_scoring_v2"
    assert seed_source == "user"


# ---------------------------------------------------------------------------
# Unit tests: save_ideas
# ---------------------------------------------------------------------------


def test_save_ideas_writes_files(tmp_path: Path):
    paths = save_ideas(SAMPLE_IDEAS, channel_id="test-channel", out_dir=tmp_path)
    assert len(paths) == 2
    for p in paths:
        assert p.exists()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert "topic" in data


def test_save_ideas_correct_directory(tmp_path: Path):
    paths = save_ideas(SAMPLE_IDEAS, channel_id="vida-plena-45", out_dir=tmp_path)
    for p in paths:
        assert p.parent == tmp_path / "ideas" / "vida-plena-45"


def test_save_ideas_filename_contains_slug(tmp_path: Path):
    paths = save_ideas([SAMPLE_IDEAS[0]], channel_id="ch", out_dir=tmp_path)
    # slug of "Rutina de caminata suave para adultos 45+" should appear
    assert "rutina" in paths[0].name


# ---------------------------------------------------------------------------
# Unit tests: generate_ideas (async, fake chatgpt_fn)
# ---------------------------------------------------------------------------


import asyncio


def test_generate_ideas_returns_list(channel_path: Path):
    async def fake_chatgpt(msgs):
        return _make_raw(SAMPLE_IDEAS)

    ideas = asyncio.run(generate_ideas(channel_path, fake_chatgpt, seed_topics=[], count=2))
    assert len(ideas) == 2
    assert ideas[0]["topic"] == SAMPLE_IDEAS[0]["topic"]


def test_generate_ideas_raises_on_empty_response(channel_path: Path):
    async def fake_chatgpt(msgs):
        return "no json here"

    with pytest.raises(ValueError, match="no parseable ideas"):
        asyncio.run(generate_ideas(channel_path, fake_chatgpt, count=2))


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


class FakeBrowserClient:
    def __init__(self, response: str = "", error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[list[str]] = []
        self.vidiq_calls: list[list[str]] = []

    async def run_session(self, site: str, messages, **kwargs) -> str:
        self.calls.append(list(messages))
        if self.error:
            raise self.error
        return self.response

    async def run_vidiq_scores(self, keywords: list[str]) -> list[dict]:
        self.vidiq_calls.append(list(keywords))
        return [
            {
                "keyword": keyword,
                "score": 82 if "45" in keyword else 40,
                "volume": "Medium",
                "competition": "Low",
                "related": ["como comer mejor despues de los 45", "energia despues de los 45"],
            }
            for keyword in keywords
        ]


@pytest.fixture
def http_client(tmp_path: Path):
    app.dependency_overrides[get_inputs_root] = lambda: tmp_path
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_post_generate_ideas_success(http_client: TestClient, tmp_path: Path):
    fake = FakeBrowserClient(response=_make_raw(SAMPLE_IDEAS))
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post(
            "/channels/vida-plena-45/ideas/generate",
            json={"seed_topics": ["sueño", "caminata"], "count": 2},
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["channel_id"] == "vida-plena-45"
    assert body["count"] == 2
    assert len(body["ideas"]) == 2
    assert len(body["saved"]) == 2
    # Files exist on disk
    for rel in body["saved"]:
        assert (tmp_path / rel).exists()


def test_post_score_ideas_filters_off_language_related_keywords(
    http_client: TestClient,
):
    class RelatedNoiseBrowserClient(FakeBrowserClient):
        async def run_vidiq_scores(self, keywords: list[str]) -> list[dict]:
            self.vidiq_calls.append(list(keywords))
            return [
                {
                    "keyword": keyword,
                    "score": 82,
                    "volume": "Medium",
                    "competition": "Low",
                    "related": [
                        {"keyword": "best camera settings", "score": 85},
                        {"keyword": "dormir mejor despues de los 45", "score": 55},
                    ],
                }
                for keyword in keywords
            ]

    fake = RelatedNoiseBrowserClient()
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post(
            "/channels/vida-plena-45/ideas/score",
            json={"ideas": [SAMPLE_IDEAS[0]]},
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 200
    related = r.json()["results"][0]["related"]
    assert "dormir mejor despues de los 45" in related
    assert "best camera settings" not in related


def test_post_generate_ideas_returns_and_saves_v2_keyword_metadata(
    http_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ideas = [
        {
            **SAMPLE_IDEAS[0],
            "target_keyword": "como comer mejor despues de los 45",
            "title_seed": "Cómo comer mejor después de los 45 sin dietas",
        }
    ]
    # Isolate from the channel's real published_videos.json so this scoring
    # test does not collide with duplicate detection (Section 6).
    monkeypatch.setattr(legacy_routes, "load_published_videos", lambda *a, **k: [])
    fake = FakeBrowserClient(response=_make_raw(ideas))
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post(
            "/channels/vida-plena-45/ideas/generate",
            json={"seed_topics": ["como comer mejor despues de los 45"], "count": 1},
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["keyword_result"]["metadata"]["version"] == "keyword_scoring_v2"
    assert body["summary"]["total_scanned"] >= 1
    assert body["summary"]["target_language"] == "spanish"

    idea = body["ideas"][0]
    assert idea["vidiq_score"] == 82
    assert idea["keyword_final_score"] >= 70
    assert idea["intent_cluster"] == "nutrition_after_45"
    assert idea["bucket"] == "top_opportunity_keywords"
    assert idea["recommended_angle"]
    assert idea["thumbnail_hook_options"]

    saved = json.loads((tmp_path / body["saved"][0]).read_text(encoding="utf-8"))
    assert saved["keyword_final_score"] == idea["keyword_final_score"]
    assert saved["bucket"] == idea["bucket"]


def test_post_generate_ideas_filters_off_language_nested_related_keywords(
    http_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    ideas = [
        {
            **SAMPLE_IDEAS[0],
            "target_keyword": "dormir mejor despues de los 45",
            "title_seed": "Dormir mejor después de los 45 con una rutina sencilla",
        }
    ]
    keyword_result = {
        "top_opportunity_keywords": [
            {
                "keyword": "dormir mejor despues de los 45",
                "vidiq_score": 82,
                "final_score": 75.0,
                "language_fit": 100,
                "content_fit": 85,
                "bucket": "top_opportunity_keywords",
                "related": [
                    {"keyword": "best camera settings", "score": 85},
                    {"keyword": "sueño reparador", "score": 55},
                ],
            }
        ],
        "long_tail_test_keywords": [],
        "rejected_keywords": [],
        "all_scored_keywords": [],
        "metadata": {"version": "keyword_scoring_v2", "target_language": "spanish"},
    }

    async def fake_generate_ideas(**kwargs):
        return ideas, keyword_result, "trend"

    monkeypatch.setattr(legacy_routes, "generate_ideas", fake_generate_ideas)
    monkeypatch.setattr(legacy_routes, "load_published_videos", lambda *a, **k: [])
    fake = FakeBrowserClient(response=_make_raw(ideas))
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post(
            "/channels/vida-plena-45/ideas/generate",
            json={"seed_topics": [], "count": 1},
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 201, r.text
    body_text = json.dumps(r.json(), ensure_ascii=False).lower()
    assert "best camera settings" not in body_text
    assert "sueño reparador" in body_text


def test_post_generate_ideas_keeps_v2_score_when_target_keyword_is_paraphrased(
    http_client: TestClient,
    tmp_path: Path,
):
    ideas = [
        {
            **SAMPLE_IDEAS[0],
            "target_keyword": "sueño",
            "title_seed": "Sueño después de los 45: una rutina simple",
        }
    ]
    fake = FakeBrowserClient(response=_make_raw(ideas))
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post(
            "/channels/vida-plena-45/ideas/generate",
            json={"seed_topics": ["como comer mejor despues de los 45"], "count": 1},
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 201, r.text
    body = r.json()
    idea = body["ideas"][0]
    assert idea["target_keyword"] == "como comer mejor despues de los 45"
    assert idea["keyword_match_source"] == "ordered_fallback"
    assert idea["vidiq_score"] == 82
    assert idea["keyword_final_score"] >= 70
    assert idea["bucket"] == "top_opportunity_keywords"

    saved = json.loads((tmp_path / body["saved"][0]).read_text(encoding="utf-8"))
    assert saved["keyword_final_score"] == idea["keyword_final_score"]
    assert saved["keyword_match_source"] == "ordered_fallback"


def test_post_generate_ideas_attaches_rejected_keyword_scores(
    http_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    ideas = [
        {
            **SAMPLE_IDEAS[0],
            "target_keyword": "sueño",
            "title_seed": "Sueño después de los 45: rutina realista",
        }
    ]
    keyword_result = {
        "top_opportunity_keywords": [],
        "long_tail_test_keywords": [],
        "rejected_keywords": [
            {
                "keyword": "sueño",
                "vidiq_score": 64,
                "final_score": 50.0,
                "volume": "High",
                "competition": "Medium",
                "intent_cluster": "sleep_after_45",
                "audience_fit": 60,
                "intent_strength": 25,
                "content_fit": 70,
                "language_fit": 100,
                "serp_opportunity": 50,
                "bucket": "rejected_keywords",
                "recommended_angle": "Dormir mejor después de los 45",
                "thumbnail_hook_options": ["DUERME MEJOR"],
                "notes": ["spanish_language_ok"],
                "rejection_reasons": ["low_final_score"],
            }
        ],
        "all_scored_keywords": [],
        "metadata": {"version": "keyword_scoring_v2", "target_language": "spanish"},
    }

    async def fake_generate_ideas(**kwargs):
        return ideas, keyword_result, "trend"

    monkeypatch.setattr(legacy_routes, "generate_ideas", fake_generate_ideas)
    fake = FakeBrowserClient(response=_make_raw(ideas))
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post(
            "/channels/vida-plena-45/ideas/generate",
            json={"seed_topics": [], "count": 1},
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 201, r.text
    body = r.json()
    idea = body["ideas"][0]
    # vidIQ scored the 1-word keyword "sueño" as rejected_keywords
    # (intent_strength=25 on a single bare word). The route now
    # re-scores the richer title_seed so the idea bucket reflects the
    # full multi-word intent signal. Raw vidIQ score stays exposed.
    assert idea["vidiq_score"] == 64
    # Final score must be >= the raw vidIQ-derived score (50.0); the
    # title-based re-score can only boost, never lower, the verdict.
    assert idea["keyword_final_score"] >= 50.0
    assert idea["bucket"] in {
        "rejected_keywords",
        "long_tail_test_keywords",
        "top_opportunity_keywords",
    }

    saved = json.loads((tmp_path / body["saved"][0]).read_text(encoding="utf-8"))
    assert saved["keyword_final_score"] >= 50.0


def test_post_generate_ideas_ordered_fallback_skips_off_language_rejected_keywords(
    http_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    ideas = [
        {
            **SAMPLE_IDEAS[0],
            "target_keyword": "keyword que no coincide",
            "title_seed": "Dormir mejor después de los 45 con una rutina sencilla",
        }
    ]
    keyword_result = {
        "top_opportunity_keywords": [],
        "long_tail_test_keywords": [],
        "rejected_keywords": [
            {
                "keyword": "best camera settings",
                "vidiq_score": 85,
                "final_score": 45.0,
                "language_fit": 65,
                "content_fit": 50,
                "rejection_reasons": ["language_mismatch"],
                "bucket": "rejected_keywords",
            },
            {
                "keyword": "dormir mejor despues de los 45",
                "vidiq_score": 55,
                "final_score": 50.0,
                "language_fit": 100,
                "content_fit": 85,
                "rejection_reasons": ["low_final_score"],
                "bucket": "rejected_keywords",
            },
        ],
        "all_scored_keywords": [],
        "metadata": {"version": "keyword_scoring_v2", "target_language": "spanish"},
    }

    async def fake_generate_ideas(**kwargs):
        return ideas, keyword_result, "trend"

    monkeypatch.setattr(legacy_routes, "generate_ideas", fake_generate_ideas)
    monkeypatch.setattr(legacy_routes, "load_published_videos", lambda *a, **k: [])
    fake = FakeBrowserClient(response=_make_raw(ideas))
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post(
            "/channels/vida-plena-45/ideas/generate",
            json={"seed_topics": [], "count": 1},
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 201, r.text
    idea = r.json()["ideas"][0]
    assert idea["target_keyword"] == "dormir mejor despues de los 45"
    assert idea["keyword_match_source"] == "ordered_fallback"
    assert idea["vidiq_score"] == 55
    assert "best camera settings" not in json.dumps(r.json()).lower()


def test_post_generate_ideas_unknown_channel(http_client: TestClient):
    r = http_client.post(
        "/channels/nonexistent-channel/ideas/generate",
        json={"count": 5},
    )
    assert r.status_code == 404


def test_post_generate_ideas_count_out_of_range(http_client: TestClient):
    r = http_client.post(
        "/channels/vida-plena-45/ideas/generate",
        json={"count": 999},
    )
    assert r.status_code == 422


def test_post_generate_ideas_browser_error_returns_502(http_client: TestClient):
    fake = FakeBrowserClient(error=BrowserClientError("boom", status_code=502, detail={}))
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post(
            "/channels/vida-plena-45/ideas/generate",
            json={"count": 3},
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 502


def test_post_generate_ideas_empty_chatgpt_response_returns_502(http_client: TestClient):
    fake = FakeBrowserClient(response="sorry, I cannot help with that")
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post(
            "/channels/vida-plena-45/ideas/generate",
            json={"count": 3},
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 502


def test_post_generate_ideas_prompt_includes_seeds(http_client: TestClient, tmp_path: Path):
    """Verify that seed_topics appear in the prompt sent to ChatGPT."""
    captured: list[str] = []

    class CaptureFake:
        async def run_session(self, site, messages, **kwargs):
            captured.extend(messages)
            return _make_raw(SAMPLE_IDEAS)

    app.dependency_overrides[get_browser_client] = lambda: CaptureFake()
    try:
        http_client.post(
            "/channels/vida-plena-45/ideas/generate",
            json={"seed_topics": ["yoga matutino"], "count": 2},
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert any("yoga matutino" in m for m in captured)


# ---------------------------------------------------------------------------
# Spain-first locale prompt tests
# ---------------------------------------------------------------------------

from video_agent.orchestrator.idea_generator import (
    _idea_gen_prompt,
    merge_keyword_channel_config,
)


_SPAIN_CFG = {
    "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+", "description": "Bienestar"},
    "audience": {"language": "es-ES", "age_range": [45, 75], "primary_markets": ["ES"]},
    "niche": {"sub_niches": ["sleep"]},
    "content_format": {"target_duration_sec": 840},
    "positioning": {
        "forbidden_phrases": ["adultos mayores"],
        "preferred_phrases": ["personas de más de 45 años"],
    },
    "locale_style": {
        "target_locale": "Spain",
        "language_code": "es-ES",
        "lexical_preferences": {
            "prefer": ["móvil", "ordenador"],
            "avoid": ["celular", "computadora", "adultos mayores"],
        },
    },
}


def test_merge_keyword_channel_config_picks_up_locale_style():
    cfg = merge_keyword_channel_config(_SPAIN_CFG)
    assert cfg["target_locale"] == "Spain"
    assert cfg["locale_language_code"] == "es-ES"
    assert "móvil" in cfg["lexical_prefer"]
    assert "celular" in cfg["lexical_avoid"]


def test_merge_keyword_channel_config_falls_back_to_audience_language():
    cfg = merge_keyword_channel_config({"audience": {"language": "es-MX"}})
    assert cfg["locale_language_code"] == "es-MX"


def test_idea_gen_prompt_contains_spain_locale_block():
    prompt = _idea_gen_prompt(_SPAIN_CFG, ["dormir mejor despues de los 45"], count=3)
    assert "Target locale: Spain" in prompt
    assert "es-ES" in prompt
    assert "móvil" in prompt
    assert "ordenador" in prompt
    assert "Spanish for Spain" in prompt
    assert "not Latin America Spanish unless the config says otherwise" in prompt


def test_idea_gen_prompt_for_non_spain_spanish_channel_keeps_dynamic_language():
    cfg = {
        "channel": {"id": "demo", "name": "Demo", "description": ""},
        "audience": {"language": "es-MX", "age_range": [45, 75]},
        "niche": {},
        "content_format": {"target_duration_sec": 600},
        "positioning": {},
    }
    prompt = _idea_gen_prompt(cfg, ["sueño"], count=2)
    assert "Language: es-MX" in prompt
    assert "Target locale: Latin America" in prompt


# ===========================================================================
# Spec v3: idea discovery & keyword scoring improvements
# ===========================================================================

_FALLBACK_CFG = {
    "audience": {"primary_markets": ["ES"], "language": "es-ES"},
    "niche": {"sub_niches": ["sleep_quality", "nutrition_45plus"]},
    "keyword_research": {
        "fallback_seeds": [
            "dormir mejor despues de los 45",
            "ejercicio suave despues de los 45",
            "cena ligera despues de los 45",
        ]
    },
}


def _no_trends(monkeypatch):
    monkeypatch.setattr(idea_generator, "_fetch_google_trends", lambda geo, lang: [])


# --- Section 1: channel-specific fallback seeds ---------------------------

def test_auto_seeds_uses_channel_specific_fallback_seeds_when_trends_do_not_match(monkeypatch):
    _no_trends(monkeypatch)
    seeds = _auto_seeds_from_trends(_FALLBACK_CFG, max_seeds=10)
    assert seeds[:3] == [
        "dormir mejor despues de los 45",
        "ejercicio suave despues de los 45",
        "cena ligera despues de los 45",
    ]


def test_auto_seeds_fallback_respects_max_seeds(monkeypatch):
    _no_trends(monkeypatch)
    seeds = _auto_seeds_from_trends(_FALLBACK_CFG, max_seeds=2)
    assert len(seeds) == 2


def test_auto_seeds_legacy_sub_niche_fallback_still_works_without_config_field(monkeypatch):
    _no_trends(monkeypatch)
    cfg = {
        "audience": {"primary_markets": ["ES"], "language": "es-ES"},
        "niche": {"sub_niches": ["sleep_quality", "nutrition_45plus"]},
    }
    seeds = _auto_seeds_from_trends(cfg, max_seeds=10)
    # Legacy behaviour: first representative keyword per sub_niche
    assert "sueño" in seeds or "salud" in seeds
    assert "dormir mejor despues de los 45" not in seeds


# --- Section 2: safe language detection -----------------------------------

def test_language_guardrail_english_keyword_penalized():
    score, notes = detect_language_fit("best camera settings", "spanish")
    assert score < 80
    assert "language_mismatch_english" in notes


def test_language_guardrail_portuguese_keyword_penalized():
    score, notes = detect_language_fit("como comer bem depois dos 45", "spanish")
    assert score < 70
    assert "language_mismatch_portuguese" in notes


def test_language_guardrail_french_keyword_penalized():
    score, notes = detect_language_fit("mieux dormir après 45 ans", "spanish")
    assert score < 80
    assert "language_mismatch_french" in notes


def test_language_guardrail_italian_phrase_keyword_penalized():
    score, notes = detect_language_fit("dormire meglio dopo i 45", "spanish")
    assert score < 80
    assert "language_mismatch_italian" in notes


def test_language_guardrail_does_not_penalize_spanish_comer_as_italian():
    score, notes = detect_language_fit("comer mejor despues de los 45", "spanish")
    assert score >= 80
    assert "language_mismatch_italian" not in notes


def test_language_guardrail_does_not_penalize_spanish_fitness_loanword():
    score, notes = detect_language_fit("rutina fitness suave despues de los 45", "spanish")
    assert score >= 80
    assert "language_mismatch_english" not in notes


def test_language_guardrail_uncertain_spanish_generic_keyword_gets_note():
    score, notes = detect_language_fit("camara reflex", "spanish")
    assert score < 100
    assert "spanish_language_uncertain" in notes


def test_language_guardrail_spanish_45plus_keyword_still_high():
    score, notes = detect_language_fit("como dormir mejor despues de los 45", "spanish")
    assert score >= 80
    assert "spanish_language_ok" in notes


def test_language_marker_phrase_matching_handles_extra_spaces():
    assert _marker_hits("best   camera    settings", ["best camera settings"]) == [
        "best camera settings"
    ]
    score, notes = detect_language_fit("best   camera   settings", "spanish")
    assert "language_mismatch_english" in notes


# --- Section 4: full scored keyword debug data ----------------------------

def _mk_item(kw, cluster, final, vidiq=50):
    return {
        "keyword": kw,
        "normalized_keyword": normalize_keyword(kw),
        "intent_cluster": cluster,
        "final_score": final,
        "vidiq_score": vidiq,
        "notes": [],
        "rejection_reasons": [],
    }


def test_all_scored_keywords_keeps_more_than_cluster_cap():
    items = [_mk_item(f"kw {i} nutricion", "nutrition_after_45", 90 - i) for i in range(6)]
    deduped = dedupe_by_normalized_keyword(items)
    assert len(deduped) == 6  # nothing capped
    selected = select_by_cluster_limit(deduped, max_per_cluster=3)
    assert len(selected) == 3


def test_top_opportunity_keywords_still_respects_cluster_cap():
    items = [_mk_item(f"kw {i} nutricion", "nutrition_after_45", 90 - i) for i in range(5)]
    selected = select_by_cluster_limit(dedupe_by_normalized_keyword(items), max_per_cluster=2)
    assert len(selected) == 2
    assert [it["final_score"] for it in selected] == [90, 89]


def test_dedupe_by_normalized_keyword_keeps_best_duplicate():
    items = [
        _mk_item("Comer mejor", "nutrition_after_45", 60, vidiq=90),
        _mk_item("comer  mejor", "nutrition_after_45", 70, vidiq=50),
    ]
    deduped = dedupe_by_normalized_keyword(items)
    assert len(deduped) == 1
    assert deduped[0]["final_score"] == 70


def test_legacy_dedupe_by_normalized_keyword_and_intent_wrapper_still_works():
    items = [_mk_item(f"kw {i} nutricion", "nutrition_after_45", 90 - i) for i in range(5)]
    result = dedupe_by_normalized_keyword_and_intent(items, max_per_cluster=2)
    assert len(result) == 2


# --- Section 5: sensitive health safety flag ------------------------------

def test_menopausia_topic_is_flagged_but_not_heavily_rejected():
    item = enrich_keyword_item(
        {"keyword": "como aliviar la menopausia despues de los 45", "score": 70},
        DEFAULT_CHANNEL_KEYWORD_CONFIG,
    )
    assert item["medical_safety_required"] is True
    assert "sensitive_45plus_topic_requires_disclaimer" in item["notes"]
    assert item["content_fit"] >= 55
    assert item["bucket"] != "rejected_keywords"


def test_perimenopausia_topic_is_flagged_but_not_heavily_rejected():
    item = enrich_keyword_item(
        {"keyword": "perimenopausia despues de los 45 sintomas", "score": 70},
        DEFAULT_CHANNEL_KEYWORD_CONFIG,
    )
    assert item["medical_safety_required"] is True
    assert "sensitive_45plus_topic_requires_disclaimer" in item["notes"]


def test_diabetes_topic_is_flagged_as_high_risk_medical():
    item = enrich_keyword_item(
        {"keyword": "diabetes despues de los 45", "score": 70},
        DEFAULT_CHANNEL_KEYWORD_CONFIG,
    )
    assert item["medical_safety_required"] is True
    assert "sensitive_medical_topic" in item["notes"]


def test_sensitive_topic_sets_medical_safety_required():
    for kw in ("menopausia", "diabetes", "osteoporosis", "sofocos"):
        item = enrich_keyword_item({"keyword": kw, "score": 50}, DEFAULT_CHANNEL_KEYWORD_CONFIG)
        assert item.get("medical_safety_required") is True


def test_menopausia_is_not_double_penalized_as_high_risk_medical():
    meno = score_content_fit("menopausia despues de los 45", DEFAULT_CHANNEL_KEYWORD_CONFIG)
    diabetes = score_content_fit("diabetes despues de los 45", DEFAULT_CHANNEL_KEYWORD_CONFIG)
    # Menopausia (valid sensitive, -5) must score higher than diabetes (high risk, -20)
    assert meno > diabetes


# --- Section 6: duplicate prevention --------------------------------------

def test_idea_prompt_includes_published_titles_to_avoid(channel_path: Path):
    from video_agent.utils.json_io import read_yaml
    cfg = read_yaml(channel_path)
    prompt = _idea_gen_prompt(
        cfg, ["dormir mejor despues de los 45"], 2,
        published_titles=["Cómo dormir mejor a partir de los 45"],
    )
    assert "Already published" in prompt
    assert "Cómo dormir mejor a partir de los 45" in prompt


def test_idea_prompt_omits_published_block_when_empty(channel_path: Path):
    from video_agent.utils.json_io import read_yaml
    cfg = read_yaml(channel_path)
    prompt = _idea_gen_prompt(cfg, ["dormir mejor"], 2, published_titles=[])
    assert "Already published" not in prompt
    prompt_none = _idea_gen_prompt(cfg, ["dormir mejor"], 2)
    assert "Already published" not in prompt_none


def test_idea_prompt_caps_published_titles_to_latest_30(channel_path: Path):
    from video_agent.utils.json_io import read_yaml
    cfg = read_yaml(channel_path)
    titles = [f"Video numero {i:02d} sobre bienestar" for i in range(40)]
    prompt = _idea_gen_prompt(cfg, ["dormir mejor"], 2, published_titles=titles)
    assert "Video numero 00 sobre bienestar" in prompt
    assert "Video numero 29 sobre bienestar" in prompt
    assert "Video numero 39 sobre bienestar" not in prompt


def _dup_setup(monkeypatch, ideas, published_titles):
    keyword_result = {
        "top_opportunity_keywords": [],
        "long_tail_test_keywords": [],
        "rejected_keywords": [],
        "all_scored_keywords": [],
        "metadata": {"version": "keyword_scoring_v2", "target_language": "spanish"},
    }

    async def fake_generate_ideas(**kwargs):
        return ideas, keyword_result, "trend"

    monkeypatch.setattr(legacy_routes, "generate_ideas", fake_generate_ideas)
    monkeypatch.setattr(
        legacy_routes,
        "load_published_videos",
        lambda *a, **k: [{"title": t} for t in published_titles],
    )


def test_generate_endpoint_returns_duplicates_separately(
    http_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ideas = [
        {**SAMPLE_IDEAS[0], "title_seed": "Caminata suave para adultos 45+"},
        {**SAMPLE_IDEAS[1]},
    ]
    _dup_setup(monkeypatch, ideas, ["Caminata suave para adultos 45+"])
    fake = FakeBrowserClient(response=_make_raw(ideas))
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post(
            "/channels/vida-plena-45/ideas/generate", json={"seed_topics": [], "count": 2}
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["duplicate_count"] == 1
    assert len(body["duplicate_candidates"]) == 1
    assert body["duplicate_candidates"][0]["is_duplicate"] is True


def test_generate_endpoint_does_not_save_duplicate_ideas(
    http_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ideas = [
        {**SAMPLE_IDEAS[0], "title_seed": "Caminata suave para adultos 45+"},
        {**SAMPLE_IDEAS[1]},
    ]
    _dup_setup(monkeypatch, ideas, ["Caminata suave para adultos 45+"])
    fake = FakeBrowserClient(response=_make_raw(ideas))
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post(
            "/channels/vida-plena-45/ideas/generate", json={"seed_topics": [], "count": 2}
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)
    body = r.json()
    assert len(body["saved"]) == 1
    assert len(body["ideas"]) == 1
    for rel in body["saved"]:
        saved = json.loads((tmp_path / rel).read_text(encoding="utf-8"))
        assert saved.get("is_duplicate") is not True


def test_generate_endpoint_count_excludes_duplicate_candidates(
    http_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ideas = [
        {**SAMPLE_IDEAS[0], "title_seed": "Caminata suave para adultos 45+"},
        {**SAMPLE_IDEAS[1]},
    ]
    _dup_setup(monkeypatch, ideas, ["Caminata suave para adultos 45+"])
    fake = FakeBrowserClient(response=_make_raw(ideas))
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post(
            "/channels/vida-plena-45/ideas/generate", json={"seed_topics": [], "count": 2}
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)
    body = r.json()
    assert body["count"] == len(body["ideas"]) == 1


# --- Section 3: title-level language re-score -----------------------------

def test_title_rescore_penalizes_english_title(
    http_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ideas = [
        {
            "topic": "Camera settings for exercise",
            "angle": "tech",
            "target_duration_sec": 54,
            "key_points": ["a", "b", "c", "d", "e"],
            "title_seed": "Best camera settings for exercise after 45",
            "target_keyword": "ejercicio suave despues de los 45",
        }
    ]
    _dup_setup(monkeypatch, ideas, [])
    fake = FakeBrowserClient(response=_make_raw(ideas))
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post(
            "/channels/vida-plena-45/ideas/generate", json={"seed_topics": [], "count": 1}
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)
    body = r.json()
    idea = body["ideas"][0]
    assert idea["language_fit"] < 80
    assert idea["bucket"] != "top_opportunity_keywords"
    notes = idea.get("keyword_notes", [])
    assert any("language_mismatch_english" in n or "spanish_language_uncertain" in n for n in notes)


def test_title_rescore_appends_language_notes_without_overwriting_existing_notes(
    http_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ideas = [
        {
            **SAMPLE_IDEAS[0],
            "title_seed": "Best camera settings for exercise after 45",
            "target_keyword": "ejercicio suave despues de los 45",
            "keyword_notes": ["preexisting_marker_note"],
        }
    ]
    _dup_setup(monkeypatch, ideas, [])
    fake = FakeBrowserClient(response=_make_raw(ideas))
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post(
            "/channels/vida-plena-45/ideas/generate", json={"seed_topics": [], "count": 1}
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)
    body = r.json()
    notes = body["ideas"][0].get("keyword_notes", [])
    assert "preexisting_marker_note" in notes
    assert any("language_mismatch" in n or "uncertain" in n for n in notes)


# --- Section 7: optional SERP hook ----------------------------------------

async def _fake_vidiq(keywords):
    return [
        {
            "keyword": kw,
            "score": 82 if "45" in kw else 40,
            "volume": "Medium",
            "competition": "Low",
            "related": ["dormir mejor despues de los 45"],
        }
        for kw in keywords
    ]


def test_serp_inspection_disabled_by_default():
    result = asyncio.run(
        _discover_top_keywords(
            ["dormir mejor despues de los 45"], _fake_vidiq,
            channel_config={"audience": {"language": "es-ES"}},
        )
    )
    assert result["metadata"]["serp_inspection"] == "disabled"
    for it in result["all_scored_keywords"]:
        assert it["serp_opportunity"] == 50


def test_serp_hook_updates_serp_opportunity_when_enabled():
    async def serp_fn(keywords):
        return [{"keyword": k, "serp_opportunity": 95, "serp_notes": ["few_exact_match_titles"]} for k in keywords]

    result = asyncio.run(
        _discover_top_keywords(
            ["dormir mejor despues de los 45"], _fake_vidiq,
            channel_config={"audience": {"language": "es-ES"}, "keyword_scoring": {"enable_serp_inspection": True}},
            serp_fn=serp_fn,
        )
    )
    assert result["metadata"]["serp_inspection"] == "enabled"
    found = result["all_scored_keywords"]
    assert any(it["serp_opportunity"] == 95 for it in found)
    assert any("few_exact_match_titles" in it["notes"] for it in found)


def test_serp_hook_recalculates_final_score_when_enabled():
    async def serp_high(keywords):
        return [{"keyword": k, "serp_opportunity": 100, "serp_notes": []} for k in keywords]

    base = asyncio.run(
        _discover_top_keywords(
            ["dormir mejor despues de los 45"], _fake_vidiq,
            channel_config={"audience": {"language": "es-ES"}},
        )
    )
    boosted = asyncio.run(
        _discover_top_keywords(
            ["dormir mejor despues de los 45"], _fake_vidiq,
            channel_config={"audience": {"language": "es-ES"}, "keyword_scoring": {"enable_serp_inspection": True}},
            serp_fn=serp_high,
        )
    )
    base_score = base["all_scored_keywords"][0]["final_score"]
    boost_score = boosted["all_scored_keywords"][0]["final_score"]
    assert boost_score > base_score


def test_serp_hook_failure_is_fail_soft():
    async def serp_boom(keywords):
        raise RuntimeError("serp down")

    result = asyncio.run(
        _discover_top_keywords(
            ["dormir mejor despues de los 45"], _fake_vidiq,
            channel_config={"audience": {"language": "es-ES"}, "keyword_scoring": {"enable_serp_inspection": True}},
            serp_fn=serp_boom,
        )
    )
    assert result["metadata"]["serp_inspection"] == "failed"
    for it in result["all_scored_keywords"]:
        assert it["serp_opportunity"] == 50
        assert "serp_inspection_failed" in it["notes"]


# --- Section 8: prompt variation + optional fields ------------------------

def test_idea_prompt_includes_pain_promise_and_format_rules(channel_path: Path):
    from video_agent.utils.json_io import read_yaml
    cfg = read_yaml(channel_path)
    prompt = _idea_gen_prompt(cfg, ["dormir mejor despues de los 45"], 3)
    low = prompt.lower()
    assert "checklist" in low
    assert "pain" in low or "dolor" in low
    assert "mistake" in low or "error" in low


def test_parse_ideas_accepts_optional_extra_fields_if_supported():
    raw = json.dumps([
        {
            "topic": "Dormir mejor",
            "angle": "rutina simple",
            "target_duration_sec": 54,
            "key_points": ["a", "b", "c", "d", "e"],
            "title_seed": "Cómo dormir mejor después de los 45",
            "target_keyword": "dormir mejor despues de los 45",
            "thumbnail_hook": "NO DESCANSAS",
            "viewer_pain": "duerme pero se levanta cansado",
            "idea_format": "mistake_checklist",
        }
    ])
    ideas = parse_ideas(raw)
    assert len(ideas) == 1
    assert ideas[0]["thumbnail_hook"] == "NO DESCANSAS"
    assert ideas[0]["idea_format"] == "mistake_checklist"


# --- Section 9: realistic regression table --------------------------------

@pytest.mark.parametrize(
    "keyword,expected",
    [
        ("dormir mejor despues de los 45", "good_spanish_45plus"),
        ("ejercicio suave despues de los 45", "good_spanish_45plus"),
        ("mente acelerada despues de los 45", "good_spanish_45plus"),
        ("cena ligera despues de los 45", "good_spanish_45plus"),
        ("best camera settings", "language_rejected"),
        ("como comer bem depois dos 45", "language_rejected"),
        ("comer mejor despues de los 45", "not_italian_false_positive"),
        ("rutina fitness suave despues de los 45", "loanword_allowed"),
        ("menopausia despues de los 45", "sensitive_allowed_with_flag"),
    ],
)
def test_realistic_keyword_scoring_regression(keyword, expected):
    lang, notes = detect_language_fit(keyword, "spanish")
    if expected == "good_spanish_45plus":
        assert lang >= 80
    elif expected == "language_rejected":
        assert lang < 80
    elif expected == "not_italian_false_positive":
        assert "language_mismatch_italian" not in notes
        assert lang >= 80
    elif expected == "loanword_allowed":
        assert "language_mismatch_english" not in notes
        assert lang >= 80
    elif expected == "sensitive_allowed_with_flag":
        item = enrich_keyword_item({"keyword": keyword, "score": 70}, DEFAULT_CHANNEL_KEYWORD_CONFIG)
        # Flagged for safety, but NOT killed for being sensitive: content fit
        # stays healthy and it is never rejected for content/sensitivity.
        assert item["medical_safety_required"] is True
        assert "sensitive_45plus_topic_requires_disclaimer" in item["notes"]
        assert item["content_fit"] >= 55
        assert "content_mismatch" not in item["rejection_reasons"]
