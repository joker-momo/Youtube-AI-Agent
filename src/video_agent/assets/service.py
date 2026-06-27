from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from video_agent.assets.library import AssetLibrary
from video_agent.assets.media_ops import extract_asset_frame  # extracted (P1)
from video_agent.assets.providers import StockPhotoClient
from video_agent.assets.stock_core import (  # extracted (P2/T3a)
    _evidence_terms,
    _term_hits,
    metadata_evidence_qa,
    _assert_safe_http_url,
    _SPANISH_TO_ENGLISH_KEYWORDS,
    _SPANISH_STOPWORDS_FOR_TRANSLATE,
    _is_likely_spanish_query,
    _translate_spanish_query_to_english,
    _force_elderly_demographic,
    DownloadClient,
    UrlDownloadClient,
    _resolve_project_path,
    _stock_filters,
    STOPWORDS,
    NEGATIVE_CONTEXT_TERMS,
    NEGATIVE_ALLOW_TERMS,
    TERM_SYNONYMS,
    _tokens,
    _query_terms,
    _term_matches_tags,
    _candidate_score,
    _ASSET_SELECTION_DEFAULTS,
    _asset_selection_config,
    _quality_norm,
    _normalize_candidate_scores,
    _candidate_haystack,
    _asset_quality_dimensions,
    _editorial_query,
)
from video_agent.assets.query_cache import QueryCache
from video_agent.contracts import repo_root
from video_agent.shorts.visual_acquisition import compile_span_search_queries
from video_agent.shorts.visual_candidate_scoring import (
    artifact_candidate_record,
    merge_query_origin,
    metadata_identity,
)
from video_agent.shorts.visual_candidate_scoring import (
    select_provisional_span_candidate as _select_provisional_span_candidate,
)

# extract_asset_frame + _IMAGE_SUFFIXES moved to assets/media_ops.py (P1).










# Safety net: when ChatGPT leaks Spanish into visual_prompt despite the prompt rule,
# we still try to send a meaningful English query to Pexels rather than the raw
# Spanish prose. This is keyword extraction, not real translation — adequate
# because Pexels stock search is token-based.









def _compact_payload_text(value: Any) -> str:
    """Flatten a layout payload into prompt text without losing its labels."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "; ".join(filter(None, (_compact_payload_text(item) for item in value)))
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            text = _compact_payload_text(item)
            if text:
                parts.append(f"{key}: {text}")
        return "; ".join(parts)
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _compact_list(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    return "; ".join(cleaned)


def _required_visual_evidence_text(scene: dict[str, Any]) -> str:
    evidence = scene.get("required_visual_evidence") or {}
    if not isinstance(evidence, dict):
        return ""
    sections: list[str] = []
    positive_fields = (
        ("required_actions", "actions"),
        ("required_objects", "objects"),
        ("subject_pose", "subject"),
        ("visibility", "visibility"),
    )
    negative_fields = (
        ("forbidden_pose", "forbidden pose"),
        ("forbidden_context", "avoid setting"),
        ("forbidden_mood", "forbidden mood"),
    )
    for key, label in positive_fields:
        text = _compact_list(evidence.get(key))
        if text:
            sections.append(f"{label}: {text}")
    for key, label in negative_fields:
        text = _compact_list(evidence.get(key))
        if text:
            sections.append(f"{label}: {text}")
    return "\n".join(sections)


def _graphic_layout_contract(layout: str, payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    if layout == "graphic_comparison":
        left = payload.get("left") if isinstance(payload.get("left"), dict) else {}
        right = payload.get("right") if isinstance(payload.get("right"), dict) else {}
        footer = str(payload.get("footer") or "").strip()
        lines = [
            "Layout contract: graphic_comparison.",
            "Use a clean side-by-side composition with two balanced panels over a realistic, softly blurred scene background.",
            "LEFT PANEL:",
            f"- heading exactly: {str(left.get('heading') or '').strip()}",
            f"- supporting line exactly: {str(left.get('text') or '').strip()}",
            "RIGHT PANEL:",
            f"- heading exactly: {str(right.get('heading') or '').strip()}",
            f"- supporting line exactly: {str(right.get('text') or '').strip()}",
        ]
        if footer:
            lines.append(f"Footer exactly: {footer}")
        lines.append("Do not invent extra numbers, calories, grams, medical claims, warning icons, red crosses, or good-versus-bad moral judgment.")
        return "\n".join(lines)
    if layout == "graphic_checklist":
        items = [str(item).strip() for item in (payload.get("items") or []) if str(item).strip()]
        lines = [
            "Layout contract: graphic_checklist.",
            "Create one saveable, premium checklist card integrated into a realistic editorial background.",
            "Show only these checklist items, in this order:",
        ]
        lines.extend(f"- {item}" for item in items)
        lines.append("Do not add extra checklist items, decorative icons that change meaning, tiny footnotes, or dense paragraphs.")
        return "\n".join(lines)
    if layout == "graphic_plate_ratio":
        segments = payload.get("segments") or []
        lines = [
            "Layout contract: graphic_plate_ratio.",
            "Create a clear plate/portion ratio visual with warm realistic food texture and readable segment labels.",
            "Use only these segments:",
        ]
        for segment in segments if isinstance(segments, list) else []:
            if isinstance(segment, dict):
                lines.append(
                    f"- {str(segment.get('label') or '').strip()}: {str(segment.get('value') or '').strip()}"
                )
        lines.append("Do not add calorie math, grams, scales, medical symbols, or extra ratios.")
        return "\n".join(lines)
    if layout == "graphic_label_callout":
        callouts = payload.get("callouts") or []
        lines = [
            "Layout contract: graphic_label_callout.",
            "Create a realistic product/label close-up with a few clean callouts, not a generic template.",
        ]
        product = str(payload.get("productLabel") or "").strip()
        if product:
            lines.append(f"Product label context: {product}")
        lines.append("Use only these callouts:")
        for callout in callouts if isinstance(callouts, list) else []:
            if isinstance(callout, dict):
                note = str(callout.get("note") or "").strip()
                line = (
                    f"- {str(callout.get('label') or '').strip()}: "
                    f"{str(callout.get('value') or '').strip()}"
                )
                if note:
                    line += f" ({note})"
                lines.append(line)
        lines.append("Do not add unrelated nutrition values, brand logos, warning stamps, or tiny legal-style copy.")
        return "\n".join(lines)
    return f"Layout contract: {layout}.\nUse the payload exactly; do not invent extra teaching points."


def build_scene_image_prompt(scene: dict[str, Any], query: str) -> str:
    """Build the ChatGPT image prompt for AI fallback scenes.

    For former ``graphic_*`` scenes the generated image must carry the same
    teaching content as the graphic payload, but as a richer editorial visual
    instead of a rigid renderer card.
    """
    layout = str(scene.get("layout") or "")
    payload = scene.get("layout_payload") or {}
    title = (
        str(payload.get("title") or "")
        if isinstance(payload, dict)
        else ""
    ).strip()
    on_screen = str(scene.get("on_screen_text") or title or "").strip()
    caption = str(scene.get("caption") or "").strip()
    narration = str(scene.get("narration") or "").strip()
    visual_prompt = str(scene.get("visual_prompt") or query or "").strip()
    payload_text = _compact_payload_text(payload)
    evidence_text = _required_visual_evidence_text(scene)
    source_graphic_layout = str(scene.get("generated_image_source_layout") or "").strip()
    graphic_layout = (
        layout
        if layout.startswith("graphic_")
        else (
            source_graphic_layout
            if source_graphic_layout.startswith("graphic_")
            else ("graphic_generated" if str(scene.get("visual_type") or "") == "graphic" or str(scene.get("asset_strategy") or "") == "graphic_fallback" else "")
        )
    )
    is_graphic = bool(graphic_layout)
    is_cta = layout in {"short_cta", "cta"} or str(scene.get("retention_function") or "") == "cta"
    is_hook = layout in {"short_hook", "hook"} or str(scene.get("retention_function") or "") == "hook"

    if is_hook:
        # The 3-second hook decides the swipe. A clean, human, emotional first
        # frame beats an empty-room stock clip. CLEAN background only (renderer
        # overlays the hook headline) — no trigger words so the driver keeps its
        # "no text overlays" guard.
        return (
            "A vertical medium close-up photograph of one calm adult aged 50 to 60 at home "
            "(living room or kitchen), face clearly visible and centered, expression subtly "
            "overwhelmed yet composed, gently holding a phone or a warm mug of tea, soft warm "
            "natural light, shallow depth of field, photorealistic editorial photography.\n"
            f"Scene mood: {caption or narration or visual_prompt}"
        )

    if is_graphic:
        layout_contract = _graphic_layout_contract(graphic_layout, payload if isinstance(payload, dict) else {})
        return (
            "Create a premium vertical editorial image for a Spanish wellness Short for adults 45+. "
            "Replace a flat renderer card with a natural, polished visual that still carries the teaching content exactly. "
            "Use warm Mediterranean light, realistic textures, tasteful magazine-style composition, and large legible Spanish typography inside mobile safe margins. "
            "Make the first frame useful and readable without zooming. "
            "Do not use a plain beige card, generic icons, stock-photo collage, watermark, tiny text, extra claims, or English wording.\n"
            f"Scene layout: {graphic_layout}\n"
            f"Scene visual idea: {visual_prompt}\n"
            f"Main headline to include exactly: {on_screen or title}\n"
            f"{layout_contract}\n"
            f"Full payload reference: {payload_text}\n"
            f"Required visual evidence:\n{evidence_text or 'Use the scene visual idea and payload as the source of truth.'}\n"
            f"Narration context: {narration or caption}\n"
            "Keep every written element short, high contrast, and visually integrated with the scene."
        )
    if is_cta:
        # CLEAN background only — the renderer overlays the CTA headline itself, so
        # the image must contain NO burned-in wording (a generated image with the
        # headline plus the renderer overlay produced two overlapping texts). Phrase
        # it WITHOUT trigger words (text/title/overlay/word/logo/watermark) so the
        # driver keeps its default "no text overlays, no watermark" guard.
        return (
            "A warm, inviting vertical wellness photograph for adults 45+. "
            "A calm mature 45+ adult in a peaceful walking or at-home wellness moment, "
            "soft natural golden light, gentle shallow depth of field, serene and hopeful mood, "
            "with plenty of calm empty space in the upper third of the frame. "
            "Photorealistic editorial photography.\n"
            f"Scene mood: {caption or narration or visual_prompt}"
        )
    if str(scene.get("asset_strategy") or "") == "ai_image_preferred" or evidence_text:
        return (
            "Create a premium photorealistic lifestyle image for a Spanish wellness Short for adults 45+. "
            "Use a realistic mature adult, warm natural Mediterranean light, calm editorial composition, and a clear opening action. "
            "The image should feel like a real photographed moment, not a template, stock collage, illustration, or poster.\n"
            f"Scene visual idea: {visual_prompt or query}\n"
            f"Required visual evidence:\n{evidence_text or 'Follow the scene visual idea exactly.'}\n"
            "No readable signage, captions, UI, numbers, commercial marks, medical symbols, scales, alarmist colors, or shame/fear mood."
        )
    return visual_prompt or query




































class StockAssetService:
    def __init__(
        self,
        visual_config: dict[str, Any],
        stock_client: StockPhotoClient | None = None,
        download_client: DownloadClient | None = None,
        image_gen_fn: Any = None,
        image_gen_recorder: Any = None,
        vision_qa_fn: Any = None,
    ) -> None:
        self.visual_config = visual_config
        self.providers = list(visual_config.get("providers") or ["pexels", "pixabay"])
        self.fallback_providers = list(visual_config.get("fallback_providers") or [])
        # Photo tier of the cascade: when no video provider yields a strict
        # (action-relevant) match we retry as stills. Default is derived from the
        # video providers (pexels_video -> pexels); explicit config overrides.
        configured_photo = list(visual_config.get("photo_providers") or [])
        if configured_photo:
            self.photo_providers = configured_photo
        else:
            derived: list[str] = []
            for p in self.providers:
                if "_video" in p:
                    still = p.replace("_video", "")
                    if still not in derived and still not in self.providers:
                        derived.append(still)
            self.photo_providers = derived
        # Optional last-resort tier: callable(prompt: str, out_path: Path) -> None
        # that renders an AI image for the scene's visual_prompt. When None the
        # cascade skips AI generation and uses an anti-blank weak stock match.
        self.image_gen_fn = image_gen_fn
        # Optional LLMHistoryRecorder: when set, every AI image-gen prompt sent to
        # ChatGPT is logged to the Short's prompt history (success + failure).
        self.image_gen_recorder = image_gen_recorder
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

    # Library-cache results without real semantic overlap have no place in the
    # final video (e.g. Vietnam Airlines clip returned for a "rutina nocturna"
    # prompt). Resolution / aspect-ratio bonuses alone are not enough — we also
    # require at least one tag term to match the query. When nothing clears the
    # bar we fall back to a fresh API search so the renderer never bakes a
    # placeholder into the output.
    _MIN_LIBRARY_CACHE_SCORE = 40
    _REQUIRE_LIBRARY_CACHE_TAG_MATCH = True

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
        "elderly", "senior", "european", "latin", "hispanic", "caucasian",
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

    def _is_key_scene(self, scene: dict[str, Any]) -> bool:
        layout = scene.get("layout") or ""
        if layout in {"short_hook", "short_cta", "graphic_label_callout", "graphic_comparison", "graphic_checklist"}:
            return True
        if scene.get("retention_function") in {"hook", "proof", "payoff", "cta"}:
            return True
        prompt = (scene.get("visual_prompt") or "").lower()
        key_terms = {"package", "label", "ingredients", "fibra", "harina", "compare", "turn", "rotate", "back label"}
        if any(term in prompt for term in key_terms):
            return True
        return False

    def _is_contradictory(self, scene: dict[str, Any], ranked_candidate: dict[str, Any]) -> bool:
        prompt = (scene.get("visual_prompt") or "").lower()
        candidate = ranked_candidate.get("candidate", {})
        tags = set(str(t).lower() for t in candidate.get("tags", []))

        # If the scene explicitly asks for supermarket/store and the asset tags have "sleep" or "bed", it's contradictory.
        if "supermarket" in prompt or "store" in prompt:
            if "sleep" in tags or "bed" in tags or "sleeping" in tags:
                return True

        # If the scene asks for reading/turning label, and asset is purely "slicing bread", it's contradictory.
        if ("turn" in prompt or "read" in prompt) and "label" in prompt:
            if "slice" in tags or "cutting" in tags:
                return True

        return False

    def get_scene_asset(self, scene: dict[str, Any], channel_id: str, job_id: str) -> dict[str, Any] | None:
        raw_query = scene.get("visual_prompt") or scene.get("on_screen_text") or ""
        # Safety net: even though prompts and validators require English visual_prompt,
        # if a Spanish prompt slips through we still translate it to English keywords
        # before sending it to Pexels (an English-keyword search engine).
        translated_query = _translate_spanish_query_to_english(raw_query)
        query = _force_elderly_demographic(translated_query)
        query = _editorial_query(query, scene)
        scene_dur = int(scene.get("duration_sec") or 30)
        filters = _stock_filters(self.visual_config, scene_duration_sec=scene_dur)
        ttl_hours = int(self.visual_config.get("query_cache_ttl_hours", 24))
        candidate_budget = self._new_candidate_budget()

        # Determine preferred media type from provider list
        prefers_video = any("video" in p for p in self.providers)
        media_type_hint = "video" if prefers_video else "photo"
        strategy = scene.get("asset_strategy", "stock_ok")
        visual_importance = scene.get("visual_importance", "normal")
        is_graphic_layout = str(scene.get("layout") or "").startswith("graphic_")

        # --- Library cache hit: skip API + download entirely ---
        # BYPASS cache when demographic keywords are present — the library token-overlap
        # search cannot enforce demographic constraints, so we must hit the API fresh.
        # Also bypass cache for graphic/image planning intents: they require an
        # explicit ChatGPT-generated asset, not a stale stock/placeholder asset
        # that merely matches query tokens.
        generated_strategy = strategy in {"graphic_fallback", "ai_image_preferred"}
        if (
            not is_graphic_layout
            and not generated_strategy
            and not self._query_requires_fresh_search(query)
        ):
            cached = self._try_library_cache(query, media_type_hint, channel_id, job_id, scene, candidate_budget)
            if cached is not None:
                return cached

        self.last_errors = []

        def _search(providers: list[str], *, require_strict: bool, is_fallback: bool = False):
            if not providers:
                return None
            return self._search_and_download(
                providers=providers,
                query=query,
                filters=filters,
                ttl_hours=ttl_hours,
                scene=scene,
                channel_id=channel_id,
                job_id=job_id,
                is_fallback=is_fallback,
                require_strict=require_strict,
                candidate_budget=candidate_budget,
            )

        is_key = visual_importance == "critical" or self._is_key_scene(scene)

        # 5-tier cascade based on asset_strategy

        # Tier 1 & 2: Strict Stock Search.
        # Explicit generated routes bypass stock entirely; their route decision
        # already said a controlled ChatGPT image is the desired asset.
        if strategy not in {"graphic_fallback", "ai_image_preferred"} and not is_graphic_layout:
            # Tier 1 — stock video, strict only.
            asset = _search(self.providers, require_strict=True)
            if asset is not None:
                asset["asset_tier"] = "pexels_video"
                asset["asset_selection"]["asset_match_status"] = "strong_match"
                return asset

            # Tier 2 — stock photo, strict only.
            asset = _search(self.photo_providers, require_strict=True)
            if asset is not None:
                asset["asset_tier"] = "pexels_photo"
                asset["asset_selection"]["asset_match_status"] = "strong_match"
                return asset

            # Tier 2b — strict fallback providers. A fallback provider is just an
            # alternate source (e.g. Pexels photos when the primary Pexels *video*
            # key is missing); a STRONG, attributed match from it is real on-topic
            # footage and must beat the slow AI tier, exactly like the primary
            # strict tiers. Only OFF-topic fallback stock stays below AI (Tier 4b).
            if self.fallback_providers:
                asset = _search(self.fallback_providers, require_strict=True, is_fallback=True)
                if asset is not None:
                    asset["asset_tier"] = "pexels_photo"
                    asset["asset_selection"]["asset_match_status"] = "strong_match"
                    return asset

        # Tier 3 — AI-generated image fallback.
        # Triggered for ANY non-graphic scene once strict stock has failed: an
        # on-brand AI image always beats an off-topic weak-match stock clip
        # (e.g. a "man photographing pasta" video standing in for a 55+ breathing
        # pause). Weak stock (Tier 4b) is kept only as the degraded path for when
        # AI generation is unavailable or fails. Quality > cost (PRIME DIRECTIVE).
        enable_ai_fallback = str(os.environ.get("ENABLE_AI_IMAGE_FALLBACK", "true")).lower() == "true"
        # Lazy AI policy: when this scene is covered by a native-video route,
        # skip expensive ChatGPT here. Generated graphic/image routes and failed
        # native routes explicitly re-enable this tier.
        skip_ai = bool(scene.get("_skip_ai_fallback"))
        ai_triggered = not skip_ai
        if ai_triggered and enable_ai_fallback and self.image_gen_fn is not None:
            # max retries logic is handled inside _ai_generate_scene_asset
            asset = self._ai_generate_scene_asset(scene, query, channel_id, job_id)
            if asset is not None:
                asset["asset_tier"] = "ai_image"
                asset["asset_selection"]["asset_match_status"] = "ai_generated"
                return asset

        # Structured graphic/image intents require a real ChatGPT image. Returning
        # the legacy graphic_fallback here would let Shorts silently reach a
        # placeholder/Remotion-card path after generation failed.
        if is_graphic_layout or strategy == "graphic_fallback":
            return None

        # Tier 4a — Text-led fallback for non-graphic key scenes only. Explicit
        # graphic/image strategies returned above if ChatGPT generation failed.
        if is_key:
            return {
                "provider": "graphic_fallback",
                "asset_id": f"graphic_{job_id}_{scene['id']}",
                "provider_asset_id": f"graphic_{job_id}_{scene['id']}",
                "media_type": "generated",
                "local_path": None, # Will be replaced by generated_placeholder in assets.py
                "attribution": "Graphic Fallback",
                "asset_tier": "graphic_fallback",
                "asset_selection": {
                    "query": query,
                    "source": "graphic_fallback",
                    "weak_match": False,
                    "asset_match_status": "graphic_fallback",
                    "reasons": ["key_scene_text_led_fallback"],
                    "matched_terms": [],
                },
            }

        # Tier 4b — Weak Pexels allowed only for non-key scenes if strategy is stock_ok
        if strategy == "stock_ok" and not is_key:
            asset = _search(self.providers, require_strict=False)
            if asset is not None:
                asset["asset_tier"] = "weak_pexels"
                asset["asset_selection"]["asset_match_status"] = "weak_match"
                return asset
            if self.fallback_providers:
                asset = _search(self.fallback_providers, require_strict=False, is_fallback=True)
                if asset is not None:
                    asset["asset_tier"] = "weak_pexels"
                    asset["asset_selection"]["asset_match_status"] = "weak_match"
                    return asset

        # Tier 5 — block + review (returns None)
        return None

    def get_visual_span_candidates(
        self,
        *,
        acquisition_context: dict[str, Any],
        budget: Any,
    ) -> list[dict[str, Any]]:
        """PR C metadata-only span search.

        Searches provider metadata for the complete visual span, deduplicates by
        provider identity (or normalized metadata identity), and returns artifact
        safe candidate records. It never downloads or materializes assets.
        """
        provider = str(
            (((self.visual_config.get("visual_quality_flow") or {}).get("acquisition") or {}).get("provider"))
            or (self.providers[0] if self.providers else "pexels_video")
        )
        providers = [provider] if provider else list(self.providers)
        queries = compile_span_search_queries(
            acquisition_context,
            locale=str(acquisition_context.get("locale") or "es-ES"),
            provider=provider,
        )
        flat_queries: list[tuple[str, str]] = []
        for query_class in ("primary", "alternates", "equivalent_action"):
            for query in queries.get(query_class) or []:
                flat_queries.append((query_class, query))
        flat_queries = flat_queries[: max(0, int(getattr(budget, "max_queries", 1)))]

        filters = _stock_filters(
            self.visual_config,
            scene_duration_sec=int(float(acquisition_context.get("planned_duration_sec") or 0.0)),
        )
        filters["per_page"] = int(getattr(budget, "metadata_candidates_per_query", filters.get("per_page", 8)))
        ttl_hours = int(self.visual_config.get("query_cache_ttl_hours", 24))
        config = ((self.visual_config.get("visual_quality_flow") or {}).get("acquisition") or {})
        records_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
        for query_class, query in flat_queries:
            for provider_name in providers:
                response = self.cache.get(provider_name, query, filters)
                if response is None:
                    try:
                        response = self.stock_client.search(provider_name, query, filters)
                    except Exception as exc:  # noqa: BLE001 - report-only metadata search degrades safely.
                        err = {
                            "provider": provider_name,
                            "error_type": exc.__class__.__name__,
                            "message": str(exc),
                            "stage": "visual_span_metadata_search",
                        }
                        if err not in self.last_errors:
                            self.last_errors.append(err)
                        continue
                    self.cache.set(provider_name, query, filters, response, ttl_hours=ttl_hours)
                for candidate in self.stock_client.normalize(provider_name, response):
                    identity = metadata_identity(candidate)
                    if identity in records_by_identity:
                        merge_query_origin(records_by_identity[identity], query=query, query_class=query_class)
                        continue
                    record = artifact_candidate_record(
                        candidate,
                        context=acquisition_context,
                        query=query,
                        query_class=query_class,
                        config=config,
                    )
                    records_by_identity[identity] = record
                    if len(records_by_identity) >= int(getattr(budget, "max_unique_metadata_candidates", 12)):
                        return list(records_by_identity.values())
        return list(records_by_identity.values())

    def select_provisional_span_candidate(
        self,
        *,
        acquisition_context: dict[str, Any],
        candidates: list[dict[str, Any]],
        recent_visual_memory: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """PR C metadata-only provisional selection. Never render-eligible."""
        return _select_provisional_span_candidate(
            acquisition_context=acquisition_context,
            candidates=candidates,
            recent_visual_memory=recent_visual_memory,
            config=config,
        )

    def _record_image_gen(
        self, prompt: str, scene: dict[str, Any], attempt: int, *,
        ok: bool, duration_ms: int = 0, error: str | None = None,
    ) -> None:
        """Log one AI image-gen prompt to the Short's history (no-op if unset)."""
        rec = self.image_gen_recorder
        if rec is None:
            return
        try:
            rec.record_image_gen(
                prompt,
                kind=f"image_gen:scene-{scene.get('id', '?')}:attempt-{attempt + 1}",
                ok=ok,
                duration_ms=duration_ms,
                error=error,
                response="image generated" if ok else "",
            )
        except Exception:  # pragma: no cover - logging must never break gen
            pass

    def _ai_generate_scene_asset(
        self, scene: dict[str, Any], query: str, channel_id: str, job_id: str
    ) -> dict[str, Any] | None:
        """Last-resort tier: render an AI image from the scene's visual_prompt."""
        import hashlib

        prompt = build_scene_image_prompt(scene, query).strip()
        if not prompt:
            return None

        aspect_ratio = "9:16"
        style_version = "v1"
        model_name = "dall-e-3"
        prompt_hash = hashlib.sha256(f"{prompt}_{aspect_ratio}_{style_version}_{model_name}".encode()).hexdigest()[:16]

        out_dir = Path(self.library.root) / "ai_generated"
        out_path = out_dir / f"{job_id}_{scene['id']}_{prompt_hash}.png"

        # Cache check
        if not (out_path.exists() and out_path.stat().st_size > 1024):
            try:
                out_dir.mkdir(parents=True, exist_ok=True)

                # The browser-worker image driver (build_image_gen_prompt) is the
                # single source of truth for the dimension/format instruction and
                # runs contradiction checks (text/watermark/border) against the
                # prompt. Pass the raw scene/CTA prompt so the instruction is added
                # exactly once — a manual copy here duplicated it and bypassed the
                # contradiction stripping (e.g. CTA wants readable text but the
                # buried copy still said "no commentary").
                full_prompt = prompt

                # Browser-worker refuses to write outside the 'jobs/' directory.
                # So we must tell it to write to a temp file inside the job dir, then move it.
                temp_path = Path(f"jobs/{job_id}/assets/ai_temp_{scene['id']}_{prompt_hash}.png").resolve()
                temp_path.parent.mkdir(parents=True, exist_ok=True)

                # Attempt generation with 1 retry
                import time as _time
                for attempt in range(2):
                    _t0 = _time.monotonic()
                    try:
                        self.image_gen_fn(full_prompt, temp_path)
                        import shutil
                        if temp_path.exists():
                            shutil.move(str(temp_path), str(out_path))
                        self._record_image_gen(
                            full_prompt, scene, attempt, ok=True,
                            duration_ms=int((_time.monotonic() - _t0) * 1000),
                        )
                        break
                    except Exception as e:
                        self._record_image_gen(
                            full_prompt, scene, attempt, ok=False, error=f"{type(e).__name__}: {e}",
                            duration_ms=int((_time.monotonic() - _t0) * 1000),
                        )
                        if attempt == 1:
                            raise e
            except Exception as exc:  # pragma: no cover - defensive
                self.last_errors.append(
                    {"provider": "ai_generated", "error_type": exc.__class__.__name__, "message": str(exc), "stage": "ai_gen"}
                )
                return None

        # Sanity Checks
        if not out_path.exists():
            return None
        if out_path.stat().st_size < 1024:
            # File exists but is too small (blank/corrupt)
            return None

        # Optional: check aspect ratio or if it's all black/white if PIL is available
        # (Assuming file size > 1KB is a basic sanity check for non-blank for now)

        asset_id = f"ai_{job_id}_{scene['id']}"
        try:
            self.library.record_usage(
                asset_id, channel_id=channel_id, job_id=job_id,
                scene_id=scene["id"], scene_intent=scene.get("motion"),
            )
        except Exception:  # pragma: no cover - library is best-effort here
            pass

        return {
            "provider": "ai_generated",
            "asset_id": asset_id,
            "provider_asset_id": asset_id,
            "media_type": "image",
            "local_path": str(out_path),
            "attribution": "AI-generated (ChatGPT image)",
            "asset_tier": "ai_image",
            "asset_selection": {
                "query": prompt,
                "source": "ai_generated",
                "weak_match": False,
                "asset_match_status": "ai_generated",
                "reasons": ["ai_generated"],
                "matched_terms": [],
            },
        }

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
                exclude_ids = {asset_id for prov, asset_id in self.used_provider_ids if prov == provider}
                response = self.cache.get(provider, query, filters)
                if response is not None:
                    normalized = self.stock_client.normalize(provider, response)
                    unused_normalized = [
                        c for c in normalized
                        if str(c.get("provider_asset_id")) not in exclude_ids
                    ]
                    if not unused_normalized:
                        response = None

                if response is None:
                    try:
                        response = self.stock_client.search(provider, query, filters, exclude_ids=exclude_ids)
                    except TypeError as e:
                        if "unexpected keyword argument 'exclude_ids'" in str(e):
                            response = self.stock_client.search(provider, query, filters)
                        else:
                            raise
                    self.cache.set(provider, query, filters, response, ttl_hours=ttl_hours)
                provider_cap = int(self.asset_selection_config.get("max_asset_candidates_per_provider") or 12)
                normalized = self.stock_client.normalize(provider, response)
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

            # Demographic strict enforcement — gate on the SCENE's original
            # visual_prompt, NOT the enriched ``query``. ``_force_elderly_demographic``
            # appends "elderly european senior" to every wellness/photo query, so
            # keying off ``query`` would demand an "elderly"/"senior" *tag* on every
            # candidate — which real stock rarely carries — and reject good on-topic
            # footage down into the slow AI tier. We only require demographic
            # evidence when the scene itself asks for a specific age group.
            scene_prompt = str(scene.get("visual_prompt") or "").lower()
            demo_terms = {"senior", "elderly", "mature", "older", "50s", "60s", "aging", "grandfather", "grandmother", "grandparent", "55+"}
            if any(term in scene_prompt for term in demo_terms):
                has_demo = any(d in matched for d in demo_terms)
                if not has_demo:
                    item["failed_required_terms"] = "Missing demographic terms (senior/elderly) for a demographic-specific query"
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
                if self._is_contradictory(scene, ranked_candidate):
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
