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
import sys
import tempfile
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from video_agent.assets.library import AssetLibrary
from video_agent.assets.providers import DEFAULT_HEADERS, StockPhotoClient
from video_agent.assets.query_cache import QueryCache
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
        # Keep the channel audience age-appropriate without forcing every asset
        # into an elderly/senior visual stereotype. The channel is 45+, not a
        # frailty/retirement channel.
        q = query
        q = re.sub(r"\b[Mm]iddle[- ]aged\b", "mature adult 45 plus", q)
        q = re.sub(r"\b[Aa]dults?\b", "mature adult", q)
        q = re.sub(r"\b45\+?\b", "45 plus", q)

        # Enforce European / Latin American / Hispanic / Caucasian
        if not any(w in lower_q for w in ["mature", "older", "45", "50", "sixties", "european", "latin", "hispanic", "caucasian", "elderly", "senior"]):
            q = f"{q} mature adult 45 plus"
        if not any(w in lower_q for w in ["european", "latin", "hispanic", "caucasian"]):
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
    # Pexels VIDEO carries NO tags and NO alt — with a tags-only haystack every
    # video scores zero overlap, fails the tag-overlap + demographic strict gate,
    # and the "prefer video" design (providers=["pexels_video"]) never yields a
    # single clip. When tags are structurally absent, fall back to the candidate's
    # own free-text metadata (alt/title/description — what photos carry), and only
    # if THAT is also empty (true for Pexels video) to query provenance: the asset
    # was returned + ranked by Pexels for our demographic-forced query, so the
    # enriched query terms (incl. "elderly/senior") are a sound on-topic signal.
    # Photos with real tags are unaffected (this branch is skipped).
    if not tag_terms:
        meta_text = " ".join(
            str(candidate.get(k) or "") for k in ("alt", "title", "description")
        ).strip().lower()
        tag_terms = _tokens(meta_text) if meta_text else _tokens(query.lower())
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


class StockSearchCore:
    """Shared, stateful stock-search machinery (T3b).

    Owns the query cache, asset library, stock client, download client, the
    per-run dedup sets, error/QA-rejection memory and the per-call candidate
    budget used while ranking and materializing stock candidates. Both pipelines'
    stock acquisition compose this core rather than re-implementing the search
    loop.

    Leaf module contract: this class only depends on ``video_agent.assets.*``
    and the stateless helpers in this module — never ``video_agent.stages`` or
    ``video_agent.shorts``.

    ``service`` is a back-reference to the owning ``StockAssetService``. It exists
    only so the search loop can reach ``_is_contradictory``, which still lives on
    the service and is relocated in a later refactor phase.
    """

    # Library-cache results without real semantic overlap have no place in the
    # final video (e.g. Vietnam Airlines clip returned for a "rutina nocturna"
    # prompt). Resolution / aspect-ratio bonuses alone are not enough — we also
    # require at least one tag term to match the query. When nothing clears the
    # bar we fall back to a fresh API search so the renderer never bakes a
    # placeholder into the output.
    _MIN_LIBRARY_CACHE_SCORE = 40
    _REQUIRE_LIBRARY_CACHE_TAG_MATCH = True

    def __init__(
        self,
        visual_config: dict[str, Any],
        *,
        service: Any,
        stock_client: StockPhotoClient | None = None,
        download_client: DownloadClient | None = None,
        vision_qa_fn: Any = None,
    ) -> None:
        self.service = service
        self.vision_qa_fn = vision_qa_fn
        # Retry memory: every Vision-QA rejection (scene, asset, missing evidence,
        # forbidden violations) — consumed by reports and later attempts.
        self.vision_rejections: list[dict[str, Any]] = []
        self.cache = QueryCache(
            _resolve_project_path(visual_config.get("query_cache_path"), "caches/query_cache.db")
        )
        self.library = AssetLibrary(
            _resolve_project_path(visual_config.get("asset_library_path"), "asset_library")
        )
        self.stock_client = stock_client or StockPhotoClient()
        self.download_client = download_client or UrlDownloadClient()
        self.used_provider_ids: set[tuple[str, str]] = set()
        self.used_asset_ids: set[tuple[str, str]] = set()  # (provider, asset_id)
        self.last_errors: list[dict[str, str]] = []
        self.asset_selection_config = _asset_selection_config(visual_config)

    def _new_candidate_budget(self) -> dict[str, int]:
        limit = int(self.asset_selection_config.get("max_candidate_metadata_score_total") or 24)
        return {"limit": max(0, limit), "scored_total": 0}

    @staticmethod
    def _candidate_budget_remaining(candidate_budget: dict[str, int] | None) -> int:
        if candidate_budget is None:
            return 10**9
        return max(0, int(candidate_budget.get("limit", 0)) - int(candidate_budget.get("scored_total", 0)))

    @staticmethod
    def _record_candidates_scored(candidate_budget: dict[str, int] | None, count: int) -> None:
        if candidate_budget is not None:
            candidate_budget["scored_total"] = int(candidate_budget.get("scored_total", 0)) + max(0, count)

    @staticmethod
    def _candidate_budget_debug(candidate_budget: dict[str, int] | None) -> dict[str, int]:
        if candidate_budget is None:
            return {"limit": 0, "scored_total": 0, "remaining": 0}
        return {
            "limit": int(candidate_budget.get("limit", 0)),
            "scored_total": int(candidate_budget.get("scored_total", 0)),
            "remaining": max(0, int(candidate_budget.get("limit", 0)) - int(candidate_budget.get("scored_total", 0))),
        }

    def _try_library_cache(
        self,
        query: str,
        media_type: str | None,
        channel_id: str,
        job_id: str,
        scene: dict[str, Any],
        candidate_budget: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        """Return a cached library asset matching the query, scored against it.

        Only returns when at least one candidate scores above
        ``_MIN_LIBRARY_CACHE_SCORE``. Lower-scoring cache hits are rejected so the
        caller falls back to a fresh provider search. The previous behavior was to
        return the first token-overlap match with score=0, which produced
        wildly off-topic backgrounds (airplane takeoff for a sleep-rest scene).
        """
        used_asset_ids = {aid for _, aid in self.used_asset_ids}
        library_cap = int(self.asset_selection_config.get("max_library_cache_candidates") or 10)
        cap = min(library_cap, self._candidate_budget_remaining(candidate_budget))
        if cap <= 0:
            return None
        candidates = self.library.search_by_query(
            query, media_type=media_type, exclude_asset_ids=used_asset_ids, limit=library_cap
        )

        valid_candidates: list[dict[str, Any]] = []
        for asset in candidates:
            provider = str(asset.get("provider") or "").lower()
            tier = str(asset.get("asset_tier") or "").lower()
            if provider == "graphic_fallback" or tier == "graphic_fallback":
                continue
            key = (asset["provider"], str(asset["provider_asset_id"]))
            if key in self.used_provider_ids:
                continue
            if not self.library.is_file_valid(asset):
                continue
            valid_candidates.append(asset)
            if len(valid_candidates) >= cap:
                break

        if not valid_candidates:
            return None

        ranked_cache = self._rank_candidates(
            query,
            valid_candidates,
            provider_order=1,
            scene=scene,
            candidate_budget=candidate_budget,
        )
        ranked_cache.sort(key=lambda item: (-item["score"], item["provider_order"], item["provider_candidate_rank"]))
        best = ranked_cache[0]
        best_score = best["score"]
        best_asset = best["candidate"]
        if best_score < self._MIN_LIBRARY_CACHE_SCORE:
            return None
        matched_terms = best.get("matched_terms") or []
        reasons = set(best.get("reasons") or [])
        # Require strong overlap (>=2 matched scene terms or the explicit
        # strong_scene_term_match reason). Resolution / aspect-ratio bonuses
        # alone push generic clips above the score threshold even when their
        # tags only share one filler word with the query.
        if self._REQUIRE_LIBRARY_CACHE_TAG_MATCH and not (
            "strong_scene_term_match" in reasons or len(matched_terms) >= 2
        ):
            return None

        self.used_provider_ids.add(
            (best_asset["provider"], str(best_asset["provider_asset_id"]))
        )
        self.used_asset_ids.add((best_asset["provider"], best_asset["asset_id"]))
        self.library.record_usage(
            best_asset["asset_id"],
            channel_id=channel_id,
            job_id=job_id,
            scene_id=scene["id"],
            scene_intent=scene.get("motion"),
        )
        best_asset["asset_selection"] = {
            "query": query,
            "source": "library_cache",
            "candidate_rank": 1,
            "searched_providers": [],
            "candidate_count": len(ranked_cache),
            "score": best_score,
            "base_raw_score": best.get("base_raw_score"),
            "base_norm": best.get("base_norm"),
            "quality_norm": best.get("quality_norm"),
            "final_norm": best.get("final_norm"),
            "quality_dimensions": best.get("quality_dimensions"),
            "quality_scoring_enabled": bool(self.asset_selection_config.get("enable_quality_scoring", True)),
            "candidate_budget": self._candidate_budget_debug(candidate_budget),
            "reasons": ["library_cache_hit", *(best.get("reasons") or [])],
            "matched_terms": best.get("matched_terms") or [],
            "candidate_tags": list(best_asset.get("tags", [])),
        }
        return best_asset

    # Demographic keywords that require a fresh API search (skip library cache to avoid
    # returning old videos of young people that were cached before this constraint existed).
    _DEMOGRAPHIC_KEYWORDS = {
        "elderly", "senior", "mature", "older", "european", "latin", "hispanic", "caucasian",
        "grandmother", "grandfather", "grandparent",
        # pipeline-specific visual_prompt patterns that will be rewritten
        "wellness", "adults", "realistic", "lifestyle", "photo",
    }

    def _query_requires_fresh_search(self, query: str) -> bool:
        """Return True when the query contains demographic enforcement keywords.

        The library cache uses token-overlap matching and cannot discriminate between
        'young woman in bed' and 'elderly European woman in bed' — it will always return
        the first token-match regardless of demographic constraints.  To guarantee we
        fetch fresh stock footage that actually shows elderly / European / Latin-American
        subjects, we bypass the library cache entirely for such queries.
        """
        tokens = set(re.findall(r"[a-z]+", query.lower()))
        return bool(tokens & self._DEMOGRAPHIC_KEYWORDS)

    def _search_and_download(
        self,
        *,
        providers: list[str],
        query: str,
        filters: dict[str, Any],
        ttl_hours: int,
        scene: dict[str, Any],
        channel_id: str,
        job_id: str,
        is_fallback: bool = False,
        require_strict: bool = False,
        candidate_budget: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        ranked_candidates: list[dict[str, Any]] = []
        for provider_order, provider in enumerate(providers, start=1):
            try:
                if self._candidate_budget_remaining(candidate_budget) <= 0:
                    break
                # bug: used_provider_ids stores the LIBRARY-normalized provider
                # name (e.g. "pexels", stripped of the "_video"/"_photo" search-
                # endpoint suffix -- see _ensure_asset's "strip _video suffix for
                # library lookup"), but `providers` here is the SEARCH-endpoint
                # name (e.g. "pexels_video"). Comparing them directly always
                # produced an empty exclude_ids, silently disabling the anti-
                # repeat/diversity exclusion for every video-tier search --
                # already-used clips could be (and were) picked again for a
                # later scene in the SAME video.
                normalized_provider = provider.replace("_video", "").replace("_photo", "")
                exclude_ids = {
                    asset_id for prov, asset_id in self.used_provider_ids
                    if prov == provider or prov == normalized_provider
                }
                response = self.cache.get(provider, query, filters)
                cached_unused_normalized: list[dict[str, Any]] | None = None
                if response is not None:
                    normalized = self.stock_client.normalize(provider, response)
                    unused_normalized = [
                        c for c in normalized
                        if str(c.get("provider_asset_id")) not in exclude_ids
                    ]
                    if not unused_normalized:
                        response = None
                    else:
                        cached_unused_normalized = unused_normalized

                if response is None:
                    try:
                        response = self.stock_client.search(provider, query, filters, exclude_ids=exclude_ids)
                    except TypeError as e:
                        if "unexpected keyword argument 'exclude_ids'" in str(e):
                            response = self.stock_client.search(provider, query, filters)
                        else:
                            raise
                    self.cache.set(provider, query, filters, response, ttl_hours=ttl_hours)
                # bug: a cache hit re-normalized the FULL cached response here,
                # discarding the exclude_ids filtering done above (unused_normalized)
                # -- an already-used asset (anti-repeat diversity) could win the
                # ranking again and get reused across scenes in the SAME video.
                # Reuse the already-filtered list on a cache hit; a fresh live
                # search still needs normalizing (and its own exclude filter, in
                # case the client doesn't honor exclude_ids server-side).
                if cached_unused_normalized is not None:
                    normalized = cached_unused_normalized
                else:
                    normalized = self.stock_client.normalize(provider, response)
                    normalized = [
                        c for c in normalized
                        if str(c.get("provider_asset_id")) not in exclude_ids
                    ]
                provider_cap = int(self.asset_selection_config.get("max_asset_candidates_per_provider") or 12)
                cap = min(provider_cap, self._candidate_budget_remaining(candidate_budget))
                candidates = self._rank_candidates(
                    query,
                    normalized[:cap],
                    provider_order=provider_order,
                    scene=scene,
                    candidate_budget=candidate_budget,
                )
                ranked_candidates.extend(candidates)
            except Exception as exc:
                err = {
                    "provider": provider,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                    "stage": "fallback" if is_fallback else "primary",
                }
                if err not in self.last_errors:
                    self.last_errors.append(err)
                # Surface provider failures to stderr so debugging stale
                # ``last_errors`` payloads is easier when the manifest already
                # captures them.
                print(
                    f"[stock] provider {provider!r} failed ({exc.__class__.__name__}): {exc}",
                    file=sys.stderr,
                )
                continue
        ranked_candidates = sorted(
            ranked_candidates,
            key=lambda item: (-item["score"], item["provider_order"], item["provider_candidate_rank"]),
        )

        def _passes_strict_gate(item: dict[str, Any]) -> bool:
            if item["score"] < self._MIN_LIBRARY_CACHE_SCORE:
                return False
            reasons = set(item.get("reasons") or [])
            matched = set(item.get("matched_terms") or [])
            prompt = query.lower()

            # Demographic strict enforcement gates on the SCENE's original
            # visual_prompt, NOT the enriched stock-search query. The enrichment
            # adds broad 45+ audience hints, but those should not force every
            # candidate to carry explicit age metadata; require demographic
            # evidence only when the scene itself asks for a specific age group.
            scene_prompt = str(scene.get("visual_prompt") or "").lower()
            demo_terms = {"senior", "elderly", "mature", "older", "50s", "60s", "aging", "grandfather", "grandmother", "grandparent", "55+"}
            if any(term in scene_prompt for term in demo_terms):
                has_demo = any(d in matched for d in demo_terms)
                if not has_demo:
                    item["failed_required_terms"] = "Missing demographic terms for a demographic-specific query"
                    return False

            # Check label reading strict context
            key_terms = {"package", "label", "ingredients", "fibra", "harina", "compare", "turn", "rotate", "flip", "back label", "back"}
            if any(term in prompt for term in key_terms):
                objects = {"bread", "package", "label", "ingredient"}
                contexts = {"supermarket", "grocery", "store", "aisle", "shelf", "shopping", "basket", "market", "checkout", "kitchen", "table"}

                turning_terms = {"turn", "rotate", "flip", "back", "turning", "rotating", "flipping"}
                reading_terms = {"read", "show", "reading", "showing", "check", "checking", "inspect", "inspecting"}

                has_obj = any(o in matched for o in objects)
                has_ctx = any(c in matched for c in contexts)

                has_turning_evidence = any(a in matched for a in turning_terms)
                has_reading_evidence = any(a in matched for a in reading_terms)

                requires_turning = any(term in prompt for term in turning_terms) or "back label" in prompt

                if requires_turning:
                    has_act = has_turning_evidence
                else:
                    has_act = has_turning_evidence or has_reading_evidence

                if not (has_obj and has_act and has_ctx):
                    item["failed_required_terms"] = "Missing object/action/context for label reading/turning"
                    return False

            # A single on-topic scene term clears the strict gate: it has already
            # survived STOPWORD filtering and the score floor above, so it is a
            # genuine topical match (e.g. a "sleep" photo for a "calm sleep
            # wellness bedroom" scene). Real, attributed stock that matches the
            # scene topic is higher quality — and far cheaper — than a slow AI
            # generation, so it must win at the strict tier instead of being
            # demoted to the weak tier that the AI fallback overrides. Off-topic
            # candidates (zero matched scene terms, e.g. an airplane clip for a
            # sleep scene) still fail here and correctly route to AI.
            return ("strong_scene_term_match" in reasons or len(matched) >= 1)

        unused_ranked_candidates = [
            item for item in ranked_candidates
            if (item["candidate"]["provider"], str(item["candidate"]["provider_asset_id"])) not in self.used_provider_ids
        ]
        any_strict = any(_passes_strict_gate(item) for item in unused_ranked_candidates)
        # Two-tier selection: prefer candidates that satisfy the strict semantic
        # gate (score + tag overlap). If none qualify (low-resolution mock data,
        # niche queries with sparse Pexels coverage, etc.) fall back to the
        # unfiltered ranking so we never bake a generated_placeholder when at
        # least *some* footage is available.
        for rank, ranked_candidate in enumerate(ranked_candidates, start=1):
            candidate = ranked_candidate["candidate"]
            key = (candidate["provider"], str(candidate["provider_asset_id"]))
            if key in self.used_provider_ids:
                continue
            is_strict = _passes_strict_gate(ranked_candidate)
            if require_strict and not is_strict:
                continue
            if any_strict and not is_strict:
                continue
            if not require_strict and not is_strict:
                if self.service._is_contradictory(scene, ranked_candidate):
                    # Skip contradictory weak candidates
                    continue
            try:
                asset = self._ensure_asset(candidate, query)
            except Exception:
                continue

            # --- Post-Asset QA for critical scenes ---
            # Injected vision_qa_fn (real pixel-level vision) takes precedence;
            # otherwise the built-in deterministic metadata gate enforces the
            # scene's required_visual_evidence against the asset's tags/alt so
            # frail/medical/off-pose stock never lands on a critical scene.
            if scene.get("visual_importance", "normal") == "critical":
                if self.vision_qa_fn is not None and asset.get("local_path"):
                    qa_result = self.vision_qa_fn(scene, asset["local_path"])
                else:
                    qa_result = metadata_evidence_qa(scene, candidate)
                if qa_result and qa_result.get("verdict") == "FAIL":
                        # Record failure reasons to asset_selection payload for transparency
                        asset_selection = asset.get("asset_selection") or {}
                        asset_selection["qa_failed"] = True
                        asset_selection["qa_missing_evidence"] = qa_result.get("missing_evidence", [])
                        asset_selection["qa_forbidden_violations"] = qa_result.get("forbidden_violations", [])
                        asset_selection["qa_reason"] = qa_result.get("reason", "")
                        # Retry memory: never offer this asset again in this run,
                        # and keep the structured rejection for later attempts.
                        self.used_provider_ids.add(key)
                        self.vision_rejections.append({
                            "scene_id": scene.get("id"),
                            "provider": asset.get("provider"),
                            "provider_asset_id": str(candidate.get("provider_asset_id")),
                            "missing_evidence": qa_result.get("missing_evidence", []),
                            "forbidden_violations": qa_result.get("forbidden_violations", []),
                            "reason": qa_result.get("reason", ""),
                        })
                        self.last_errors.append({
                            "provider": asset.get("provider"),
                            "error_type": "VisionQAFailure",
                            "message": f"QA Failed: {qa_result.get('reason')}",
                            "stage": "post_asset_qa"
                        })
                        continue

            self.used_provider_ids.add(key)
            self.used_asset_ids.add((asset["provider"], asset["asset_id"]))
            self.library.record_usage(
                asset["asset_id"],
                channel_id=channel_id,
                job_id=job_id,
                scene_id=scene["id"],
                scene_intent=scene.get("motion"),
            )
            asset["asset_selection"] = {
                "query": query,
                "source": "provider_search",
                "candidate_rank": rank,
                "provider_rank": ranked_candidate["provider_order"],
                "provider_candidate_rank": ranked_candidate["provider_candidate_rank"],
                "searched_providers": providers,
                "candidate_count": len(ranked_candidates),
                "score": ranked_candidate["score"],
                "base_raw_score": ranked_candidate.get("base_raw_score"),
                "base_norm": ranked_candidate.get("base_norm"),
                "quality_norm": ranked_candidate.get("quality_norm"),
                "final_norm": ranked_candidate.get("final_norm"),
                "quality_dimensions": ranked_candidate.get("quality_dimensions"),
                "quality_scoring_enabled": bool(self.asset_selection_config.get("enable_quality_scoring", True)),
                "candidate_budget": self._candidate_budget_debug(candidate_budget),
                "reasons": ranked_candidate.get("reasons") or [],
                "matched_terms": ranked_candidate.get("matched_terms") or [],
                "fallback": is_fallback,
                "weak_match": not is_strict,
                "failed_required_terms": ranked_candidate.get("failed_required_terms"),
                "fallback_reason": ranked_candidate.get("fallback_reason"),
                "candidate_tags": list(candidate.get("tags", [])),
            }
            return asset
        return None

    def _rank_candidates(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        provider_order: int,
        *,
        scene: dict[str, Any] | None = None,
        candidate_budget: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        ranked = []
        base_scorings = [_candidate_score(query, candidate) for candidate in candidates]
        self._record_candidates_scored(candidate_budget, len(base_scorings))
        base_norms = _normalize_candidate_scores([float(scoring["score"]) for scoring in base_scorings])
        quality_dimensions = [_asset_quality_dimensions(scene, candidate) for candidate in candidates]
        quality_norms = [_quality_norm(float(dim["quality_raw"]) / 100.0) for dim in quality_dimensions]
        quality_weight = float(self.asset_selection_config.get("quality_weight") or 0.45)
        quality_weight = _quality_norm(quality_weight)
        enable_quality = bool(self.asset_selection_config.get("enable_quality_scoring", True))
        for candidate_index, (candidate, scoring, base_norm, quality_norm, quality_dim) in enumerate(
            zip(
                candidates,
                base_scorings,
                base_norms,
                quality_norms,
                quality_dimensions,
                strict=True,
            ),
            start=1,
        ):
            if enable_quality:
                final_norm = (base_norm * (1.0 - quality_weight)) + (quality_norm * quality_weight)
                score = int(round(final_norm * 100))
            else:
                final_norm = base_norm
                score = scoring["score"]
            ranked.append(
                {
                    "candidate": candidate,
                    "provider_order": provider_order,
                    "provider_candidate_rank": candidate_index,
                    "base_raw_score": scoring["score"],
                    "base_norm": base_norm,
                    "quality_norm": quality_norm,
                    "final_norm": final_norm,
                    "quality_dimensions": quality_dim,
                    "score": score,
                    "reasons": scoring.get("reasons") or [],
                    "matched_terms": scoring.get("matched_terms") or [],
                    "penalized_terms": scoring.get("penalized_terms") or [],
                }
            )
        return ranked

    def _ensure_asset(self, candidate: dict[str, Any], query: str) -> dict[str, Any]:
        # Use normalized provider id (strip _video suffix for library lookup)
        lookup_provider = candidate["provider"].replace("_video", "")
        existing = self.library.get_by_provider_id(lookup_provider, str(candidate["provider_asset_id"]))
        if existing and self.library.is_file_valid(existing):
            return existing

        is_video = candidate.get("media_type") == "video"
        ext = ".mp4" if is_video else ".jpg"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / f"download{ext}"
            self.download_client.download(candidate["download_url"], temp_path)
            if is_video:
                return self.library.store_video(candidate, temp_path, original_query=query)
            return self.library.store_photo(candidate, temp_path, original_query=query)
