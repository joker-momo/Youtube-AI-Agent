"""Shorts visual-span asset service.

Moved from ``video_agent.assets.service`` (T4): the PR C metadata-only span
search/selection plus the AI image-generation tier. ``ShortSpanAssetService``
holds a :class:`~video_agent.assets.stock_core.StockSearchCore` by composition
and reuses the parent service's stock/library/cache machinery through it.

This module is Shorts-only and must not import ``video_agent.stages.*``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.assets.stock_core import StockSearchCore, _stock_filters
from video_agent.shorts.assets.image_prompt import build_scene_image_prompt
from video_agent.shorts.visual_acquisition import compile_span_search_queries
from video_agent.shorts.visual_candidate_scoring import (
    artifact_candidate_record,
    merge_query_origin,
    metadata_identity,
)
from video_agent.shorts.visual_candidate_scoring import (
    select_provisional_span_candidate as _select_provisional_span_candidate,
)


class ShortSpanAssetService:
    """Stateful Shorts span search + AI image generation.

    Composes a shared :class:`StockSearchCore` (query cache, asset library,
    stock/download clients, dedup sets, error/QA memory) rather than subclassing
    it. The owning :class:`~video_agent.assets.service.StockAssetService` builds
    its own ``core`` and hands the same instance here so both layers share one
    cache and one error log.
    """

    def __init__(
        self,
        visual_config: dict[str, Any],
        *,
        core: StockSearchCore,
        providers: list[str],
        image_gen_fn: Any = None,
        image_gen_recorder: Any = None,
    ) -> None:
        self.visual_config = visual_config
        self.core = core
        self.providers = providers
        # Standalone fallbacks. When this service is owned by a StockAssetService
        # (the normal case) the live attributes below defer to that owner via
        # ``self.core.service`` so runtime reassignment (e.g. ``svc.image_gen_fn =
        # fake`` in tests/callers) is honored — preserving pre-T4 behavior.
        self._image_gen_fn = image_gen_fn
        self._image_gen_recorder = image_gen_recorder

    @classmethod
    def for_visual_config(
        cls,
        visual_config: dict[str, Any],
        *,
        stock_client: Any = None,
        download_client: Any = None,
        image_gen_fn: Any = None,
        image_gen_recorder: Any = None,
        vision_qa_fn: Any = None,
    ) -> ShortSpanAssetService:
        """Build a standalone span service that owns its own ``StockSearchCore``.

        Used by the Shorts metadata-acquisition stage, which only needs the span
        search/selection surface (no full per-scene cascade). The metadata-only
        span flow never reaches the weak-match path, so the core needs no owning
        service (``_is_contradictory``); the span service keeps its own
        ``image_gen_fn`` / ``image_gen_recorder`` for the AI tier.
        """
        providers = list(visual_config.get("providers") or ["pexels", "pixabay"])
        core = StockSearchCore(
            visual_config,
            service=None,
            stock_client=stock_client,
            download_client=download_client,
            vision_qa_fn=vision_qa_fn,
        )
        return cls(
            visual_config,
            core=core,
            providers=providers,
            image_gen_fn=image_gen_fn,
            image_gen_recorder=image_gen_recorder,
        )

    @property
    def image_gen_fn(self) -> Any:
        """Last-resort tier: callable(prompt: str, out_path: Path) -> None.

        Prefers the owning service's live attribute so reassigning it on the
        ``StockAssetService`` after construction takes effect here too.
        """
        owner = getattr(self.core, "service", None)
        if owner is not None and hasattr(owner, "image_gen_fn"):
            return owner.image_gen_fn
        return self._image_gen_fn

    @property
    def image_gen_recorder(self) -> Any:
        """LLMHistoryRecorder for AI image-gen prompts (live from the owner)."""
        owner = getattr(self.core, "service", None)
        if owner is not None and hasattr(owner, "image_gen_recorder"):
            return owner.image_gen_recorder
        return self._image_gen_recorder

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
                response = self.core.cache.get(provider_name, query, filters)
                if response is None:
                    try:
                        response = self.core.stock_client.search(provider_name, query, filters)
                    except Exception as exc:  # noqa: BLE001 - report-only metadata search degrades safely.
                        err = {
                            "provider": provider_name,
                            "error_type": exc.__class__.__name__,
                            "message": str(exc),
                            "stage": "visual_span_metadata_search",
                        }
                        if err not in self.core.last_errors:
                            self.core.last_errors.append(err)
                        continue
                    self.core.cache.set(provider_name, query, filters, response, ttl_hours=ttl_hours)
                for candidate in self.core.stock_client.normalize(provider_name, response):
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

        out_dir = Path(self.core.library.root) / "ai_generated"
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
                self.core.last_errors.append(
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
            self.core.library.record_usage(
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
