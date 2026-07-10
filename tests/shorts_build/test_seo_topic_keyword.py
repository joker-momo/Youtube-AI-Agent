"""Topic-keyword SEO contract (operator feedback 2026-07-10).

A Short titled "Si tienes más de 45, descubre tu ritmo" scored 53/100: YouTube
could not classify the video because neither the title, the description's first
sentence, nor any hashtag carried the actual topic keyword ("café"). These
tests pin the three rules:

1. TITLE must contain a topic keyword (audience+benefit alone is invalid).
2. DESCRIPTION's first sentence must contain the topic keyword.
3. At least one hashtag must be topic-specific (e.g. #caféysalud), not only
   generic wellness tags.
"""
import json

from video_agent.shorts.short_seo_builder import (
    _seo_topic_issues,
    _topic_tokens,
    build_short_seo,
)

PLAN = {"short_id": "s-1", "format": "checklist",
        "title": "El plan de siete días para medir tu café",
        "viewer_pain": "No sabes cuánto café tomas", "practical_payoff": "Mide tu café"}
SCRIPT = {"hook": "Si tienes más de 45, mira tu café",
          "narration": "Un plan de siete días para medir tu café y tu descanso.",
          "cta": "Sigue para más"}
CFG = {"audience": {"age_range": [45, 75]}}


def test_topic_tokens_derive_from_plan_title_topic_pillar():
    tokens = _topic_tokens(PLAN)
    assert "cafe" in tokens  # accent-stripped
    assert "plan" in tokens


def test_generic_title_without_topic_keyword_is_flagged():
    issues = _seo_topic_issues(
        "Si tienes más de 45, descubre tu ritmo",
        "El café influye en tu ritmo. #cafeysalud",
        ["#cafeysalud", "#bienestar"],
        _topic_tokens(PLAN),
    )
    assert any("Title" in i or "title" in i for i in issues)


def test_description_first_sentence_must_carry_topic_keyword():
    issues = _seo_topic_issues(
        "Si tienes más de 45, mira tu café",
        "Encuentra tu propio ritmo con pasos diarios. El café importa.",
        ["#cafeysalud", "#bienestar"],
        _topic_tokens(PLAN),
    )
    assert any("first sentence" in i.lower() for i in issues)


def test_generic_hashtags_only_is_flagged():
    issues = _seo_topic_issues(
        "Si tienes más de 45, mira tu café",
        "Tu café marca tu ritmo diario.",
        ["#bienestar", "#autocuidado", "#vida45plus"],
        _topic_tokens(PLAN),
    )
    assert any("hashtag" in i.lower() for i in issues)


def test_fully_topical_seo_has_no_topic_issues():
    issues = _seo_topic_issues(
        "Si tienes más de 45, mira tu café",
        "Descubre cómo tu café influye en tu ritmo y bienestar.",
        ["#cafeysalud", "#bienestar", "#vida45plus"],
        _topic_tokens(PLAN),
    )
    assert issues == []


def test_build_short_seo_retries_generic_title_into_topical_one(tmp_path):
    calls = []

    def llm_fn(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({
                "title": "Si tienes más de 45, descubre tu ritmo",
                "description": "Encuentra tu ritmo con pequeños pasos. #bienestar",
                "hashtags": ["#bienestar", "#autocuidado"],
                "pinned_comment": "¿Qué te cuesta más?",
            })
        return json.dumps({
            "title": "Si tienes más de 45, mira tu café",
            "description": "Descubre cómo tu café marca tu ritmo diario. ¿Cuándo tomas el último? #cafeysalud",
            "hashtags": ["#cafeysalud", "#bienestar", "#vida45plus"],
            "pinned_comment": "¿Cuántos cafés tomas al día?",
        })

    seo = build_short_seo(tmp_path, "s-1", PLAN, SCRIPT, CFG, llm_fn)
    assert len(calls) == 2  # first answer rejected, feedback retry
    assert "café" in seo["title"].lower() or "cafe" in seo["title"].lower()
    # Retry prompt must carry the topic feedback so the model can self-correct.
    assert "topic keyword" in calls[1].lower()


def test_build_short_seo_appends_topic_hashtag_on_exhaustion(tmp_path):
    def llm_fn(prompt):
        # Always topical title + description but only generic hashtags.
        return json.dumps({
            "title": "Si tienes más de 45, mira tu café",
            "description": "Tu café marca tu ritmo. #bienestar",
            "hashtags": ["#bienestar", "#autocuidado"],
            "pinned_comment": "¿Cuántos cafés tomas?",
        })

    seo = build_short_seo(tmp_path, "s-1", PLAN, SCRIPT, CFG, llm_fn)
    stripped = [t.lstrip("#") for t in seo["hashtags"]]
    assert any(t.startswith(("cafe", "plan", "siete", "medir")) for t in stripped), seo["hashtags"]


def test_seo_prompt_carries_topic_keyword_rules():
    from video_agent.shorts import prompts
    p = prompts.short_seo_prompt(CFG, PLAN, SCRIPT)
    assert "PRIMARY TOPIC KEYWORD" in p
    assert "first sentence" in p.lower()
    assert "topic-specific" in p.lower()


def test_description_must_end_with_engagement_question():
    """Operator audit: the description should close with one short question that
    invites a comment (algorithm rewards early interaction)."""
    from video_agent.shorts.short_seo_builder import _seo_engagement_issue
    assert _seo_engagement_issue("Tu café marca tu ritmo. #cafe #bienestar") is not None
    assert _seo_engagement_issue(
        "Tu café marca tu ritmo. ¿Qué error cometes más? #cafe #bienestar"
    ) is None


def test_build_short_seo_retries_description_without_question(tmp_path):
    import json as _json
    calls = []

    def llm_fn(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return _json.dumps({
                "title": "Si tienes más de 45, mira tu café",
                "description": "Tu café marca tu ritmo diario. #cafeysalud",
                "hashtags": ["#cafeysalud", "#bienestar"],
                "pinned_comment": "¿Cuántos cafés tomas?",
            })
        return _json.dumps({
            "title": "Si tienes más de 45, mira tu café",
            "description": "Tu café marca tu ritmo diario. ¿Qué error cometes más? #cafeysalud",
            "hashtags": ["#cafeysalud", "#bienestar"],
            "pinned_comment": "¿Cuántos cafés tomas?",
        })

    seo = build_short_seo(tmp_path, "s-1", PLAN, SCRIPT, CFG, llm_fn)
    assert len(calls) == 2
    assert "engagement" in calls[1].lower() or "question" in calls[1].lower()
    body = seo["description"].split("#")[0].strip()
    assert body.endswith("?")


def test_seo_prompt_asks_for_broad_search_phrase_in_description():
    """Operator audit (tolerancia short, 82/100): besides the topic keyword,
    the description's first sentence should carry one broad wellness search
    phrase (e.g. "hábitos saludables") so YouTube search can classify it."""
    from video_agent.shorts import prompts
    p = prompts.short_seo_prompt(CFG, PLAN, SCRIPT)
    assert "hábitos saludables" in p
    assert "broad wellness search phrase" in p.lower()


def test_short_3_letter_topic_word_is_not_dropped_from_topic_tokens():
    """Bug (2026-07-11): a bread ("pan") checklist_score Short published with title
    "Si tienes más de 45: comprueba" -- no mention of "pan" anywhere. Root cause:
    _title_content_tokens required >=4 chars, silently dropping short-but-real
    Spanish topic nouns ("pan", "sal", "sol", "voz"...) from BOTH the topic-keyword
    requirement AND the emergency-fallback title synthesis, so the whole
    topic-keyword contract (bug-517/519) never even considered "pan" a valid
    keyword to enforce or embed."""
    plan_with_short_topic = {"short_id": "s-1", "format": "infographic",
                              "title": "¿Este pan te conviene?"}
    tokens = _topic_tokens(plan_with_short_topic)
    assert "pan" in tokens


def test_fallback_title_can_embed_a_3_letter_topic_word():
    from video_agent.shorts.short_seo_builder import _fallback_title_from_hook
    title = _fallback_title_from_hook("Pan: comprueba estas 5 señales", 45)
    assert "pan" in title.lower()
