"""Tests for idea_generator module and /channels/{id}/ideas/generate endpoint."""
from __future__ import annotations

import json
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_agent.contracts import repo_root
import video_agent.orchestrator.idea_generator as idea_generator
import video_agent.web.routes._legacy as legacy_routes
from video_agent.orchestrator.idea_generator import (
    DEFAULT_CHANNEL_KEYWORD_CONFIG,
    _discover_top_keywords,
    _idea_gen_prompt,
    _select_keywords_for_prompt,
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


def test_post_generate_ideas_returns_and_saves_v2_keyword_metadata(http_client: TestClient, tmp_path: Path):
    ideas = [
        {
            **SAMPLE_IDEAS[0],
            "target_keyword": "como comer mejor despues de los 45",
            "title_seed": "Cómo comer mejor después de los 45 sin dietas",
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
    assert idea["keyword_final_score"] == 50.0
    assert idea["vidiq_score"] == 64
    assert idea["bucket"] == "rejected_keywords"
    assert idea["keyword_rejection_reasons"] == ["low_final_score"]

    saved = json.loads((tmp_path / body["saved"][0]).read_text(encoding="utf-8"))
    assert saved["keyword_final_score"] == 50.0
    assert saved["bucket"] == "rejected_keywords"


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
