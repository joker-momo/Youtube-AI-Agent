"""Keyword normalization, scoring, enrichment & discovery."""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Awaitable, Callable

from video_agent.orchestrator.idea_constants import *  # noqa: F401,F403

__all__ = [
    "_niche_keywords",
    "_trend_matches_niche",
    "_norm_for_marker_match",
    "_marker_to_pattern",
    "_marker_hits",
    "_strip_accents",
    "_clamp",
    "normalize_keyword",
    "classify_intent_cluster",
    "detect_language_fit",
    "_has_any",
    "score_audience_fit",
    "score_intent_strength",
    "score_content_fit",
    "_is_too_generic",
    "calculate_final_score",
    "assign_bucket",
    "generate_keyword_pack",
    "dedupe_by_normalized_keyword",
    "select_by_cluster_limit",
    "dedupe_by_normalized_keyword_and_intent",
    "merge_keyword_channel_config",
    "enrich_keyword_item",
    "_extract_related_strings",
    "_discover_top_keywords",
]


def _niche_keywords(channel_config: dict) -> set[str]:
    """Build a set of Spanish trigger words from channel niche config."""
    category = channel_config.get("niche", {}).get("category", "")
    sub_niches = channel_config.get("niche", {}).get("sub_niches", [])
    description = channel_config.get("channel", {}).get("description", "").lower()

    kws: set[str] = set()
    for w in _CATEGORY_KW_MAP.get(category, []):
        kws.add(w)
    for sn in sub_niches:
        for w in _NICHE_KW_MAP.get(sn, []):
            kws.add(w)
    # Also add raw words from channel description (≥4 chars, alpha)
    for word in re.findall(r"[a-záéíóúüñ]{4,}", description):
        kws.add(word)
    return kws


def _trend_matches_niche(trend_title: str, niche_kws: set[str]) -> bool:
    """True if any niche keyword appears in the trend title (case-insensitive)."""
    title_lower = trend_title.lower()
    # Normalise accents for comparison
    title_norm = unicodedata.normalize("NFKD", title_lower).encode("ascii", "ignore").decode()
    for kw in niche_kws:
        kw_norm = unicodedata.normalize("NFKD", kw).encode("ascii", "ignore").decode()
        if kw_norm in title_norm or kw in title_lower:
            return True
    return False


def _norm_for_marker_match(text: str) -> str:
    return normalize_keyword(text)


@functools.lru_cache(maxsize=512)
def _marker_to_pattern(marker: str) -> re.Pattern[str]:
    # Normalize first, then split into tokens joined by ``\s+``. This avoids
    # relying on the Python-version-specific behaviour of ``re.escape(" ")``
    # and keeps phrase matching flexible across collapsed whitespace.
    parts = [re.escape(part) for part in _norm_for_marker_match(marker).split()]
    if not parts:
        return re.compile(r"(?!x)x")  # never matches
    phrase = r"\s+".join(parts)
    return re.compile(rf"(?<!\w){phrase}(?!\w)")


def _marker_hits(keyword: str, markers: list[str]) -> list[str]:
    norm = _norm_for_marker_match(keyword)
    return [marker for marker in markers if _marker_to_pattern(marker).search(norm)]


def _strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


def normalize_keyword(keyword: str) -> str:
    text = _strip_accents(str(keyword or "").lower().strip())
    text = re.sub(r"\b45\s+plus\b", "45+", text)
    text = re.sub(r"\b(mas de 45|mayores de 45|a partir de los 45)\b", "despues de los 45", text)
    text = re.sub(r"[^\w\s+]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def classify_intent_cluster(keyword: str) -> str:
    norm = normalize_keyword(keyword)
    for cluster, markers in INTENT_KEYWORDS:
        if any(marker in norm for marker in markers):
            return cluster
    return "unknown"


def detect_language_fit(keyword: str, target_language: str) -> tuple[int, list[str]]:
    if not str(keyword or "").strip():
        return 0, ["empty_keyword"]
    if target_language != "spanish":
        return 80, ["language_guardrail_not_configured"]
    norm = normalize_keyword(keyword)
    pt_hits = _marker_hits(keyword, PORTUGUESE_MARKERS)
    en_hits = _marker_hits(keyword, ENGLISH_MARKERS)
    fr_hits = _marker_hits(keyword, FRENCH_MARKERS)
    it_hits = _marker_hits(keyword, ITALIAN_MARKERS)
    notes: list[str] = []
    score = 100

    if pt_hits:
        score -= 30 * len(pt_hits)
        if len(pt_hits) >= 2:
            score -= 20
        notes.append("language_mismatch_portuguese")
    if en_hits:
        score -= 35 * len(en_hits)
        if len(en_hits) >= 2:
            score -= 20
        notes.append("language_mismatch_english")
    if fr_hits:
        score -= 30 * len(fr_hits)
        notes.append("language_mismatch_french")
    if it_hits:
        score -= 25 * len(it_hits)
        notes.append("language_mismatch_italian")

    spanish_present = bool(_marker_hits(keyword, SPANISH_MARKERS))
    foreign_hit = bool(pt_hits or en_hits or fr_hits or it_hits)
    if spanish_present:
        notes.append("spanish_language_ok")
    elif not foreign_hit and not _has_any(norm, _AGE_CHANNEL_MARKERS):
        score -= 10
        notes.append("spanish_language_uncertain")

    return _clamp(score), notes


def _has_any(norm: str, words: list[str]) -> bool:
    return any(normalize_keyword(word) in norm for word in words)


def score_audience_fit(keyword: str, channel_config: dict) -> int:
    norm = normalize_keyword(keyword)
    score = 45
    if _has_any(norm, ["45", "45+", "despues de los 45", "mayores de 45", "a partir de los 45"]):
        score += 30
    if _has_any(
        norm,
        [
            "salud",
            "bienestar",
            "comer",
            "alimentacion",
            "nutricion",
            "sueno",
            "energia",
            "habitos",
            "movimiento",
        ],
    ):
        score += 15
    if _has_any(norm, ["simple", "practico", "realista", "sin dietas", "sin culpa", "calma"]):
        score += 10
    if _has_any(
        norm,
        ["cansancio", "fatiga", "bajones", "peso", "metabolismo", "dormir", "insomnio", "estres"],
    ):
        score += 10
    if norm in GENERIC_KEYWORDS and not _has_any(
        norm, ["45", "despues de los 45", "cansancio", "peso", "dormir"]
    ):
        score -= 20
    if _has_any(
        norm, ["ninos", "adolescentes", "embarazo", "embarazada", "culturismo", "volumen muscular"]
    ):
        score -= 25
    return _clamp(score)


def score_intent_strength(keyword: str) -> int:
    norm = normalize_keyword(keyword)
    score = 40
    if _has_any(
        norm,
        [
            "como",
            "evitar",
            "mejorar",
            "organizar",
            "recuperar",
            "dormir",
            "comer",
            "bajar",
            "cambiar",
        ],
    ):
        score += 20
    if _has_any(
        norm,
        [
            "culpa",
            "caos",
            "cansancio",
            "fatiga",
            "bajones",
            "insomnio",
            "ansiedad",
            "estres",
            "efecto rebote",
            "efecto yoyo",
        ],
    ):
        score += 20
    if _has_any(
        norm, ["mas energia", "dormir mejor", "comer mejor", "bajar de peso", "sin dietas"]
    ):
        score += 15
    if _has_any(norm, ["despues de los 45", "45+", "mayores de 45"]):
        score += 10
    if len(norm.split()) < 3:
        score -= 15
    if norm in GENERIC_KEYWORDS:
        score -= 10
    return _clamp(score)


def score_content_fit(keyword: str, channel_config: dict) -> int:
    norm = normalize_keyword(keyword)
    score = 50
    if classify_intent_cluster(norm) in {
        "nutrition_after_45",
        "energy_after_45",
        "sleep_after_45",
        "movement_after_45",
        "emotional_wellbeing_after_45",
    }:
        score += 20
    if _has_any(
        norm,
        [
            "culpa",
            "caos",
            "energia",
            "cansancio",
            "plato",
            "cuerpo",
            "dormir",
            "calma",
            "edad",
            "45",
        ],
    ):
        score += 15
    if _has_any(norm, ["simple", "practico", "organizar", "habitos", "rutina", "consejos"]):
        score += 10
    if _has_any(norm, HIGH_RISK_MEDICAL_TOPICS):
        score -= 20
    elif _has_any(norm, VALID_45PLUS_SENSITIVE_TOPICS):
        # 45+ pillar topic (e.g. menopausia) — gentle nudge only, not a
        # high-risk penalty, so strong audience/intent signals can still
        # lift it into long-tail or top opportunity.
        score -= 5
    if _has_any(norm, UNSAFE_CLAIMS):
        score -= 30
    return _clamp(score)


def _is_too_generic(keyword: str) -> bool:
    return normalize_keyword(keyword) in GENERIC_KEYWORDS


def calculate_final_score(item: dict) -> float:
    source_score = item.get("keyword_source_score")
    source_component = source_score if isinstance(source_score, (int, float)) else 35
    score = (
        0.40 * source_component
        + 0.22 * item["audience_fit"]
        + 0.15 * item["intent_strength"]
        + 0.10 * item["content_fit"]
        + 0.08 * item["language_fit"]
        + 0.05 * item["serp_opportunity"]
    )
    notes = item.setdefault("notes", [])
    reasons = item.setdefault("rejection_reasons", [])
    if item["language_fit"] < 60:
        score -= 30
        notes.append("language_penalty_high")
    elif item["language_fit"] < 80:
        score -= 20
        notes.append("language_penalty_medium")
    if item["audience_fit"] < 50:
        score -= 20
        reasons.append("audience_mismatch")
    if item["content_fit"] < 50:
        score -= 15
        reasons.append("content_mismatch")
    if item["intent_strength"] < 50:
        score -= 10
    if _is_too_generic(item.get("keyword", "")):
        score -= 15
    if _has_any(normalize_keyword(item.get("keyword", "")), UNSAFE_CLAIMS):
        score -= 25
        reasons.append("unsafe_health_claim_risk")
    final = round(max(0, min(100, score)), 1)
    item["final_score"] = final
    return final


def assign_bucket(item: dict) -> str:
    reasons = item.setdefault("rejection_reasons", [])
    if not str(item.get("keyword") or "").strip():
        reasons.append("empty_keyword")
    if item.get("language_fit", 0) < 70:
        reasons.append("language_mismatch")
    if item.get("audience_fit", 0) < 45:
        reasons.append("audience_mismatch")
    if item.get("content_fit", 0) < 40:
        reasons.append("content_mismatch")
    if item.get("final_score", 0) < 50:
        reasons.append("low_final_score")
    if reasons:
        return "rejected_keywords"
    if (
        item.get("final_score", 0) >= 70
        and item.get("language_fit", 0) >= 80
        and item.get("audience_fit", 0) >= 70
        and item.get("intent_strength", 0) >= 60
        and item.get("content_fit", 0) >= 60
        and item.get("keyword_source_score") is not None
    ):
        return "top_opportunity_keywords"
    if (
        item.get("language_fit", 0) >= 80
        and item.get("audience_fit", 0) >= 70
        and item.get("intent_strength", 0) >= 60
        and item.get("content_fit", 0) >= 55
    ):
        return "long_tail_test_keywords"
    return "rejected_keywords"


def generate_keyword_pack(item: dict) -> dict:
    cluster = item.get("intent_cluster")
    mapping = {
        "nutrition_after_45": (
            "Comer mejor después de los 45 sin culpa ni dietas extremas",
            ["SIN CULPA", "COME CON CALMA", "TU PLATO BASE"],
        ),
        "energy_after_45": (
            "Evitar bajones de energía después de los 45 con comidas simples",
            ["MÁS ENERGÍA", "NO ES TU EDAD", "RECUPERA TU RITMO"],
        ),
        "sleep_after_45": (
            "Dormir mejor después de los 45 con una rutina realista",
            ["DUERME MEJOR", "DESCANSA HOY", "NOCHE EN CALMA"],
        ),
        "movement_after_45": (
            "Moverte más después de los 45 sin rutinas imposibles",
            ["MUÉVETE SIN DOLOR", "EMPIEZA SUAVE", "TU CUERPO PIDE MOVIMIENTO"],
        ),
        "emotional_wellbeing_after_45": (
            "Cuidar tu bienestar emocional después de los 45 con hábitos simples",
            ["MENTE EN CALMA", "MENOS ESTRÉS", "RESPIRA HOY"],
        ),
        "weight_management_after_45": (
            "Manejar el peso después de los 45 sin efecto rebote ni dietas extremas",
            ["SIN REBOTE", "NO MÁS YOYÓ", "SIN DIETAS LOCAS"],
        ),
    }
    angle, hooks = mapping.get(
        cluster,
        (
            "Un hábito simple para sentirte mejor después de los 45",
            ["DESPUÉS DE LOS 45", "CAMBIO SIMPLE", "VIDA PLENA"],
        ),
    )
    item["recommended_angle"] = angle
    item["thumbnail_hook_options"] = hooks
    return item


def dedupe_by_normalized_keyword(items: list[dict]) -> list[dict]:
    """Dedupe by exact normalized keyword only, keeping the best scoring item.

    Does NOT cap per intent cluster, so the caller can keep a complete
    ``all_scored_keywords`` debug view of every distinct keyword scanned.
    """
    best: dict[str, dict] = {}
    for item in items:
        key = item.get("normalized_keyword") or normalize_keyword(item.get("keyword", ""))
        item["normalized_keyword"] = key
        current = best.get(key)
        if current is None:
            best[key] = item
            continue
        cur_tuple = (current.get("final_score", 0), current.get("keyword_source_score") or 0)
        new_tuple = (item.get("final_score", 0), item.get("keyword_source_score") or 0)
        if new_tuple > cur_tuple:
            item["notes"] = sorted(set((current.get("notes") or []) + (item.get("notes") or [])))
            item["rejection_reasons"] = sorted(
                set(
                    (current.get("rejection_reasons") or []) + (item.get("rejection_reasons") or [])
                )
            )
            best[key] = item
    output = list(best.values())
    output.sort(key=lambda x: x.get("final_score", 0), reverse=True)
    return output


def select_by_cluster_limit(items: list[dict], max_per_cluster: int) -> list[dict]:
    """Group by intent cluster, sort by final_score desc, cap per cluster."""
    grouped: dict[str, list[dict]] = {}
    for item in items:
        grouped.setdefault(item.get("intent_cluster", "unknown"), []).append(item)
    output: list[dict] = []
    for _cluster, cluster_items in grouped.items():
        cluster_items.sort(key=lambda x: x.get("final_score", 0), reverse=True)
        output.extend(cluster_items[:max_per_cluster])
    output.sort(key=lambda x: x.get("final_score", 0), reverse=True)
    return output


def dedupe_by_normalized_keyword_and_intent(
    items: list[dict], max_per_cluster: int = 3
) -> list[dict]:
    """Backward-compatible wrapper: dedupe then cluster-cap in one step."""
    return select_by_cluster_limit(dedupe_by_normalized_keyword(items), max_per_cluster)


def merge_keyword_channel_config(channel_config: dict | None) -> dict:
    cfg = dict(DEFAULT_CHANNEL_KEYWORD_CONFIG)
    if not channel_config:
        return cfg
    audience = channel_config.get("audience") or {}
    channel = channel_config.get("channel") or {}
    if channel.get("name"):
        cfg["channel_name"] = channel["name"]
    language = str(audience.get("language") or "").lower()
    if language.startswith("es"):
        cfg["target_language"] = "spanish"
    # Locale-style overrides — Spain-first config flows through to idea prompts.
    locale_style = channel_config.get("locale_style") or {}
    if locale_style:
        cfg["target_locale"] = locale_style.get("target_locale", cfg["target_locale"])
        cfg["locale_language_code"] = (
            locale_style.get("language_code")
            or audience.get("language")
            or cfg["locale_language_code"]
        )
        lexical = locale_style.get("lexical_preferences") or {}
        prefer = lexical.get("prefer")
        avoid = lexical.get("avoid")
        if prefer:
            cfg["lexical_prefer"] = list(prefer)
        if avoid:
            cfg["lexical_avoid"] = list(avoid)
    elif audience.get("language"):
        cfg["locale_language_code"] = audience["language"]
    keyword_cfg = channel_config.get("keyword_scoring") or {}
    cfg.update({k: v for k, v in keyword_cfg.items() if v is not None})
    return cfg


def enrich_keyword_item(item: dict, channel_config: dict) -> dict:
    keyword = str(item.get("keyword") or "").strip()
    score = item.get("score")
    keyword_source_score = score if isinstance(score, (int, float)) else None
    notes = list(item.get("notes") or [])
    if item.get("note"):
        notes.append(str(item["note"]))
    language_fit, language_notes = detect_language_fit(keyword, channel_config["target_language"])
    notes.extend(language_notes)
    enriched = {
        **item,
        "keyword": keyword,
        "normalized_keyword": normalize_keyword(keyword),
        "intent_cluster": classify_intent_cluster(keyword),
        "keyword_source_score": keyword_source_score,
        "score": score if isinstance(score, (int, float)) else None,
        "audience_fit": score_audience_fit(keyword, channel_config),
        "intent_strength": score_intent_strength(keyword),
        "content_fit": score_content_fit(keyword, channel_config),
        "language_fit": language_fit,
        # SERP inspection is intentionally deferred. Browser SERP scraping is
        # expensive for the local Mac appliance, so V2 keeps a neutral score.
        "serp_opportunity": 50,
        "notes": sorted(set(notes + ["serp_inspection_disabled"])),
        "rejection_reasons": [],
    }
    norm = enriched["normalized_keyword"]
    extra_notes: list[str] = []
    if _has_any(norm, HIGH_RISK_MEDICAL_TOPICS):
        enriched["medical_safety_required"] = True
        extra_notes.append("sensitive_medical_topic")
    elif _has_any(norm, VALID_45PLUS_SENSITIVE_TOPICS):
        enriched["medical_safety_required"] = True
        extra_notes.append("sensitive_45plus_topic_requires_disclaimer")
    if extra_notes:
        enriched["notes"] = sorted(set(enriched["notes"] + extra_notes))

    calculate_final_score(enriched)
    enriched["bucket"] = assign_bucket(enriched)
    generate_keyword_pack(enriched)
    return enriched


def _extract_related_strings(score_result: dict) -> list[str]:
    """Pull related keyword strings from a score dict."""
    out: list[str] = []
    for r in score_result.get("related") or []:
        if isinstance(r, str):
            out.append(r)
        elif isinstance(r, dict):
            kw = r.get("keyword") or r.get("term") or ""
            if kw:
                out.append(str(kw))
    return out


async def _discover_top_keywords(
    seeds: list[str],
    score_fn: Callable[[list[str]], Awaitable[list[dict]]],
    max_related: int = 15,
    top_n: int = 8,
    channel_config: dict | None = None,
    use_v2: bool = True,
    serp_fn: Callable[[list[str]], Awaitable[list[dict]]] | None = None,
) -> list[dict] | dict:
    """Score seeds + related keywords and return bucketed V2 keyword data.

    With use_v2=True, returns top opportunity, long-tail, rejected, and all
    scored keyword buckets. With use_v2=False, returns the legacy score-sorted
    list for older callers.
    """
    # Phase 1: score seeds
    seed_results: list[dict] = []
    if seeds:
        try:
            seed_results = await score_fn(seeds)
        except Exception:
            seed_results = []

    # Collect unique related keywords from seed results
    seen: set[str] = set(s.lower() for s in seeds)
    related_pool: list[str] = []
    for r in seed_results:
        for kw in _extract_related_strings(r):
            if kw.lower() not in seen:
                seen.add(kw.lower())
                related_pool.append(kw)
            if len(related_pool) >= max_related:
                break

    # Phase 2: score related keywords
    related_results: list[dict] = []
    if related_pool:
        try:
            related_results = await score_fn(related_pool[:max_related])
        except Exception:
            related_results = []

    raw_scored: list[dict] = []
    for r in seed_results + related_results:
        kw = r.get("keyword", "")
        if not kw:
            continue
        raw_scored.append(
            {
                **r,
                "keyword": kw,
            }
        )

    if use_v2:
        cfg = merge_keyword_channel_config(channel_config)
        max_per_cluster = int(cfg.get("max_keywords_per_intent_cluster", 3))
        enriched = [enrich_keyword_item(item, cfg) for item in raw_scored]
        # Dedupe only — keep the full scanned set for the all_scored debug view.
        deduped_all = dedupe_by_normalized_keyword(enriched)

        # Optional SERP opportunity hook. Disabled by default; fail-soft when
        # the injected callable raises so idea generation never breaks.
        serp_status = "disabled"
        if serp_fn is not None and bool(cfg.get("enable_serp_inspection", False)):
            try:
                candidates = [it["keyword"] for it in deduped_all]
                serp_results = await serp_fn(candidates)
                serp_map = {
                    normalize_keyword(str(r.get("keyword", ""))): r
                    for r in (serp_results or [])
                    if isinstance(r, dict) and r.get("keyword")
                }
                for it in deduped_all:
                    r = serp_map.get(it["normalized_keyword"])
                    if not r or not isinstance(r.get("serp_opportunity"), (int, float)):
                        continue
                    it["serp_opportunity"] = r["serp_opportunity"]
                    serp_notes = [str(n) for n in (r.get("serp_notes") or [])]
                    base_notes = [
                        n for n in (it.get("notes") or []) if n != "serp_inspection_disabled"
                    ]
                    it["notes"] = sorted(set(base_notes + serp_notes))
                    calculate_final_score(it)
                    it["bucket"] = assign_bucket(it)
                serp_status = "enabled"
            except Exception:
                serp_status = "failed"
                for it in deduped_all:
                    it["notes"] = sorted(set((it.get("notes") or []) + ["serp_inspection_failed"]))

        selectable = select_by_cluster_limit(deduped_all, max_per_cluster)

        bucketed = {
            "top_opportunity_keywords": [],
            "long_tail_test_keywords": [],
            "rejected_keywords": [],
            "all_scored_keywords": deduped_all,
            "metadata": {
                "version": "keyword_scoring_v2",
                "enable_serp_inspection": bool(cfg.get("enable_serp_inspection", False)),
                "serp_inspection": serp_status,
                "target_language": cfg.get("target_language", "spanish"),
                "target_audience": cfg.get("target_audience", "people_45_plus"),
            },
        }
        for item in selectable:
            bucket = item["bucket"]
            if bucket in bucketed:
                bucketed[bucket].append(item)
        for key in ("top_opportunity_keywords", "long_tail_test_keywords", "rejected_keywords"):
            bucketed[key].sort(key=lambda x: x.get("final_score", 0), reverse=True)
        bucketed["top_opportunity_keywords"] = bucketed["top_opportunity_keywords"][:top_n]
        bucketed["long_tail_test_keywords"] = bucketed["long_tail_test_keywords"][
            : max(3, top_n // 2)
        ]
        bucketed["rejected_keywords"] = bucketed["rejected_keywords"][:20]
        return bucketed

    # Merge and normalise legacy output
    all_scored: list[dict] = []
    for r in raw_scored:
        score = r.get("score")
        all_scored.append(
            {
                "keyword": r["keyword"],
                "score": score if isinstance(score, (int, float)) else 0,
                "volume": r.get("volume", ""),
                "competition": r.get("competition", ""),
            }
        )

    # Deduplicate by keyword (keep highest score)
    best: dict[str, dict] = {}
    for item in all_scored:
        key = item["keyword"].lower()
        if key not in best or item["score"] > best[key]["score"]:
            best[key] = item

    sorted_kws = sorted(best.values(), key=lambda x: x["score"], reverse=True)
    return sorted_kws[:top_n]
