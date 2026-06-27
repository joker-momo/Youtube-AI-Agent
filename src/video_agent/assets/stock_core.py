"""Shared stock-search primitives (stateless helpers).

Query translation/scoring/filters, URL-safety, candidate ranking helpers and the
download clients used by BOTH pipelines' stock acquisition. Leaf module: no
imports from video_agent.stages or video_agent.shorts (see
tests/test_asset_layer_boundary.py). Stateful search machinery lives in the
StockSearchCore class added in T3b.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from video_agent.assets.providers import DEFAULT_HEADERS
from video_agent.contracts import repo_root


def _evidence_terms(evidence: dict[str, Any], *keys: str) -> list[str]:
    out: list[str] = []
    for k in keys:
        for term in (evidence.get(k) or []):
            t = str(term).strip().lower()
            if t:
                out.append(t)
    return out


def _term_hits(term: str, haystack: str) -> bool:
    """True when any informative word (len>3) of ``term`` appears in haystack."""
    words = [w for w in re.split(r"[^\wáéíóúüñ]+", term.lower()) if len(w) > 3]
    if not words:
        words = [term.lower()]
    return any(w in haystack for w in words)


def metadata_evidence_qa(scene: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Deterministic post-asset QA against an asset's metadata (tags/alt/query).

    Enforces the scene's ``required_visual_evidence`` without pixel-level vision:
    rejects a candidate whose metadata hits any forbidden_pose/context/mood term,
    or — for a scene that declares required_actions/objects — whose metadata shows
    none of them. Returns the strict PASS/FAIL Vision-QA schema so it is a drop-in
    default for the injectable ``vision_qa_fn`` (which can later do real vision).
    """
    evidence = scene.get("required_visual_evidence") or {}
    haystack_parts = list(candidate.get("tags") or [])
    for extra in ("alt", "description", "title"):
        if candidate.get(extra):
            haystack_parts.append(str(candidate[extra]))
    haystack_parts.append(str(scene.get("visual_prompt") or ""))
    haystack = " ".join(str(p) for p in haystack_parts).lower()

    forbidden = _evidence_terms(evidence, "forbidden_pose", "forbidden_context", "forbidden_mood")
    forbidden_violations = [term for term in forbidden if _term_hits(term, haystack)]

    required = _evidence_terms(evidence, "required_actions", "required_objects")
    missing_evidence: list[str] = []
    if required and not any(_term_hits(term, haystack) for term in required):
        missing_evidence = list(required)

    if forbidden_violations or missing_evidence:
        reason_parts = []
        if forbidden_violations:
            reason_parts.append("forbidden: " + ", ".join(forbidden_violations))
        if missing_evidence:
            reason_parts.append("missing required evidence")
        return {
            "verdict": "FAIL",
            "missing_evidence": missing_evidence,
            "forbidden_violations": forbidden_violations,
            "confidence": 0.6,
            "reason": "; ".join(reason_parts),
        }
    return {"verdict": "PASS", "missing_evidence": [], "forbidden_violations": [], "confidence": 0.5, "reason": "metadata ok"}


def _assert_safe_http_url(url: str) -> None:
    """Reject non-http(s) schemes and link-local / loopback / RFC1918 hosts.

    Stock-asset URLs come from third-party JSON responses; a compromised
    provider could return ``file://`` or ``http://169.254.169.254/...`` to
    coerce the worker into reading local resources.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Refusing non-http(s) URL: {url!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"URL missing host: {url!r}")
    if host in ("localhost", "metadata.google.internal"):
        raise ValueError(f"Refusing internal host: {host}")
    if host.startswith(("127.", "10.", "169.254.", "192.168.")):
        raise ValueError(f"Refusing internal host: {host}")
    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
            if 16 <= second <= 31:
                raise ValueError(f"Refusing internal host: {host}")
        except (ValueError, IndexError):
            pass


_SPANISH_TO_ENGLISH_KEYWORDS: dict[str, str] = {
    # rooms & spaces
    "habitación": "bedroom", "habitacion": "bedroom", "dormitorio": "bedroom",
    "cocina": "kitchen", "sala": "living room", "baño": "bathroom",
    "casa": "home", "ventana": "window", "puerta": "door", "cama": "bed",
    "mesa": "table", "sofá": "sofa", "sofa": "sofa", "silla": "chair",
    "cortinas": "curtains", "almohada": "pillow", "manta": "blanket",
    # people
    "persona": "person", "personas": "people", "mujer": "woman", "hombre": "man",
    "pareja": "couple", "familia": "family", "adulto": "adult", "adulta": "adult",
    "anciano": "elderly", "anciana": "elderly",
    # objects & activity
    "reloj": "clock", "lámpara": "lamp", "lampara": "lamp", "luz": "light",
    "libro": "book", "libreta": "notebook", "móvil": "smartphone", "movil": "smartphone",
    "teléfono": "phone", "telefono": "phone", "pantalla": "screen",
    "ordenador": "computer", "portátil": "laptop", "portatil": "laptop",
    "taza": "cup", "infusión": "tea", "infusion": "tea", "té": "tea",
    "agua": "water", "comida": "food", "plato": "plate", "cena": "dinner",
    "desayuno": "breakfast", "comer": "eating",
    # actions
    "duerme": "sleeping", "dormir": "sleeping", "descansa": "resting",
    "descanso": "rest", "respira": "breathing", "respiración": "breathing",
    "camina": "walking", "caminar": "walking", "estira": "stretching",
    "estiramiento": "stretching", "lee": "reading", "leer": "reading",
    "escribe": "writing", "escribir": "writing", "apaga": "turning off",
    "enciende": "turning on", "abre": "opening", "cierra": "closing",
    "acomoda": "arranging", "ordena": "organizing", "ordenada": "tidy",
    # time / mood
    "noche": "night", "nocturna": "night", "nocturno": "night",
    "tarde": "evening", "atardecer": "dusk", "mañana": "morning",
    "matutino": "morning", "matutina": "morning", "amanecer": "sunrise",
    "calma": "calm", "tranquilo": "calm", "tranquila": "calm",
    "cálido": "warm", "calido": "warm", "cálida": "warm", "calida": "warm",
    "tenue": "soft", "suave": "gentle",
    # camera framing
    "plano": "shot", "primer": "close-up", "secuencia": "sequence",
    "montaje": "montage", "fondo": "background",
}


_SPANISH_STOPWORDS_FOR_TRANSLATE = {
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "de", "del", "en", "con", "sin", "por", "para", "sobre",
    "y", "o", "u", "que", "qué", "como", "cómo", "cuando",
    "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas",
    "su", "sus", "mi", "mis", "tu", "tus", "se", "le", "lo", "les",
    "no", "sí", "más", "mas", "muy", "ya", "pero", "también",
    "al", "es", "son", "estar", "ser", "hay",
}


def _is_likely_spanish_query(query: str) -> bool:
    spanish_chars = set("áéíóúñ¿¡üÁÉÍÓÚÑÜ")
    if any(c in spanish_chars for c in query):
        return True
    tokens = re.findall(r"[a-záéíóúñü]+", query.lower())
    hits = sum(1 for t in tokens if t in _SPANISH_STOPWORDS_FOR_TRANSLATE)
    return hits >= 2


def _translate_spanish_query_to_english(query: str) -> str:
    """Best-effort Spanish→English keyword extraction for stock-search queries.

    Maps known Spanish content words to English equivalents and drops stopwords.
    Not a real translator — adequate for token-based stock-image search.
    Returns the original query unchanged if no Spanish words are detected.
    """
    if not _is_likely_spanish_query(query):
        return query
    tokens = re.findall(r"[\wáéíóúñüÁÉÍÓÚÑÜ]+", query, flags=re.UNICODE)
    out: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if lowered in _SPANISH_STOPWORDS_FOR_TRANSLATE:
            continue
        translated = _SPANISH_TO_ENGLISH_KEYWORDS.get(lowered)
        if translated:
            out.append(translated)
        else:
            # Keep words that look English-safe (ASCII letters, length >= 4).
            if re.fullmatch(r"[A-Za-z]+", token) and len(token) >= 4:
                out.append(token.lower())
    deduped: list[str] = []
    seen: set[str] = set()
    for word in out:
        if word not in seen:
            deduped.append(word)
            seen.add(word)
    return " ".join(deduped[:12]) or query


def _force_elderly_demographic(query: str) -> str:
    lower_q = query.lower()
    people_words = [
        "person", "woman", "man", "adult", "adults", "couple", "people", "insomniac", "family",
        "doctor", "parent", "senior", "elderly", "grandmother", "grandfather",
        "grandparent", "lady", "gentleman", "individual", "sleeper", "someone", "patient",
        # common visual_prompt patterns used by this pipeline
        "wellness", "photo", "realistic", "lifestyle",
    ]
    has_people = any(word in lower_q for word in people_words)

    if has_people:
        # Replace weak terms like middle aged, adult with strong senior/elderly terms
        q = query
        q = q.replace("Middle aged", "Elderly senior")
        q = q.replace("middle aged", "elderly senior")
        q = q.replace("Middle-aged", "Elderly senior")
        q = q.replace("middle-aged", "elderly senior")
        q = q.replace("Adult", "Elderly")
        q = q.replace("adult", "elderly")
        # Replace '45+' phrasing with explicit elderly
        q = re.sub(r'\b45\+?\b', 'elderly senior 55+', q)

        # Enforce European / Latin American / Hispanic / Caucasian
        if not any(w in lower_q for w in ["european", "latin", "hispanic", "caucasian", "elderly", "senior"]):
            q = f"{q} elderly european senior"
        elif not any(w in lower_q for w in ["european", "latin", "hispanic", "caucasian"]):
            q = f"{q} european latin american"
        return q.strip()
    return query


class DownloadClient(Protocol):
    def download(self, url: str, output_path: Path) -> None: ...


class UrlDownloadClient:
    def download(self, url: str, output_path: Path) -> None:
        _assert_safe_http_url(url)
        request = Request(url, headers=DEFAULT_HEADERS)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(request, timeout=60) as response, output_path.open("wb") as handle:  # noqa: S310 — scheme/host vetted above
            shutil.copyfileobj(response, handle)


def _resolve_project_path(value: str | None, default: str) -> Path:
    path = Path(value or default)
    if not path.is_absolute():
        path = repo_root() / path
    return path


def _stock_filters(visual_config: dict[str, Any], scene_duration_sec: int | None = None) -> dict[str, Any]:
    orientation = visual_config.get("orientation", "landscape")
    if orientation == "horizontal":
        orientation = "landscape"
    filters: dict[str, Any] = {
        "orientation": orientation,
        "per_page": int(visual_config.get("per_page", 10)),
    }
    if scene_duration_sec is not None:
        # Request clips at least as long as the scene so we don't need to loop
        filters["min_duration_sec"] = max(10, scene_duration_sec)
    return filters


STOPWORDS = {
    "45",
    "adult",
    "adults",
    "after",
    "antes",
    "con",
    "del",
    "despues",
    "dia",
    "for",
    "from",
    "las",
    "los",
    "para",
    "por",
    "the",
    "una",
    "with",
}


NEGATIVE_CONTEXT_TERMS = {"massage", "masaje", "spa", "therapy", "terapia"}


NEGATIVE_ALLOW_TERMS = {"massage", "masaje", "spa", "therapy", "terapia", "therapist", "fisioterapia"}


TERM_SYNONYMS = {
    "agua": {"water"},
    "avena": {"oat", "oats", "oatmeal"},
    "botella": {"bottle"},
    "caminar": {"walk", "walking"},
    "cama": {"bed", "bedroom"},
    "cena": {"dinner"},
    "desayuno": {"breakfast"},
    "dormir": {"sleep", "sleeping"},
    "fruta": {"fruit"},
    "luz": {"light"},
    "parque": {"park"},
    "sombra": {"shade"},
    "zapatos": {"shoe", "shoes"},
}


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-zA-ZáéíóúñÁÉÍÓÚÑ0-9]+", text.lower()) if len(token) > 2}


def _query_terms(query: str) -> set[str]:
    return {term for term in _tokens(query) if term not in STOPWORDS}


def _term_matches_tags(term: str, tag_terms: set[str]) -> bool:
    if term in tag_terms:
        return True
    return bool(TERM_SYNONYMS.get(term, set()) & tag_terms)


def _candidate_score(query: str, candidate: dict[str, Any]) -> dict[str, Any]:
    score = 0
    reasons = []
    width = int(candidate.get("width") or 0)
    height = int(candidate.get("height") or 0)
    if width >= 1920 and height >= 1080:
        score += 40
        reasons.append("high_resolution")
    elif width >= 1280 and height >= 720:
        score += 20
        reasons.append("usable_resolution")

    if width and height:
        ratio = width / height
        if abs(ratio - 16 / 9) < 0.12:
            score += 15
            reasons.append("landscape_16_9")

    tags_text = " ".join(str(tag).lower() for tag in candidate.get("tags") or [])
    tag_terms = _tokens(tags_text)
    raw_overlap = sorted(term for term in _tokens(query) if term in tag_terms)
    overlap = sorted(term for term in _query_terms(query) if _term_matches_tags(term, tag_terms))
    if overlap:
        score += min(30, len(overlap) * 8)
        reasons.append("tag_match")
        if len(overlap) >= 2:
            score += min(20, len(overlap) * 5)
            reasons.append("strong_scene_term_match")
    elif raw_overlap:
        reasons.append("generic_match_ignored")

    query_terms = _query_terms(query)
    penalized_terms = sorted(NEGATIVE_CONTEXT_TERMS & tag_terms)
    if penalized_terms and not query_terms.intersection(NEGATIVE_ALLOW_TERMS):
        score -= 25
        reasons.append("negative_keyword_penalty")

    if candidate.get("quality") in {"large2x", "fullhd", "original"}:
        score += 10
        reasons.append("preferred_quality")

    return {
        "score": score,
        "reasons": reasons or ["provider_order"],
        "matched_terms": overlap,
        "penalized_terms": penalized_terms,
    }


_ASSET_SELECTION_DEFAULTS: dict[str, Any] = {
    "enable_quality_scoring": True,
    "max_asset_candidates_per_provider": 12,
    "max_library_cache_candidates": 10,
    "max_candidate_metadata_score_total": 24,
    "quality_weight": 0.45,
}


def _asset_selection_config(visual_config: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(_ASSET_SELECTION_DEFAULTS)
    cfg.update(visual_config.get("asset_selection") or {})
    cfg.update((visual_config.get("shorts_quality") or {}).get("asset_selection") or {})
    return cfg


def _quality_norm(value: float) -> float:
    return max(0.0, min(1.0, value))


def _normalize_candidate_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    lo = min(scores)
    hi = max(scores)
    if hi == lo:
        return [1.0 for _ in scores]
    return [(score - lo) / (hi - lo) for score in scores]


def _candidate_haystack(candidate: dict[str, Any]) -> str:
    parts = list(candidate.get("tags") or [])
    for extra in ("alt", "description", "title", "query", "original_query"):
        if candidate.get(extra):
            parts.append(str(candidate[extra]))
    return " ".join(str(p) for p in parts).lower()


def _asset_quality_dimensions(scene: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    scene = scene or {}
    haystack = _candidate_haystack(candidate)
    first_frame_plan = scene.get("first_frame_plan") or {}
    evidence = scene.get("required_visual_evidence") or {}

    positive_terms: list[str] = []
    positive_terms.extend(str(term) for term in first_frame_plan.get("must_show") or [])
    if first_frame_plan.get("roi_target"):
        positive_terms.append(str(first_frame_plan["roi_target"]))
    positive_terms.extend(_evidence_terms(evidence, "required_actions", "required_objects"))
    positive_hits = [term for term in positive_terms if _term_hits(term, haystack)]

    avoid_terms = [str(term) for term in first_frame_plan.get("must_avoid") or []]
    avoid_terms.extend(_evidence_terms(evidence, "forbidden_pose", "forbidden_context", "forbidden_mood"))
    avoid_hits = [term for term in avoid_terms if _term_hits(term, haystack)]

    stock_terms = {
        "smiling", "family", "portrait", "pose", "posed", "wide", "generic", "stock",
        "centered", "kitchen",
    }
    stock_hits = sorted(term for term in stock_terms if term in haystack)
    importance_bonus = 12 if scene.get("visual_importance") == "critical" and positive_hits else 0
    first_frame_bonus = 10 if first_frame_plan and positive_hits else 0
    evidence_bonus = min(40, len(set(positive_hits)) * 12)
    avoid_penalty = min(35, len(set(avoid_hits)) * 12)
    stock_penalty = min(25, len(stock_hits) * 5)
    raw = 50 + importance_bonus + first_frame_bonus + evidence_bonus - avoid_penalty - stock_penalty
    return {
        "quality_raw": raw,
        "evidence_hits": positive_hits,
        "avoid_hits": avoid_hits,
        "stock_feeling_penalty": stock_penalty,
        "first_frame_bonus": first_frame_bonus,
        "visual_importance_bonus": importance_bonus,
    }


def _editorial_query(query: str, scene: dict[str, Any]) -> str:
    terms: list[str] = [query]
    first_frame_plan = scene.get("first_frame_plan") or {}
    if first_frame_plan.get("roi_target"):
        terms.append(str(first_frame_plan["roi_target"]))
    terms.extend(str(term) for term in first_frame_plan.get("must_show") or [])
    evidence = scene.get("required_visual_evidence") or {}
    terms.extend(_evidence_terms(evidence, "required_actions", "required_objects"))

    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        clean = str(term).strip()
        key = clean.lower()
        if clean and key not in seen:
            out.append(clean)
            seen.add(key)
    return " ".join(out)
