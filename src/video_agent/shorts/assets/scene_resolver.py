"""Shorts per-scene asset cascade (T5).

Forked from ``video_agent.assets.service.StockAssetService.get_scene_asset`` so
the Shorts pipeline owns its scene-resolution policy independently of the long
pipeline. The two intentionally diverge: the long path never reaches the AI
image tier, while Shorts always may, so duplicating the stock-tier skeleton is
the accepted cost of the fork.

This module is Shorts-only and must not import ``video_agent.stages.*`` (the
asset-layer boundary). It composes the shared, stateless/stateful primitives
from ``video_agent.assets.*`` and the Shorts span service for the AI tier.
"""

from __future__ import annotations

import os
from typing import Any

from video_agent.assets.stock_core import (
    StockSearchCore,
    _editorial_query,
    _force_elderly_demographic,
    _stock_filters,
    _translate_spanish_query_to_english,
)
from video_agent.shorts.assets.span_candidates import ShortSpanAssetService


class ShortSceneResolver:
    """Stateful per-scene stock + graphic + AI cascade for Shorts.

    Holds a :class:`~video_agent.assets.stock_core.StockSearchCore` (query cache,
    asset library, stock/download clients, dedup sets, error/QA memory) and a
    :class:`~video_agent.shorts.assets.span_candidates.ShortSpanAssetService` that
    shares the SAME ``core`` so the metadata cache, asset library and error log
    stay unified. The AI image tier is delegated to that span service.
    """

    def __init__(
        self,
        visual_config: dict[str, Any],
        stock_client: Any = None,
        download_client: Any = None,
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
        # Shared, stateful search machinery. ``service=self`` so the weak-match
        # path can call back into ``_is_contradictory``.
        self.core = StockSearchCore(
            visual_config,
            service=self,
            stock_client=stock_client,
            download_client=download_client,
            vision_qa_fn=vision_qa_fn,
        )
        # Shorts span search + AI image generation. Composes the SAME ``self.core``
        # so the metadata cache, asset library and error log stay unified. The AI
        # tier below is delegated to this service.
        self._span = ShortSpanAssetService(
            visual_config,
            core=self.core,
            providers=self.providers,
            image_gen_fn=image_gen_fn,
            image_gen_recorder=image_gen_recorder,
        )

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
        candidate_budget = self.core._new_candidate_budget()

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
            and not self.core._query_requires_fresh_search(query)
        ):
            cached = self.core._try_library_cache(query, media_type_hint, channel_id, job_id, scene, candidate_budget)
            if cached is not None:
                return cached

        self.core.last_errors = []

        def _search(providers: list[str], *, require_strict: bool, is_fallback: bool = False):
            if not providers:
                return None
            return self.core._search_and_download(
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
                "local_path": None, # Will be replaced by generated_placeholder in prepare.py
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

    def _ai_generate_scene_asset(
        self, scene: dict[str, Any], query: str, channel_id: str, job_id: str
    ) -> dict[str, Any] | None:
        """Last-resort AI image tier (delegated to ShortSpanAssetService).

        Kept as an instance method so ``get_scene_asset`` reaches it via ``self``
        and tests can monkeypatch it on the resolver instance.
        """
        return self._span._ai_generate_scene_asset(scene, query, channel_id, job_id)
