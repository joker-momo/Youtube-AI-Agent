#!/usr/bin/env python3
"""Backfill v5.4 visual-diversity columns on the existing asset library DB.

By default runs in --dry-run mode: prints what would be updated without
touching the database. Pass --write to apply changes.

Usage:
    python scripts/backfill_asset_visual_metadata.py \\
        --channel-id vida-plena-45 \\
        --visual-dna configs/vida-plena-45/visual-dna.yaml \\
        --asset-db asset_library/metadata.db \\
        --dry-run
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import yaml  # noqa: E402

from video_agent.assets.library import AssetLibrary  # noqa: E402
from video_agent.assets.visual_diversity.backfill import backfill_asset  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill asset visual-diversity metadata")
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--visual-dna", required=True, type=Path)
    parser.add_argument("--asset-db", required=True, type=Path)
    parser.add_argument("--since", help="YYYY-MM-DD: only backfill assets downloaded since this date")
    parser.add_argument("--limit", type=int, default=0)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--write", action="store_true")
    return parser.parse_args()


def _load_visual_dna(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def main() -> int:
    args = _parse_args()
    if not args.visual_dna.exists():
        print(f"visual-dna file not found: {args.visual_dna}", file=sys.stderr)
        return 2
    if not args.asset_db.exists():
        print(f"asset DB not found: {args.asset_db}", file=sys.stderr)
        return 2

    visual_dna = _load_visual_dna(args.visual_dna)
    write_mode = bool(args.write) and not args.dry_run

    library_root = args.asset_db.parent
    library = AssetLibrary(library_root)  # ensures migration applied

    sql = "SELECT * FROM assets WHERE is_banned = 0"
    params: list = []
    if args.since:
        sql += " AND downloaded_at >= ?"
        params.append(args.since)
    sql += " ORDER BY downloaded_at ASC"
    if args.limit > 0:
        sql += f" LIMIT {int(args.limit)}"

    updated = 0
    skipped = 0
    with sqlite3.connect(str(args.asset_db)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(sql, params).fetchall()
        for row in rows:
            asset = dict(row)
            patch = backfill_asset(asset, visual_dna)
            if not patch:
                skipped += 1
                continue
            print(f"[{asset['asset_id']}] -> {sorted(patch.keys())}")
            if write_mode:
                library.update_visual_metadata(asset["asset_id"], **patch)
            updated += 1

    mode = "DRY-RUN" if not write_mode else "WROTE"
    print(f"{mode}: updated={updated} skipped={skipped} total={updated + skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
