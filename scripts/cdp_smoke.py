#!/usr/bin/env python3
"""CDP attach smoke check (bridge 20260709).

Attaches to the browser runtime through the same bounded, retrying helper the
worker uses and prints a machine-readable verdict. Run this before the real image
gate to tell — without an opaque 500 — whether ChatGPT image generation can run.

    PYTHONPATH=src .venv/bin/python scripts/cdp_smoke.py

Exit code 0 = attach healthy, 1 = degraded (structured reason on stdout).
"""
from __future__ import annotations

import asyncio
import json
import sys


def main() -> int:
    from video_agent.browser_worker.app import cdp_attach_health

    result = asyncio.run(cdp_attach_health())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
