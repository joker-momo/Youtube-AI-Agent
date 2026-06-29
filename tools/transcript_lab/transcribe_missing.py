"""Whisper-transcribe (full text) any downloaded job missing a transcript .txt.

Competitor videos with no captions only got a word count earlier; this saves the
full Spanish text to output/<id>.txt so the script can be evaluated. Runs in the
MAIN venv (has whisper). Slow: ~real-time-ish per video on M2.

Run:  .venv/bin/python tools/transcript_lab/transcribe_missing.py
"""

from __future__ import annotations

from pathlib import Path

import whisper

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"


def main() -> None:
    jobs = sorted(
        d for d in OUTPUT_DIR.iterdir()
        if d.is_dir() and (d / "audio.wav").exists()
        and not (OUTPUT_DIR / f"{d.name}.txt").exists()
    )
    print(f"to transcribe: {[d.name for d in jobs]}", flush=True)
    if not jobs:
        print("nothing to do", flush=True)
        return
    model = whisper.load_model("base")
    for d in jobs:
        try:
            res = model.transcribe(str(d / "audio.wav"), language="es", fp16=False)
            text = str(res.get("text", "")).strip()
            (OUTPUT_DIR / f"{d.name}.txt").write_text(text, encoding="utf-8")
            print(f"OK {d.name}: {len(text.split())} words", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {d.name}: {exc}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
