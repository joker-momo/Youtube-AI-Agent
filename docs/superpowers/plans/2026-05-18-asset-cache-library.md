# Asset Cache Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Phase 2A photo-only query cache and asset library for Pexels/Pixabay-backed scene images.

**Architecture:** Add focused modules under `src/video_agent/assets/` for query caching, provider normalization/search, durable library storage, and scene asset selection. Keep `src/video_agent/stages/assets.py` as the pipeline integration point and preserve local-directory/placeholder fallbacks.

**Tech Stack:** Python standard library SQLite/urllib/hashlib, Pillow for image dimensions, Docker-only verification through `docker compose run`.

---

### Task 1: Query Cache

**Files:**
- Create: `src/video_agent/assets/query_cache.py`
- Test: `tests/test_asset_query_cache.py`

- [ ] Write tests for query normalization, cache miss, cache hit, hit count, and expiry.
- [ ] Run tests in Docker and verify they fail because the module is missing.
- [ ] Implement SQLite-backed `QueryCache`, `normalize_query`, and `query_cache_key`.
- [ ] Run the query cache tests in Docker and verify they pass.

### Task 2: Provider Normalization

**Files:**
- Create: `src/video_agent/assets/providers.py`
- Test: `tests/test_stock_photo_providers.py`

- [ ] Write fixture-based tests for Pexels and Pixabay response normalization.
- [ ] Run tests in Docker and verify they fail because provider functions are missing.
- [ ] Implement provider-specific search clients and normalization helpers.
- [ ] Run provider tests in Docker and verify they pass.

### Task 3: Asset Library

**Files:**
- Create: `src/video_agent/assets/library.py`
- Test: `tests/test_asset_library.py`

- [ ] Write tests for storing a downloaded photo, reusing by provider asset id, verifying SHA256, and recording usage.
- [ ] Run tests in Docker and verify they fail because the library is missing.
- [ ] Implement SQLite schema, file naming, hash verification, image metadata extraction, and usage tracking.
- [ ] Run asset library tests in Docker and verify they pass.

### Task 4: Pipeline Integration

**Files:**
- Create: `src/video_agent/assets/service.py`
- Modify: `src/video_agent/stages/assets.py`
- Test: `tests/test_assets_stage.py`

- [ ] Extend tests to cover `visuals.strategy: stock_photo_api` with a fake provider client and local image bytes.
- [ ] Run tests in Docker and verify they fail because stock API mode is unsupported.
- [ ] Implement scene-level stock photo selection, query cache lookup, asset library reuse/download, job asset copy, and fallback to placeholders.
- [ ] Run asset stage tests in Docker and verify they pass.

### Task 5: Docker Verification

**Files:**
- Modify: `.gitignore` if needed for durable local-only cache directories.

- [ ] Run focused asset tests in Docker.
- [ ] Run full pytest suite in Docker.
- [ ] Run a no-render pipeline smoke test in Docker with missing API keys and verify placeholder fallback.
- [ ] Confirm no API keys are written to tracked files, manifests, or logs.
