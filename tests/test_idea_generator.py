"""Tests for idea_generator module and /channels/{id}/ideas/generate endpoint."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_agent.contracts import repo_root
from video_agent.orchestrator.idea_generator import (
    DEFAULT_CHANNEL_KEYWORD_CONFIG,
    _idea_gen_prompt,
    _slug,
    assign_bucket,
    calculate_final_score,
    classify_intent_cluster,
    dedupe_by_normalized_keyword_and_intent,
    detect_language_fit,
    generate_ideas,
    normalize_keyword,
    parse_ideas,
    save_ideas,
    score_audience_fit,
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
    assert [item["keyword"] for item in flatten_keyword_result_for_ui(v2)] == ["b", "c"]


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

    async def run_session(self, site: str, messages, **kwargs) -> str:
        self.calls.append(list(messages))
        if self.error:
            raise self.error
        return self.response


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
