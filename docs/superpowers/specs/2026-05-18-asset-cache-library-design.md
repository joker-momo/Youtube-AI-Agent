# Asset Cache Library Phase 2A Design

Date: 2026-05-18
Status: Draft for user review

## Decision

Implement Phase 2A as a photo-only asset system:

- Query Cache: avoid repeated provider API calls for the same search within 24 hours.
- Asset Library: store downloaded provider photos once and reuse them across jobs.
- Asset Usage Tracking: record which channel/job/scene used each asset.

Do not implement CLIP embeddings, semantic search, video stock assets, archive automation, or cross-channel exclusivity in this phase. Leave schema room for those later only where it does not complicate the MVP.

## Goals

- Turn provider images into reusable local assets.
- Respect Pexels and Pixabay API guidance.
- Keep API keys out of git and job artifacts.
- Keep the render pipeline Docker-first.
- Preserve the existing render contract: Remotion receives local job asset references, not provider URLs.
- Make the system useful immediately for the current `vida-plena-45` MVP.

## Non-Goals

- No Sora or browser-based ChatGPT image workflow.
- No paid image generation API integration.
- No CLIP or semantic index yet.
- No stock video pipeline yet.
- No YouTube upload integration.
- No automatic long-term cold-storage/archive policy.

## External Provider Rules

Pexels:

- Use the API key in the `Authorization` header.
- Store source photo URLs, photographer names, and photographer profile URLs.
- Credit photographers when possible.
- Keep a visible provider attribution path available for descriptions/reports.
- Track rate-limit headers when returned.

Pixabay:

- Use the API key as the `key` query parameter.
- Cache API responses for 24 hours.
- Do not permanently hotlink provider media URLs.
- Download selected files into the local asset library.
- Store source page URL, contributor username, contributor profile URL, and tags.
- Avoid systematic mass downloads.

## Directory Layout

```text
project_root/
├── asset_library/
│   ├── photos/
│   │   ├── pexels/YYYY/MM/
│   │   └── pixabay/YYYY/MM/
│   └── metadata.db
├── caches/
│   └── query_cache.db
└── jobs/
    └── <job_id>/
        └── assets/
            ├── scene-01.jpg
            └── scene-02.jpg
```

`asset_library/` is durable project memory. `caches/` is disposable provider response cache. `jobs/` remains local generated output and stays gitignored.

## Query Cache

Use SQLite at `caches/query_cache.db`.

Schema:

```sql
CREATE TABLE IF NOT EXISTS query_cache (
    cache_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    query_original TEXT NOT NULL,
    query_normalized TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    response_json TEXT NOT NULL,
    results_count INTEGER NOT NULL,
    cached_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_query_cache_provider_query
    ON query_cache(provider, query_normalized);

CREATE INDEX IF NOT EXISTS idx_query_cache_expires
    ON query_cache(expires_at);
```

Cache key:

```text
{provider}:{normalized_query}:{filters_hash}
```

Normalization:

- lowercase
- trim
- remove punctuation
- collapse whitespace
- remove common English stop words
- sort remaining words to make equivalent word-order queries share a key

TTL is 24 hours. Expired entries are ignored and can be deleted by a later cleanup script.

## Asset Library

Use SQLite at `asset_library/metadata.db`.

Schema:

```sql
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_asset_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    file_hash TEXT NOT NULL,
    perceptual_hash TEXT,
    width INTEGER,
    height INTEGER,
    aspect_ratio TEXT,
    original_url TEXT,
    original_query TEXT,
    provider_tags_json TEXT,
    photographer TEXT,
    photographer_url TEXT,
    attribution TEXT,
    license TEXT,
    downloaded_at TEXT NOT NULL,
    last_used_at TEXT,
    use_count INTEGER NOT NULL DEFAULT 0,
    is_banned INTEGER NOT NULL DEFAULT 0,
    banned_reason TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_provider_id
    ON assets(provider, provider_asset_id);

CREATE INDEX IF NOT EXISTS idx_assets_phash
    ON assets(perceptual_hash);

CREATE INDEX IF NOT EXISTS idx_assets_use_count
    ON assets(use_count);
```

Usage tracking:

```sql
CREATE TABLE IF NOT EXISTS asset_usage (
    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    scene_intent TEXT,
    used_at TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
);

CREATE INDEX IF NOT EXISTS idx_usage_asset
    ON asset_usage(asset_id);

CREATE INDEX IF NOT EXISTS idx_usage_channel
    ON asset_usage(channel_id);

CREATE INDEX IF NOT EXISTS idx_usage_job
    ON asset_usage(job_id);
```

File naming:

```text
asset_library/photos/{provider}/{YYYY}/{MM}/{provider}_{provider_asset_id}_{quality}.{ext}
```

Examples:

```text
asset_library/photos/pexels/2026/05/pexels_6793199_large2x.jpg
asset_library/photos/pixabay/2026/05/pixabay_7009836_fullhd.jpg
```

## Provider Selection

Phase 2A supports:

- Pexels photo search
- Pixabay photo search

Provider keys are read from environment variables:

```text
PEXELS_API_KEY
PIXABAY_API_KEY
```

Keys are never written to config, manifests, logs, or committed files.

Provider result normalization should produce a common shape:

```json
{
  "provider": "pexels",
  "provider_asset_id": "6793199",
  "media_type": "photo",
  "download_url": "https://...",
  "source_url": "https://www.pexels.com/photo/...",
  "width": 6550,
  "height": 4367,
  "tags": [],
  "photographer": "Yaroslav Shuraev",
  "photographer_url": "https://www.pexels.com/@yaroslav-shuraev",
  "attribution": "Photo by Yaroslav Shuraev on Pexels"
}
```

## Asset Flow

For each scene:

1. Build one provider query from the scene visual prompt.
2. Look up Query Cache by provider, normalized query, and filters.
3. If cache hit and unexpired, use cached provider results.
4. If cache miss, call provider API and write raw response into Query Cache.
5. Normalize provider results.
6. Pick a small candidate set.
7. For each selected candidate, check Asset Library by `provider + provider_asset_id`.
8. If existing file hash verifies, reuse it.
9. If missing or corrupt, download again and update metadata.
10. Copy the chosen library file into `jobs/<job_id>/assets/scene-XX.jpg`.
11. Record `asset_usage`.
12. Write the normal job `assets_manifest.json`.

The renderer never reads remote URLs. It only reads copied job assets.

## Manifest Additions

Job `assets_manifest.json` should include enough attribution for reports and later upload descriptions:

```json
{
  "scene_id": "scene-01",
  "background": "/absolute/job/assets/scene-01.jpg",
  "public_background": "jobs/<job_id>/assets/scene-01.jpg",
  "source": "asset_library",
  "asset_id": "uuid",
  "provider": "pexels",
  "provider_asset_id": "6793199",
  "source_url": "https://www.pexels.com/photo/...",
  "attribution": "Photo by Yaroslav Shuraev on Pexels"
}
```

## Fallbacks

- If a provider key is missing, skip that provider.
- If all configured providers fail, fall back to existing placeholder generation.
- If a selected library file is missing or hash mismatch, redownload it.
- If redownload fails, mark the candidate failed for the run and try the next candidate.
- If provider response returns too few results, use what is available and fill remaining scenes through fallback.

## Configuration

Channel config can evolve from:

```yaml
visuals:
  strategy: "mock_local"
```

to:

```yaml
visuals:
  strategy: "stock_photo_api"
  providers: ["pexels", "pixabay"]
  query_cache_ttl_hours: 24
  scene_count_target: 5
  orientation: "landscape"
```

The existing `local_directory` mode remains useful for ChatGPT-browser downloads and manual asset curation.

## Testing

Unit tests:

- query normalization produces stable keys
- query cache hit/miss/expiry
- provider response normalization for Pexels and Pixabay fixtures
- asset library insert/reuse by provider asset id
- corrupted file detection by SHA256 mismatch
- job asset copy writes scene files and manifest metadata

Integration tests:

- run pipeline with provider fixtures and no network
- run pipeline with missing API keys and verify placeholder fallback
- run existing MVP tests unchanged

Manual demo:

- fetch 5 Pexels images with real API key passed by environment
- render one `vida-plena-45` demo
- verify output `video.mp4` is 1920x1080 and 54 seconds
- verify `assets_manifest.json` records `source: asset_library`

## Future Phases

Phase 2B:

- perceptual hash duplicate ranking
- cross-channel reuse policy
- basic analytics queries

Phase 2C:

- CLIP embeddings
- semantic search
- FAISS only when brute-force search becomes slow

Phase 3:

- stock video assets
- archive automation
- dashboard
