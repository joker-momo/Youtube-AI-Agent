"""Idea generator: seed keyword selection → ChatGPT idea expansion.

Correct flow:
  1. Collect user-provided seeds or channel-matched trend seeds.
  2. Enrich keyword candidates with local audience, language, intent, and content signals.
  3. Select top-opportunity plus long-tail candidates for prompt input.
  4. Ask ChatGPT to flesh out one video idea per top keyword.

Usage::

    ideas = await generate_ideas(
        channel_path,
        chatgpt_fn=lambda msgs: client.run_session("chatgpt", msgs),
        seed_topics=["insomnio", "menopausia"],
        count=5,
    )
    paths = save_ideas(ideas, channel_id="vida-plena-45", out_dir=repo_root() / "inputs")
"""
from __future__ import annotations

import functools
import json
import re
import unicodedata
import html
import urllib.request

try:
    from defusedxml import ElementTree as ET  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - falls back when defusedxml missing
    import xml.etree.ElementTree as ET  # noqa: S405 — XXE-safe payload only via defusedxml; install defusedxml
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from video_agent.storage.atomic import atomic_write_json
from video_agent.utils.json_io import read_yaml

IDEAS_SUBDIR = "ideas"
PUBLISHED_VIDEOS_FILE = "published_videos.json"


# ---------------------------------------------------------------------------
# YouTube channel sync + duplicate detection
# ---------------------------------------------------------------------------

def _yt_rss_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def _yt_videos_url(channel_id: str) -> str:
    return f"https://www.youtube.com/channel/{channel_id}/videos"


def _decode_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value


def _title_from_accessibility_label(label: str) -> str:
    label = html.unescape(_decode_json_string(label)).strip()
    label = re.sub(
        r"\s+\d+\s+(?:phút|minutos?|minutes?|giây|segundos?|seconds?).*$",
        "",
        label,
        flags=re.IGNORECASE,
    ).strip()
    return label


def _parse_channel_videos_page(raw_html: str, channel_id: str) -> list[dict]:
    """Extract public video ids/titles from YouTube channel /videos HTML.

    YouTube's RSS endpoint can return 404 even when the channel page lists
    public videos. This fallback reads only stable public page data and keeps
    duplicate detection working with id + title.
    """
    pattern = re.compile(
        r'"(?:contentId|videoId)":"(?P<id>[A-Za-z0-9_-]{11})"'
        r'.{0,2500}?"accessibilityContext":\{"label":"(?P<label>[^"]+)"',
        re.DOTALL,
    )
    seen: set[str] = set()
    videos: list[dict] = []
    for match in pattern.finditer(raw_html):
        vid_id = match.group("id")
        if vid_id in seen:
            continue
        title = _title_from_accessibility_label(match.group("label"))
        if not title or not re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", title):
            continue
        seen.add(vid_id)
        videos.append(
            {
                "id": vid_id,
                "title": title,
                "published": "",
                "updated": None,
                "url": f"https://www.youtube.com/watch?v={vid_id}",
                "author": channel_id,
                "description": None,
                "views": None,
                "rating_average": None,
                "rating_count": None,
            }
        )
    return videos


def _fetch_channel_page_videos(channel_id: str) -> list[dict]:
    req = urllib.request.Request(_yt_videos_url(channel_id), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return _parse_channel_videos_page(raw, channel_id)


def sync_published_videos(channel_config: dict, configs_dir: Path) -> list[dict]:
    """Fetch published videos from YouTube RSS and save to published_videos.json.

    Returns list of video dicts: [{id, title, published}].
    Raises ValueError if youtube_channel_id not set in channel config.
    """
    channel_id = channel_config.get("channel", {}).get("youtube_channel_id")
    ch_id = channel_config.get("channel", {}).get("id", "")
    if not channel_id:
        raise ValueError(f"youtube_channel_id not set in channel config for {ch_id}")

    url = _yt_rss_url(channel_id)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
    except Exception as exc:
        videos = _fetch_channel_page_videos(channel_id)
        if not videos:
            raise ValueError(f"Failed to fetch YouTube RSS for {channel_id}: {exc}") from exc
        out_path = configs_dir / ch_id / PUBLISHED_VIDEOS_FILE
        atomic_write_json(
            out_path,
            {
                "channel_id": channel_id,
                "source": "channel_page_fallback",
                "videos": videos,
                "synced_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return videos

    root = ET.fromstring(raw)
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }
    videos: list[dict] = []
    for entry in root.findall("atom:entry", ns):
        vid_id = entry.findtext("yt:videoId", namespaces=ns) or ""
        title = entry.findtext("atom:title", namespaces=ns) or ""
        published = entry.findtext("atom:published", namespaces=ns) or ""
        updated = entry.findtext("atom:updated", namespaces=ns) or ""
        description = entry.findtext("media:group/media:description", namespaces=ns) or ""
        # Watch URL is exposed as the entry's <link rel="alternate"> href. We
        # also derive a thumbnail URL from the video id (the RSS feed itself
        # lists multiple thumbnail variants; the mqdefault.jpg form is the
        # smallest stable one and is good enough for the dashboard).
        link_el = entry.find("atom:link[@rel='alternate']", ns)
        url = link_el.attrib.get("href") if link_el is not None else (
            f"https://www.youtube.com/watch?v={vid_id}" if vid_id else ""
        )
        author_name = entry.findtext("atom:author/atom:name", namespaces=ns) or ""

        # YouTube RSS exposes ``media:community`` per entry with:
        #   - ``media:statistics views="N"`` — total view count
        #   - ``media:starRating count="N" average="X" min="1" max="5"``
        #     — like ratio (the visible 👍 count is not in RSS; average
        #     rating is a public 0-5 scale proxy).
        views: int | None = None
        stats = entry.find("media:group/media:community/media:statistics", ns)
        if stats is not None:
            raw_views = stats.attrib.get("views")
            if raw_views and raw_views.isdigit():
                views = int(raw_views)
        rating_avg: float | None = None
        rating_count: int | None = None
        rating = entry.find("media:group/media:community/media:starRating", ns)
        if rating is not None:
            try:
                rating_avg = float(rating.attrib.get("average", "0")) or None
            except ValueError:
                rating_avg = None
            try:
                rating_count = int(rating.attrib.get("count", "0")) or None
            except ValueError:
                rating_count = None

        if vid_id and title:
            videos.append(
                {
                    "id": vid_id,
                    "title": title,
                    "published": published,
                    "updated": updated or None,
                    "url": url,
                    "author": author_name or None,
                    "description": description or None,
                    "views": views,
                    "rating_average": rating_avg,
                    "rating_count": rating_count,
                }
            )

    # Sort newest first so the dashboard does not need to re-order on every
    # render. ``published`` is ISO-8601 with timezone so a lexical reverse
    # sort matches chronological order.
    videos.sort(key=lambda v: str(v.get("published") or ""), reverse=True)

    out_path = configs_dir / ch_id / PUBLISHED_VIDEOS_FILE
    atomic_write_json(
        out_path,
        {
            "channel_id": channel_id,
            "source": "rss",
            "videos": videos,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return videos


def load_published_videos(channel_config: dict, configs_dir: Path) -> list[dict]:
    """Load cached published_videos.json. Returns [] if file missing or empty."""
    ch_id = channel_config.get("channel", {}).get("id", "")
    path = configs_dir / ch_id / PUBLISHED_VIDEOS_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("videos", [])
    except Exception:
        return []


# Spanish filler / structural words that show up in nearly every wellness
# title for the 45+ niche ("Cómo dormir mejor después de los 45 cuando…").
# Counting them as evidence of duplication caused 100% false-positives on
# any new idea that shared the channel's positioning phrase. We strip
# these before the overlap check.
_TITLE_STOPWORDS: frozenset[str] = frozenset(
    {
        # filler verbs / connectors
        "como", "para", "cuando", "donde", "sobre", "entre", "desde",
        "todo", "todos", "todas", "esto", "esta", "estos", "estas",
        "este", "ese", "esa", "esos", "esas", "otro", "otra", "otros",
        "otras", "tanto", "tanta", "tantos", "tantas",
        # comparatives / common adverbs
        "menos", "mucho", "mucha", "muchos", "muchas", "poco", "poca",
        "pocos", "pocas", "muy", "mejor", "peor", "casi", "siempre",
        "nunca", "tambien", "ademas", "solo", "solamente",
        # generic time / age positioning the channel reuses on every title
        "despues", "antes", "ahora", "anos", "tiempo", "edad",
        # channel-positioning fragments (vida plena 45+)
        "vida", "plena",
        # other filler nouns that don't carry semantic meaning alone
        "manera", "forma", "modo", "tipo", "tipos", "veces", "vez",
        "parte", "partes", "lado", "lados", "caso", "casos", "cosa",
        "cosas", "ayuda", "ayudar", "puedes", "tener", "hacer", "hace",
    }
)


def _title_tokens(text: str) -> set[str]:
    """Lowercase, accent-strip, split to ≥4-char alpha tokens with stopwords removed."""
    norm = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    tokens = {w for w in re.findall(r"[a-z]{4,}", norm)}
    return tokens - _TITLE_STOPWORDS


def find_duplicate(idea: dict, published: list[dict], overlap_threshold: int = 2) -> str | None:
    """Return matching published video title if the idea genuinely overlaps, else None.

    The duplicate detector used to count any shared 4+-char tokens, which
    fired on filler words ("como", "después") that appear in every
    Spanish wellness title for adults 45+. We now strip a curated
    stopword list before comparison so only content tokens contribute,
    and require ``overlap_threshold`` (default 2) shared content tokens
    before flagging duplication. The match list is the union of the
    idea's title_seed, topic, and target_keyword.
    """
    idea_text = " ".join([
        idea.get("title_seed", ""),
        idea.get("topic", ""),
        idea.get("target_keyword", ""),
    ])
    idea_tokens = _title_tokens(idea_text)
    if not idea_tokens:
        return None
    for vid in published:
        pub_tokens = _title_tokens(vid.get("title", ""))
        shared = idea_tokens & pub_tokens
        if len(shared) >= overlap_threshold:
            return vid["title"]
    return None

# ---------------------------------------------------------------------------
# Niche → Spanish keyword expansion
# ---------------------------------------------------------------------------

_NICHE_KW_MAP: dict[str, list[str]] = {
    "health_wellness":      ["salud", "bienestar", "cuerpo", "mente", "vida"],
    "nutrition_45plus":     ["nutrición", "alimentación", "dieta", "comer", "nutricion"],
    "exercise_low_impact":  ["ejercicio", "caminar", "yoga", "movimiento", "actividad", "estiramiento"],
    "sleep_quality":        ["sueño", "dormir", "descanso", "insomnio", "sueno"],
    "mental_health":        ["estrés", "ansiedad", "depresión", "mental", "emocional", "estres"],
    "hormonal_health":      ["menopausia", "hormonas", "tiroides", "climaterio"],
    "cardiovascular":       ["corazón", "presión", "colesterol", "circulación", "corazon"],
    "diabetes":             ["diabetes", "glucosa", "azúcar", "insulina"],
    "weight_management":    ["peso", "adelgazar", "grasa", "metabolismo", "obesidad"],
}

_CATEGORY_KW_MAP: dict[str, list[str]] = {
    "health_wellness":   ["salud", "bienestar", "médico", "cuerpo", "medico"],
    "fitness":           ["ejercicio", "fitness", "deporte", "gym"],
    "food":              ["comida", "receta", "cocina", "alimento"],
    "beauty":            ["belleza", "piel", "cabello", "antiedad"],
}


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


def _fetch_google_trends(geo: str, language: str = "es") -> list[str]:
    """Fetch daily trending searches from Google Trends RSS for the given geo.

    Returns a list of trend title strings. Never raises — returns [] on error.
    Geo examples: "MX", "CO", "ES", "AR"
    """
    url = f"https://trends.google.com/trending/rss?geo={geo}&hl={language}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        ns = {"ht": "https://trends.google.com/trends/trendingsearches/daily"}
        titles: list[str] = []
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                titles.append(title_el.text.strip())
        return titles
    except Exception as exc:
        import sys
        print(f"[idea_generator] Google Trends fetch failed ({geo}): {exc}", file=sys.stderr)
        return []


def _auto_seeds_from_trends(channel_config: dict, max_seeds: int = 10) -> list[str]:
    """Derive seed keywords from Google Trends filtered by channel niche.

    Tries each primary_market geo in order. Falls back to sub_niches as seeds
    if no trending topic matches the channel niche.
    """
    markets = channel_config.get("audience", {}).get("primary_markets", ["MX"])
    language = channel_config.get("audience", {}).get("language", "es").split("-")[0]
    niche_kws = _niche_keywords(channel_config)
    sub_niches_raw = channel_config.get("niche", {}).get("sub_niches", [])

    all_trends: list[str] = []
    for geo in markets[:2]:  # try first 2 markets max
        trends = _fetch_google_trends(geo, language)
        all_trends.extend(trends)
        if len(all_trends) >= 30:
            break

    # Filter by niche relevance
    matched = [t for t in all_trends if _trend_matches_niche(t, niche_kws)]

    if matched:
        unique = list(dict.fromkeys(matched))
        return unique[:max_seeds]

    # Fallback 1: channel-specific seed profile (Spain-first, 45+ concrete).
    explicit_seeds = [
        str(s).strip()
        for s in ((channel_config.get("keyword_research") or {}).get("fallback_seeds") or [])
        if str(s).strip()
    ]
    if explicit_seeds:
        return explicit_seeds[:max_seeds]

    # Fallback 2: use sub_niches expanded to Spanish keywords as seeds
    fallback: list[str] = []
    for sn in sub_niches_raw:
        kws = _NICHE_KW_MAP.get(sn, [])
        if kws:
            fallback.append(kws[0])  # take first representative keyword
    return fallback[:max_seeds] if fallback else ["salud", "bienestar"]

_REQUIRED_FIELDS = {"topic", "angle", "target_duration_sec", "key_points", "title_seed"}


# ---------------------------------------------------------------------------
# Keyword scoring V2 helpers
# ---------------------------------------------------------------------------

DEFAULT_CHANNEL_KEYWORD_CONFIG = {
    "channel_name": "Vida Plena 45+: Salud y Bienestar",
    "target_language": "spanish",
    "target_audience": "people_45_plus",
    "audience_markers": [
        "45", "45+", "despues de los 45", "después de los 45",
        "mayores de 45", "a partir de los 45", "despues de los cuarenta",
        "después de los cuarenta",
    ],
    "core_topics": [
        "nutricion", "nutrición", "alimentacion", "alimentación",
        "comer mejor", "comer bien", "salud", "bienestar",
        "energia", "energía", "sueño", "dormir", "descanso",
        "habitos", "hábitos", "movimiento", "caminar",
        "estres", "estrés", "ansiedad", "peso", "metabolismo",
    ],
    "content_positioning": [
        "sin dietas extremas", "sin culpa", "sin caos",
        "simple", "practico", "práctico", "realista",
        "calma", "vida plena",
    ],
    "enable_serp_inspection": False,
    "serp_max_results": 10,
    "max_keywords_per_intent_cluster": 3,
    "target_locale": "Spain",
    "locale_language_code": "es-ES",
    "lexical_prefer": [
        "móvil",
        "ordenador",
        "por la tarde",
        "de madrugada",
        "personas de más de 45 años",
    ],
    "lexical_avoid": [
        "celular",
        "computadora",
        "LatAm",
        "adultos mayores",
        "tercera edad",
        "ancianos",
    ],
}

PORTUGUESE_MARKERS = [
    "depois", "voce", "você", "saude", "saúde", "bem-estar",
    "efeito sanfona", "comer bem", "sem culpa", "mais energia",
    "dieta maluca", "emagrecer", "sono", "cafe da manha", "café da manhã",
    "refeicao", "refeição", "almoco", "almoço", "jantar", "apos os 45",
    "após os 45", "aos 45",
]

SPANISH_MARKERS = [
    "despues", "después", "sin culpa", "salud", "bienestar",
    "energia", "energía", "sueno", "sueño", "dormir",
    "alimentacion", "alimentación", "nutricion", "nutrición",
    "comer mejor", "habitos", "hábitos",
]

# Foreign-language guardrail markers. These are matched with word/phrase
# boundaries via ``_marker_hits`` — NOT naive substring matching — to avoid
# false positives such as Italian "come" inside Spanish "comer" or the
# English loanword "fitness" inside natural Spanish keywords. Prefer
# multi-word phrase markers; avoid short ambiguous single tokens.
ENGLISH_MARKERS = [
    "best camera settings", "weight loss", "sleep tips",
    "morning routine", "diet plan", "workout routine", "healthy aging",
]

FRENCH_MARKERS = [
    "apres 45 ans", "bien-etre", "mieux dormir",
    "perdre du poids", "manger mieux",
]

ITALIAN_MARKERS = [
    "dopo i 45", "dopo 45 anni", "dormire meglio",
    "mangiare meglio", "benessere dopo", "muoversi dopo",
]

# Age / channel markers used only to decide whether an otherwise unmarked
# keyword should be flagged as "spanish_language_uncertain".
_AGE_CHANNEL_MARKERS = ["45", "45+", "cuarenta"]


def _norm_for_marker_match(text: str) -> str:
    return normalize_keyword(text)


@functools.lru_cache(maxsize=512)
def _marker_to_pattern(marker: str) -> "re.Pattern[str]":
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

INTENT_KEYWORDS = [
    ("nutrition_after_45", ["comer", "alimentacion", "nutricion", "plato", "comida", "dieta", "proteina", "fibra"]),
    ("energy_after_45", ["energia", "cansancio", "fatiga", "bajones", "ritmo"]),
    ("sleep_after_45", ["sueno", "dormir", "descanso", "insomnio", "noche"]),
    ("movement_after_45", ["movimiento", "caminar", "ejercicio", "fuerza", "musculo", "articulaciones"]),
    ("emotional_wellbeing_after_45", ["estres", "ansiedad", "calma", "emocional", "mente", "motivacion"]),
    ("weight_management_after_45", ["peso", "adelgazar", "bajar de peso", "metabolismo", "efecto rebote", "efecto yoyo"]),
    ("general_health_after_45", ["salud", "bienestar", "habitos"]),
]

UNSAFE_CLAIMS = ["cura", "curar", "garantizado", "milagro", "elimina para siempre"]
GENERIC_KEYWORDS = {"salud", "bienestar", "nutricion", "dieta"}


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
    if _has_any(norm, ["salud", "bienestar", "comer", "alimentacion", "nutricion", "sueno", "energia", "habitos", "movimiento"]):
        score += 15
    if _has_any(norm, ["simple", "practico", "realista", "sin dietas", "sin culpa", "calma"]):
        score += 10
    if _has_any(norm, ["cansancio", "fatiga", "bajones", "peso", "metabolismo", "dormir", "insomnio", "estres"]):
        score += 10
    if norm in GENERIC_KEYWORDS and not _has_any(norm, ["45", "despues de los 45", "cansancio", "peso", "dormir"]):
        score -= 20
    if _has_any(norm, ["ninos", "adolescentes", "embarazo", "embarazada", "culturismo", "volumen muscular"]):
        score -= 25
    return _clamp(score)


def score_intent_strength(keyword: str) -> int:
    norm = normalize_keyword(keyword)
    score = 40
    if _has_any(norm, ["como", "evitar", "mejorar", "organizar", "recuperar", "dormir", "comer", "bajar", "cambiar"]):
        score += 20
    if _has_any(norm, ["culpa", "caos", "cansancio", "fatiga", "bajones", "insomnio", "ansiedad", "estres", "efecto rebote", "efecto yoyo"]):
        score += 20
    if _has_any(norm, ["mas energia", "dormir mejor", "comer mejor", "bajar de peso", "sin dietas"]):
        score += 15
    if _has_any(norm, ["despues de los 45", "45+", "mayores de 45"]):
        score += 10
    if len(norm.split()) < 3:
        score -= 15
    if norm in GENERIC_KEYWORDS:
        score -= 10
    return _clamp(score)


# High-risk disease topics: still penalized and flagged for medical safety.
HIGH_RISK_MEDICAL_TOPICS = [
    "diabetes", "hipertension", "hipertensión", "tiroides",
    "colesterol", "osteoporosis",
]

# Valid 45+ wellness pillars: legitimate when handled with a disclaimer.
# Lightly penalized (or not at all) and flagged — never blindly rejected.
VALID_45PLUS_SENSITIVE_TOPICS = [
    "menopausia", "perimenopausia", "sofocos", "cambios hormonales",
]


def score_content_fit(keyword: str, channel_config: dict) -> int:
    norm = normalize_keyword(keyword)
    score = 50
    if classify_intent_cluster(norm) in {
        "nutrition_after_45", "energy_after_45", "sleep_after_45",
        "movement_after_45", "emotional_wellbeing_after_45",
    }:
        score += 20
    if _has_any(norm, ["culpa", "caos", "energia", "cansancio", "plato", "cuerpo", "dormir", "calma", "edad", "45"]):
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
        "nutrition_after_45": ("Comer mejor después de los 45 sin culpa ni dietas extremas", ["SIN CULPA", "COME CON CALMA", "TU PLATO BASE"]),
        "energy_after_45": ("Evitar bajones de energía después de los 45 con comidas simples", ["MÁS ENERGÍA", "NO ES TU EDAD", "RECUPERA TU RITMO"]),
        "sleep_after_45": ("Dormir mejor después de los 45 con una rutina realista", ["DUERME MEJOR", "DESCANSA HOY", "NOCHE EN CALMA"]),
        "movement_after_45": ("Moverte más después de los 45 sin rutinas imposibles", ["MUÉVETE SIN DOLOR", "EMPIEZA SUAVE", "TU CUERPO PIDE MOVIMIENTO"]),
        "emotional_wellbeing_after_45": ("Cuidar tu bienestar emocional después de los 45 con hábitos simples", ["MENTE EN CALMA", "MENOS ESTRÉS", "RESPIRA HOY"]),
        "weight_management_after_45": ("Manejar el peso después de los 45 sin efecto rebote ni dietas extremas", ["SIN REBOTE", "NO MÁS YOYÓ", "SIN DIETAS LOCAS"]),
    }
    angle, hooks = mapping.get(cluster, ("Un hábito simple para sentirte mejor después de los 45", ["DESPUÉS DE LOS 45", "CAMBIO SIMPLE", "VIDA PLENA"]))
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
            item["rejection_reasons"] = sorted(set((current.get("rejection_reasons") or []) + (item.get("rejection_reasons") or [])))
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


def dedupe_by_normalized_keyword_and_intent(items: list[dict], max_per_cluster: int = 3) -> list[dict]:
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


# ---------------------------------------------------------------------------
# Keyword scoring helpers
# ---------------------------------------------------------------------------

def _extract_related_strings(score_result: dict) -> list[str]:
    """Pull related keyword strings from a score dict."""
    out: list[str] = []
    for r in (score_result.get("related") or []):
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
    for r in (seed_results + related_results):
        kw = r.get("keyword", "")
        if not kw:
            continue
        raw_scored.append({
            **r,
            "keyword": kw,
        })

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
                    base_notes = [n for n in (it.get("notes") or []) if n != "serp_inspection_disabled"]
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
        bucketed["long_tail_test_keywords"] = bucketed["long_tail_test_keywords"][: max(3, top_n // 2)]
        bucketed["rejected_keywords"] = bucketed["rejected_keywords"][:20]
        return bucketed

    # Merge and normalise legacy output
    all_scored: list[dict] = []
    for r in raw_scored:
        score = r.get("score")
        all_scored.append({
            "keyword": r["keyword"],
            "score": score if isinstance(score, (int, float)) else 0,
            "volume": r.get("volume", ""),
            "competition": r.get("competition", ""),
        })

    # Deduplicate by keyword (keep highest score)
    best: dict[str, dict] = {}
    for item in all_scored:
        key = item["keyword"].lower()
        if key not in best or item["score"] > best[key]["score"]:
            best[key] = item

    sorted_kws = sorted(best.values(), key=lambda x: x["score"], reverse=True)
    return sorted_kws[:top_n]


# ---------------------------------------------------------------------------
# Prompt builder (keyword-anchored)
# ---------------------------------------------------------------------------

MAX_PUBLISHED_TITLES_IN_IDEA_PROMPT = 30


def _idea_gen_prompt(
    channel_config: dict,
    top_keywords: list[dict] | list[str],
    count: int,
    published_titles: list[str] | None = None,
) -> str:
    ch = channel_config.get("channel", {})
    audience = channel_config.get("audience", {})
    niche = channel_config.get("niche", {})
    fmt = channel_config.get("content_format", {})
    positioning = channel_config.get("positioning", {})

    channel_name = ch.get("name", ch.get("id", ""))
    description = ch.get("description", "")
    language = audience.get("language", "es-ES")
    locale_style = channel_config.get("locale_style", {}) or {}
    target_locale = locale_style.get("target_locale") or ("Spain" if language == "es-ES" else "Latin America")
    lexical = locale_style.get("lexical_preferences", {}) or {}
    lexical_prefer = list(lexical.get("prefer") or [])
    lexical_avoid = list(lexical.get("avoid") or [])
    age_range = audience.get("age_range", [45, 75])
    sub_niches = niche.get("sub_niches", [])
    avoid_topics = niche.get("avoid_topics", [])
    target_dur = fmt.get("target_duration_sec", 54)
    forbidden = positioning.get("forbidden_phrases", [])
    preferred = positioning.get("preferred_phrases", [])

    avoid_block = ""
    if avoid_topics:
        avoid_block = f"\nAvoid these subjects entirely: {', '.join(avoid_topics)}."

    phrase_block = ""
    if forbidden or preferred:
        parts = []
        if forbidden:
            parts.append("Forbidden phrases (never use): " + ", ".join(f'"{p}"' for p in forbidden))
        if preferred:
            parts.append("Preferred phrases: " + ", ".join(f'"{p}"' for p in preferred))
        phrase_block = "\n" + ". ".join(parts) + "."

    kw_lines = []
    for i, kw in enumerate(top_keywords, 1):
        if isinstance(kw, str):
            kw_lines.append(f'  {i}. "{kw}"')
            continue
        score = kw.get("final_score", kw.get("score", "?"))
        keyword_source_score = kw.get("keyword_source_score", kw.get("score"))
        vol = kw.get("volume", "")
        comp = kw.get("competition", "")
        cluster = kw.get("intent_cluster", "")
        angle = kw.get("recommended_angle", "")
        hooks = kw.get("thumbnail_hook_options", [])
        meta = f"final score {score}/100"
        if keyword_source_score is not None:
            meta += f", external score: {keyword_source_score}/100"
        if vol:
            meta += f", volume: {vol}"
        if comp:
            meta += f", competition: {comp}"
        if cluster:
            meta += f", cluster: {cluster}"
        if angle:
            meta += f", recommended angle: {angle}"
        if hooks:
            meta += f", thumbnail hooks: {', '.join(hooks[:3])}"
        kw_lines.append(f"  {i}. \"{kw['keyword']}\" ({meta})")

    kw_block = "\n".join(kw_lines)

    published_block = ""
    titles = [str(t).strip() for t in (published_titles or []) if str(t).strip()]
    titles = titles[:MAX_PUBLISHED_TITLES_IN_IDEA_PROMPT]
    if titles:
        title_lines = "\n".join(f"  {i}. {t}" for i, t in enumerate(titles, 1))
        published_block = (
            "\n## Already published videos to avoid\n\n"
            "Do not generate ideas that substantially repeat these titles or angles:\n"
            f"{title_lines}\n\n"
            "- Do not reuse the same core angle as any published title.\n"
            "- If a keyword is close to a published video, choose a clearly different angle.\n"
        )

    prefer_line = ("- Prefer: " + ", ".join(lexical_prefer)) if lexical_prefer else ""
    avoid_line = ("- Avoid: " + ", ".join(lexical_avoid)) if lexical_avoid else ""
    locale_block = "\n".join(
        line for line in [
            "## Locale style",
            f"- Target locale: {target_locale}",
            f"- Language code: {language}",
            f"- Use {target_locale}-natural Spanish.",
            prefer_line,
            avoid_line,
        ] if line
    )

    return f"""You are a YouTube content strategist for the channel **{channel_name}**.

## Channel
- Description: {description}
- Primary audience: Spanish-speaking adults aged {age_range[0]}–{age_range[1]}
- Language: {language}
- Sub-niches: {", ".join(sub_niches)}
{phrase_block}{avoid_block}

{locale_block}

## High-opportunity keywords

These keywords were scored for audience fit, search intent, language fit, and content fit. Higher score = better opportunity.
Build your video ideas directly around these keywords — each idea should target one of them.

{kw_block}
{published_block}
## Task

Generate exactly **{count} video ideas**, each anchored to one of the keywords above.
Use the keyword naturally in the title and topic — this is what viewers are actively searching for.

Each idea must be a JSON object with these exact keys:

```json
{{
  "topic": "one clear sentence describing what the video covers",
  "angle": "the specific educational or emotional angle — why this matters for 45+ adults",
  "target_duration_sec": {target_dur},
  "key_points": ["point 1", "point 2", "point 3", "point 4", "point 5"],
  "title_seed": "a natural Spanish title for the video (no clickbait, no exclamation marks)",
  "target_keyword": "the exact keyword from the list above that this idea targets"
}}
```

Rules:
- All text must be in Spanish for {target_locale} ({language}), not Latin America Spanish unless the config says otherwise.
- Do not use Portuguese.
- Do not use forbidden age-positioning phrases from channel_config.positioning.forbidden_phrases.
- `target_duration_sec` must be exactly {target_dur} for every idea.
- `key_points` must have 5–7 items; each concrete and actionable.
- `title_seed` must include or closely echo the target keyword naturally.
- `title_seed` must read naturally — no ALL CAPS, no "¡¡¡", no exaggerated claims.
- Each idea targets a DIFFERENT keyword from the list.
- Do NOT include medical diagnoses, supplement recommendations, or miracle cures.

## Variation rules (make each idea distinct)
- Avoid making every idea about the same night/sleep angle.
- Prefer a specific viewer pain + practical promise.
- Each idea should use a DIFFERENT viewer intent when possible: symptom/pain, mistake to avoid, simple routine, checklist, explanation.
- Do not generate generic "wellness" topics — be concrete.
- The title should be specific enough to support a strong thumbnail.
- Prefer concrete {target_locale}-first wording for people over 45.

You MAY also include these optional fields when helpful (they are accepted but not required):
- `"thumbnail_hook"`: 1–3 word punchy thumbnail text (e.g. "NO DESCANSAS")
- `"viewer_pain"`: the concrete pain the viewer feels (e.g. "duerme pero se levanta cansado")
- `"idea_format"`: one of symptom_pain, mistake_to_avoid, simple_routine, mistake_checklist, explanation

Return ONLY a valid JSON array of {count} objects — no markdown fences, no commentary.
"""


def _select_keywords_for_prompt(keyword_result: list[dict] | dict, count: int) -> list[dict]:
    if isinstance(keyword_result, list):
        return keyword_result[: max(count, 8)]
    selected = (
        keyword_result.get("top_opportunity_keywords", [])
        + keyword_result.get("long_tail_test_keywords", [])
    )
    if selected:
        return selected[: max(count, 8)]
    legacy = keyword_result.get("all_scored_keywords", [])
    hard_rejections = {"language_mismatch", "audience_mismatch", "content_mismatch"}
    safe_fallback = [
        item
        for item in legacy
        if item.get("language_fit", 0) >= 80
        and item.get("content_fit", 0) >= 60
        and not hard_rejections.intersection(set(item.get("rejection_reasons") or []))
    ]
    return safe_fallback[: max(count, 8)]


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def parse_ideas(raw: str) -> list[dict]:
    """Parse ChatGPT raw response into a list of validated idea dicts."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [_validate_idea(item) for item in parsed if isinstance(item, dict)]
    except json.JSONDecodeError:
        pass

    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, list):
                return [_validate_idea(item) for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass

    ideas = []
    for obj_text in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
        try:
            obj = json.loads(obj_text.group(0))
            if isinstance(obj, dict) and _required_keys_present(obj):
                ideas.append(_validate_idea(obj))
        except json.JSONDecodeError:
            continue

    return ideas


def _required_keys_present(obj: dict) -> bool:
    return _REQUIRED_FIELDS.issubset(obj.keys())


def _validate_idea(obj: dict) -> dict:
    """Normalise and validate an idea dict."""
    missing = _REQUIRED_FIELDS - obj.keys()
    if missing:
        raise ValueError(f"Idea missing required fields: {missing}")
    if not isinstance(obj.get("key_points"), list):
        raise ValueError("key_points must be a list")
    if not isinstance(obj.get("target_duration_sec"), int):
        try:
            obj["target_duration_sec"] = int(obj["target_duration_sec"])
        except (TypeError, ValueError):
            raise ValueError("target_duration_sec must be an integer")
    result = {
        "topic": str(obj["topic"]).strip(),
        "angle": str(obj["angle"]).strip(),
        "target_duration_sec": obj["target_duration_sec"],
        "key_points": [str(kp).strip() for kp in obj["key_points"]],
        "title_seed": str(obj["title_seed"]).strip(),
    }
    if "target_keyword" in obj:
        result["target_keyword"] = str(obj["target_keyword"]).strip()
    # Preserve known optional fields when present (backward compatible: absent
    # fields are simply omitted, never injected).
    for opt in ("thumbnail_hook", "viewer_pain", "idea_format"):
        if obj.get(opt) is not None:
            result[opt] = str(obj[opt]).strip()
    return result


# ---------------------------------------------------------------------------
# File saver
# ---------------------------------------------------------------------------

def _slug(text: str, max_len: int = 40) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9 ]", "", ascii_text).strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    return slug[:max_len].rstrip("-") or "idea"


def save_ideas(ideas: list[dict], channel_id: str, out_dir: Path) -> list[Path]:
    """Write each idea to ``out_dir/ideas/<channel_id>/<timestamp>-<slug>.json``.

    ``channel_id`` is validated against a safe-id regex and the resolved
    ``dest`` is asserted to stay inside ``out_dir`` to prevent traversal
    when this helper is called from CLI/batch entrypoints.
    """
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", channel_id or ""):
        raise ValueError(f"Invalid channel_id: {channel_id!r}")

    out_root = out_dir.resolve()
    dest = (out_dir / IDEAS_SUBDIR / channel_id).resolve()
    try:
        dest.relative_to(out_root)
    except ValueError as exc:
        raise ValueError(f"channel_id escapes out_dir: {channel_id!r}") from exc
    dest.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    paths: list[Path] = []

    for i, idea in enumerate(ideas):
        slug = _slug(idea.get("topic", "idea"))
        filename = f"{now}-{i:02d}-{slug}.json"
        path = dest / filename
        atomic_write_json(path, idea)
        paths.append(path)

    return paths


# ---------------------------------------------------------------------------
# High-level async entrypoint
# ---------------------------------------------------------------------------

async def generate_ideas(
    channel_path: Path,
    chatgpt_fn: Callable[[list[str]], Awaitable[str]],
    seed_topics: list[str] | None = None,
    count: int = 10,
    with_metadata: bool = False,
    published_titles: list[str] | None = None,
) -> list[dict] | tuple[list[dict], list[dict], str]:
    """Select top keywords, then ask ChatGPT to flesh out ideas.

    Returns ideas list by default.
    If ``with_metadata=True``, returns (ideas, top_keywords, seed_source) where seed_source is one of:
      "user"    — caller provided seed_topics
      "trend"   — auto-discovered from Google Trends matching channel niche
      "fallback" — trends didn't match niche; used channel sub_niches as seeds
    """
    import sys
    channel_config = read_yaml(channel_path)
    seeds = list(seed_topics) if seed_topics else []
    seed_source = "user"

    # If no seeds provided, auto-discover from Google Trends filtered by channel niche
    if not seeds:
        print("[idea_generator] No seeds given — fetching from Google Trends…", file=sys.stderr)
        seeds = _auto_seeds_from_trends(channel_config)
        # Detect whether we got real trends or fell back to sub_niches
        niche_kws = _niche_keywords(channel_config)
        seed_source = "trend" if any(_trend_matches_niche(s, niche_kws) for s in seeds) else "fallback"
        print(f"[idea_generator] Seed source={seed_source}, seeds={seeds}", file=sys.stderr)

    cfg = merge_keyword_channel_config(channel_config)
    raw_keywords = [{"keyword": s, "score": None, "volume": "", "competition": ""} for s in seeds]
    enriched = [enrich_keyword_item(item, cfg) for item in raw_keywords if item["keyword"]]
    top_keywords = {
        "top_opportunity_keywords": select_by_cluster_limit(
            [item for item in enriched if item.get("bucket") == "top_opportunity_keywords"],
            int(cfg.get("max_keywords_per_intent_cluster", 3)),
        ),
        "long_tail_test_keywords": select_by_cluster_limit(
            [item for item in enriched if item.get("bucket") == "long_tail_test_keywords"],
            int(cfg.get("max_keywords_per_intent_cluster", 3)),
        ),
        "rejected_keywords": [item for item in enriched if item.get("bucket") == "rejected_keywords"],
        "all_scored_keywords": enriched,
        "metadata": {
            "version": "local_keyword_scoring_v1",
            "target_language": cfg.get("target_language", "spanish"),
            "target_audience": cfg.get("target_audience", "people_45_plus"),
            "serp_inspection": "disabled",
        },
    }

    selected_keywords = _select_keywords_for_prompt(top_keywords, count)
    if not selected_keywords:
        raise ValueError("No scoreable keywords were found from the given seeds. Try different topics.")

    prompt = _idea_gen_prompt(channel_config, selected_keywords, count, published_titles=published_titles)
    raw = await chatgpt_fn([prompt])
    ideas = parse_ideas(raw)
    if not ideas:
        raise ValueError(f"ChatGPT returned no parseable ideas. Raw:\n{raw[:500]}")

    if with_metadata:
        return ideas, top_keywords, seed_source
    return ideas
