"""Transcript Lab — standalone FastAPI app (port 8750).

Run:
    cd tools/transcript_lab
    pip install -r requirements.txt
    python app.py

NOT part of the main video_agent pipeline. No shared imports.
"""

from __future__ import annotations

import json
import subprocess

# tool venv is Python 3.9 — use timezone.utc, NOT datetime.UTC (3.11+ only).
from datetime import datetime, timezone
from pathlib import Path

import downloader
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fetcher import TranscriptResult, fetch_transcript
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"
# Heavy analysis runs in the MAIN project venv (has cv2/numpy/soundfile); the web
# app stays in the light tool venv. Process-separate, no code import of video_agent.
MAIN_VENV_PY = HERE.parent.parent / ".venv" / "bin" / "python"
ANALYZER = HERE / "analyzer.py"
PORT = 8750

app = FastAPI(title="transcript-lab", version="0.1.0")


class FetchRequest(BaseModel):
    urls: list[str]


def _save(result: TranscriptResult) -> dict[str, str]:
    """Persist a successful result as .txt + .json; return relative file paths."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    vid = result.video_id
    txt_path = OUTPUT_DIR / f"{vid}.txt"
    json_path = OUTPUT_DIR / f"{vid}.json"

    txt_path.write_text(result.text, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "url": result.url,
                "video_id": vid,
                "lang": result.lang,
                "source": result.source,
                "fetched_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 (tool venv is py3.9)
                "segments": [
                    {"start": s.start, "text": s.text} for s in result.segments
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"txt": f"output/{vid}.txt", "json": f"output/{vid}.json"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "index.html")


@app.post("/fetch")
def fetch(req: FetchRequest) -> JSONResponse:
    # Dedupe + drop blank lines while preserving order.
    seen: set[str] = set()
    urls: list[str] = []
    for raw in req.urls:
        u = raw.strip()
        if u and u not in seen:
            seen.add(u)
            urls.append(u)

    results = []
    for url in urls:
        result = fetch_transcript(url)
        entry: dict[str, object] = {
            "url": result.url,
            "video_id": result.video_id,
            "ok": result.ok,
            "lang": result.lang,
            "source": result.source,
            "error": result.error,
        }
        if result.ok:
            entry["text"] = result.text
            entry["text_chars"] = len(result.text)
            entry["files"] = _save(result)
        results.append(entry)

    return JSONResponse({"results": results})


def _analyze_one(url: str) -> dict:
    """Download (tool venv) then analyze (main venv subprocess) one URL."""
    try:
        dl = downloader.download(url, OUTPUT_DIR)
    except Exception as exc:  # noqa: BLE001 - report per-URL, keep batch going
        return {"url": url, "ok": False, "stage": "download", "error": str(exc)}

    job_dir = Path(dl["dir"])
    # Ensure a transcript exists so the analyzer can compute WPM (word_count).
    # analyzer._word_count looks for OUTPUT_DIR/<id>.txt.
    txt_path = OUTPUT_DIR / f"{dl['video_id']}.txt"
    if not txt_path.exists():
        tr = fetch_transcript(url)
        if tr.ok:
            txt_path.write_text(tr.text, encoding="utf-8")

    if not MAIN_VENV_PY.exists():
        return {"url": url, "video_id": dl["video_id"], "ok": False,
                "stage": "analyze", "error": f"main venv python not found: {MAIN_VENV_PY}"}
    proc = subprocess.run(
        [str(MAIN_VENV_PY), str(ANALYZER), str(job_dir)],
        capture_output=True, text=True, timeout=3600,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        return {"url": url, "video_id": dl["video_id"], "ok": False,
                "stage": "analyze", "error": tail[-1] if tail else "analyzer failed"}

    analysis = json.loads((job_dir / "analysis.json").read_text(encoding="utf-8"))
    meta = analysis.get("metadata", {})
    return {
        "url": url,
        "video_id": dl["video_id"],
        "ok": True,
        "title": meta.get("title"),
        "duration_sec": analysis["format"].get("duration_sec"),
        "shot_count": analysis["composition"].get("shot_count"),
        "cuts_per_min": analysis["composition"].get("cuts_per_min"),
        "wpm": analysis["voice"].get("wpm"),
        "f0_median_hz": analysis["voice"].get("f0_median_hz"),
        "integrated_lufs": analysis["audio"].get("integrated_lufs"),
        "frames": len(analysis.get("keyframes", [])),
        "files": {
            "analysis": f"output/{dl['video_id']}/analysis.json",
            "frames_dir": f"output/{dl['video_id']}/frames/",
            "thumbnail": f"output/{dl['video_id']}/thumbnail.jpg",
        },
    }


@app.post("/analyze")
def analyze(req: FetchRequest) -> JSONResponse:
    seen: set[str] = set()
    urls: list[str] = []
    for raw in req.urls:
        u = raw.strip()
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    results = [_analyze_one(url) for url in urls]
    return JSONResponse({"results": results})


# Serve saved artifacts (transcripts, frames, analysis.json) for the UI links.
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")


if __name__ == "__main__":
    print(f"Transcript Lab → http://localhost:{PORT}")
    uvicorn.run(app, host="127.0.0.1", port=PORT)
