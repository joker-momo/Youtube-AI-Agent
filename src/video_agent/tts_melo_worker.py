"""MeloTTS sidecar worker — runs inside the melo venv, driven over JSON-over-stdio.

MeloTTS pins old transformers/librosa and needs a Japanese dict, which would clash
with the project venv. So it lives in a separate venv (``tools/melo-venv``) and is
spoken to by :class:`video_agent.tts.MeloTTSClient` via this worker: the model is
loaded once at startup, then each stdin line ``{"text","speed","out"}`` produces a
wav and gets a ``{"ok","sample_rate"}`` reply. Imports stay limited to MeloTTS +
stdlib so the script runs under the sidecar interpreter with no project deps.
"""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="tts-melo-worker")
    parser.add_argument("--language", default="ES")
    parser.add_argument("--device", default="mps")
    args = parser.parse_args()

    # MeloTTS prints progress/log noise to stdout; keep the JSON protocol on the
    # real stdout and divert everything else (melo prints, tqdm) to stderr.
    protocol = sys.stdout
    sys.stdout = sys.stderr

    def send(obj: dict) -> None:
        protocol.write(json.dumps(obj) + "\n")
        protocol.flush()

    try:
        from melo.api import TTS

        model = TTS(language=args.language, device=args.device)
        speaker_id = list(model.hps.data.spk2id.values())[0]
        sample_rate = int(model.hps.data.sampling_rate)
    except Exception as exc:  # surface load failure to the parent, then exit
        send({"ready": False, "error": repr(exc)})
        return 1

    send({"ready": True, "sample_rate": sample_rate})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            model.tts_to_file(req["text"], speaker_id, req["out"], speed=float(req.get("speed", 1.0)))
            send({"ok": True, "sample_rate": sample_rate})
        except Exception as exc:
            send({"ok": False, "error": repr(exc)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
