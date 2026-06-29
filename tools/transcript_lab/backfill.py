"""Backfill voice WPM for already-downloaded teardown jobs.

Older /analyze runs analyzed before transcript-fetch + whisper-fallback existed,
so word_count/wpm came out null. This re-runs the CURRENT analyzer (which fetches
the transcript or falls back to Whisper) WITHOUT re-downloading the video.

Waits until no analyzer.py / yt-dlp is running so it never races the live batch.

Run (tool venv):  ./.venv/bin/python backfill.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from fetcher import fetch_transcript

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"
MAIN_VENV_PY = HERE.parent.parent / ".venv" / "bin" / "python"
ANALYZER = HERE / "analyzer.py"


def _busy() -> bool:
    """True if a foreign analyzer.py / yt-dlp is running (avoid racing the batch)."""
    out = subprocess.run(["pgrep", "-fl", "analyzer.py|yt_dlp|yt-dlp"],
                         capture_output=True, text=True)
    lines = [ln for ln in out.stdout.splitlines() if "backfill.py" not in ln]
    return bool(lines)


def _needs_backfill(job_dir: Path) -> bool:
    if not (job_dir / "video.mp4").exists():
        return False
    a = job_dir / "analysis.json"
    if not a.exists():
        return True
    try:
        return json.loads(a.read_text())["voice"]["wpm"] is None
    except Exception:  # noqa: BLE001
        return True


def backfill_one(job_dir: Path) -> dict:
    vid = job_dir.name
    txt = OUTPUT_DIR / f"{vid}.txt"
    source = "existing-transcript"
    if not txt.exists():
        tr = fetch_transcript(f"https://www.youtube.com/watch?v={vid}")
        if tr.ok:
            txt.write_text(tr.text, encoding="utf-8")
            source = "fetched-transcript"
        else:
            source = "whisper-fallback"
    proc = subprocess.run([str(MAIN_VENV_PY), str(ANALYZER), str(job_dir)],
                         capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        return {"video_id": vid, "ok": False, "error": tail[-1] if tail else "failed"}
    wpm = json.loads((job_dir / "analysis.json").read_text())["voice"]["wpm"]
    return {"video_id": vid, "ok": True, "source": source, "wpm": wpm}


def main() -> None:
    while _busy():
        print("batch still running, waiting 20s...", flush=True)
        time.sleep(20)
    targets = sorted(d for d in OUTPUT_DIR.iterdir()
                    if d.is_dir() and _needs_backfill(d))
    print(f"backfilling {len(targets)}: {[d.name for d in targets]}", flush=True)
    for d in targets:
        print(backfill_one(d), flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    sys.exit(main())
