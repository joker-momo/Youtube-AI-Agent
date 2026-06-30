from __future__ import annotations

from typing import Any

from video_agent.assets.providers import StockPhotoClient
from video_agent.assets.stock_core import (  # extracted (P2/T3a, T3b)
    DownloadClient,
    StockSearchCore,
    _editorial_query,
    _force_elderly_demographic,
    _stock_filters,
    _translate_spanish_query_to_english,
)

# The per-scene graphic/ChatGPT-AI tiers and the Shorts visual-span methods live
# in the Shorts fork (video_agent.shorts.assets) after the P4 asset-layer
# decoupling; this long-facing service keeps only the stock cascade. The moved
# stock helpers (search/score/translate/cache) now live in assets.stock_core and
# are imported there directly by callers, not re-exported through this module.


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
        # Shared, stateful search machinery (T3b). Owns the query cache, asset
        # library, stock/download clients, dedup sets, error/QA-rejection memory
        # and the candidate budget. Held by composition: every reference to a
        # moved method/state goes through ``self.core``.
        self.core = StockSearchCore(
            visual_config,
            service=self,
            stock_client=stock_client,
            download_client=download_client,
            vision_qa_fn=vision_qa_fn,
        )

    def _is_key_scene(self, scene: dict[str, Any]) -> bool:
        layout = scene.get("layout") or ""
        if layout in {"short_hook", "short_cta", "graphic_label_callout", "graphic_comparison", "graphic_checklist"}:
            return True
        if scene.get("retention_function") in {"hook", "proof", "payoff", "cta"}:
            return True
        prompt = (scene.get("visual_prompt") or "").lower()
        # Label/package-reading cues that need the EXACT product on screen (key
        # scene → strict-only, no weak fallback). "ingredients" was removed: it is
        # the subject word of a nutrition channel (almost every food scene mentions
        # it), so it wrongly forced generic scenes — incl. the hook — to be
        # critical-no-fallback and rendered them as a blank gradient when strict
        # failed. The remaining terms are specific label/comparison reads.
        key_terms = {"package", "label", "fibra", "harina", "compare", "turn", "rotate", "back label"}
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
        # --- Library cache hit: skip API + download entirely ---
        # BYPASS cache when demographic keywords are present — the library token-overlap
        # search cannot enforce demographic constraints, so we must hit the API fresh.
        if not self.core._query_requires_fresh_search(query):
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

        # Stock-only cascade (long-facing). The graphic_*/ChatGPT-AI tiers moved to
        # the Shorts fork (video_agent.shorts.assets.scene_resolver) in the P4
        # asset-layer decoupling; this service resolves stock footage only.

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
        # footage.
        if self.fallback_providers:
            asset = _search(self.fallback_providers, require_strict=True, is_fallback=True)
            if asset is not None:
                asset["asset_tier"] = "pexels_photo"
                asset["asset_selection"]["asset_match_status"] = "strong_match"
                return asset

        # Weak Pexels — allowed only for non-key scenes when strategy is stock_ok
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

        # Tier 4.5 — broadened-query retry before giving up. A compound
        # visual_prompt ("fish, eggs, chicken, legumes, tofu, yogurt") can match
        # NOTHING on Pexels; retry with a simplified query (first clause, few words)
        # so the scene gets REAL on-topic footage (weak_match) instead of a blank
        # placeholder. Only reached after every tier above failed, so it never
        # changes scenes that already matched.
        _vp = str(scene.get("visual_prompt") or "").strip()
        _low = _vp.lower()
        _cut = len(_vp)
        for _m in (",", " including ", " with ", " and ", " featuring ", " plus "):
            _i = _low.find(_m)
            if _i != -1:
                _cut = min(_cut, _i)
        _broad_raw = " ".join(_vp[:_cut].split()[:4])
        if _broad_raw:
            broad_q = _editorial_query(_broad_raw, scene)
            if broad_q and broad_q != query:
                for _provs, _tier, _fb in (
                    (self.providers, "pexels_video", False),
                    (self.fallback_providers, "pexels_photo", True),
                ):
                    if not _provs:
                        continue
                    asset = self.core._search_and_download(
                        providers=_provs, query=broad_q, filters=filters,
                        ttl_hours=ttl_hours, scene=scene, channel_id=channel_id,
                        job_id=job_id, is_fallback=_fb, require_strict=False,
                        candidate_budget=candidate_budget,
                    )
                    if asset is not None:
                        asset["asset_tier"] = _tier
                        asset["asset_selection"]["asset_match_status"] = "weak_match"
                        return asset

        # Tier 5 — block + review (returns None)
        return None
