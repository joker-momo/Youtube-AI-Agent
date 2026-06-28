#!/usr/bin/env bash
# Build the MeloTTS sidecar venv used for the Elena voice (provider: melo).
#
# MeloTTS pins old transformers/librosa and needs a Japanese dict, which would
# clash with the project .venv and break the kokoro/pipeline path — so it lives
# in its own venv here. src/video_agent/tts.py MeloTTSClient drives it via a
# worker (src/video_agent/tts_melo_worker.py). This venv is gitignored; rebuild
# it on a fresh checkout with this script.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON311:-python3.11}"
command -v "$PY" >/dev/null 2>&1 || { echo "error: need python3.11 (set PYTHON311=...)"; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { echo "error: ffmpeg required (brew install ffmpeg)"; exit 1; }
command -v espeak-ng >/dev/null 2>&1 || echo "warn: espeak-ng not found (brew install espeak-ng)"

rm -rf tools/melo-venv
"$PY" -m venv tools/melo-venv
tools/melo-venv/bin/python -m pip install -q --upgrade pip "setuptools<81"
# MeloTTS (MIT) from source; pulls its own (older) transformers/librosa.
tools/melo-venv/bin/python -m pip install -q "git+https://github.com/myshell-ai/MeloTTS.git"
# MeloTTS eagerly imports the Japanese module → needs the unidic dict even for ES.
tools/melo-venv/bin/python -m unidic download
tools/melo-venv/bin/python -c "from melo.api import TTS; print('MeloTTS sidecar venv ready')"

echo "Done. tools/melo-venv ready. channel tts.provider: melo"
