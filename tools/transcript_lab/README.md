# Transcript Lab

Standalone tool — fetch transcripts/subtitles from a batch of YouTube URLs and save
them locally. For studying competitor video **structure** only (hooks, beats, pacing).
Do **not** copy other channels' content verbatim; auto-captions also misspell names,
numbers, and terms.

> Independent of the main `video_agent` pipeline. Own deps, own port (8750). Nothing
> here imports from, or is imported by, `src/video_agent/`.

## Install & run

```bash
cd tools/transcript_lab
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py            # http://localhost:8750
```

Open the URL, paste one YouTube URL per line, click **Fetch All**.

## How it works

- Language priority: **es** → en.
- Tier 1: `youtube-transcript-api` (manual + auto captions, with timestamps).
- Tier 2 (fallback): `yt-dlp` subtitles → SRT → parsed.
- No whisper/audio fallback. A video with no subtitles returns a clear error; one
  failing URL does not abort the batch.

## Output

Saved under `output/` (gitignored):

- `<video_id>.txt` — plain text, one segment per line.
- `<video_id>.json` — `{ url, video_id, lang, source, fetched_at, segments }`.

## Tests

```bash
cd tools/transcript_lab
python -m pytest tests/ -q     # network-free
```

## Video Teardown (download + analyze)

Second feature: download a competitor video (480p) + audio and compute objective
metrics for a full teardown. UI: "Download & Analyze" panel, or `POST /analyze
{urls}`.

- **Download** (`downloader.py`, tool venv): yt-dlp 480p video + 16k mono wav +
  metadata (title/description/tags/views/likes/date). Retries across
  `player_client` (android/tv/web) to dodge HTTP 403.
- **Analyze** (`analyzer.py`, runs in the **main project `.venv`** via subprocess —
  has cv2/numpy/soundfile; does NOT import video_agent): composition (shot count,
  cuts/min, shot length, motion), color (saturation/brightness/contrast), audio
  (LUFS, true-peak, silence ratio, pause stats, music-under-speech heuristic),
  voice (WPM from transcript + speaking time, F0 register via numpy autocorr),
  and ~20 keyframes for vision review.
- Output: `output/<id>/` → video.mp4, audio.wav, frames/NN.jpg, analysis.json,
  meta.json. Served at `/output/<id>/...`.

**Teardown workflow:** run analyze → give Claude the `analysis.json` + the 20
frames → Claude writes the full teardown (composition/VFX/voice/audio + compare to
our channel). `music_under_speech` and deep VFX/shot-type are heuristics/vision,
not exact. tags may be missing (YouTube hides them).

> NOTE: the tool venv is **Python 3.9** — keep app.py/downloader.py/fetcher.py
> 3.9-compatible (use `timezone.utc`, not `datetime.UTC`). analyzer.py runs in the
> 3.11 main venv.
