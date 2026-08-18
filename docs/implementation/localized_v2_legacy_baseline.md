# Localized V2 legacy baseline

This baseline freezes the last completed Vida Plena production job before the
localized V2 sidecar was introduced. It is a release gate, not a migration
input: V2 never reads the live job, legacy queue, or legacy runtime state.

## Frozen production evidence

- Baseline commit: `7584152`
- Job:
  `how-to-cook-potatoes-to-lower-their-glycemic-index-for-bette-vida-plena-45-20260730-122514`
- Queue status: `completed`
- Queue completion: `2026-07-30 07:04:17` UTC
- Final video: H.264/AAC, 1920x1080, 30 fps source contract, 792.1 seconds
- Fixture:
  `tests/localized_v2/fixtures/legacy_vida_plena/manifest.json`

The fixture contains copies of small promoted JSON artifacts plus hashes for
the production prompts, final video, protected channel configuration, and every
file tracked at the baseline commit. It intentionally does not copy the video,
audio, thumbnails, raw provider responses, or provider credentials.

## Reproduce

Run only against a completed legacy job:

```bash
.venv/bin/python scripts/capture_legacy_localization_baseline.py \
  --repo-root "$PWD" \
  --job-dir jobs/how-to-cook-potatoes-to-lower-their-glycemic-index-for-bette-vida-plena-45-20260730-122514 \
  --queue-db jobs/queue.db \
  --output-dir tests/localized_v2/fixtures/legacy_vida_plena \
  --baseline-ref 7584152
```

The command opens the legacy queue read-only, refuses non-terminal jobs, writes
to a temporary sibling directory, and atomically promotes the completed
fixture. It also refuses capture when any pre-existing tracked file differs
from the baseline commit.

## Verification

```bash
.venv/bin/python -m pytest -q tests/localized_v2/test_legacy_isolation.py
```

The isolation suite proves:

1. copied artifact hashes and stage order remain frozen;
2. every pre-existing tracked file still has its baseline SHA-256;
3. importing the legacy worker does not import `video_agent.localized_v2`;
4. legacy `render.concurrency` remains `"auto"`;
5. non-terminal jobs cannot be captured; and
6. repository changes are additive and restricted to the KTD1 allowlist.
