# YouTube AI Agent

A Docker-first, standalone local automated YouTube video production system.

The system is designed around a standalone Python web application with a FastAPI interface, WebSocket-based real-time progress updates, a background task queue runner, and a containerized headless browser appliance for automated session interaction.

---

## 1. System Architecture (V3)

The YouTube AI Agent consists of four main containerized services running on an internal Docker network:

```
  ┌─────────────────────────────────────────────────────────────┐
  │                        User Browser                         │
  └──────────────────────────────┬──────────────────────────────┘
                                 │
                   HTTP / WS     ▼ (Port 8000 & 7900)
  ┌─────────────────────────────────────────────────────────────┐
  │                      Web Dashboard (App)                    │
  ├─────────────────────────────────────────────────────────────┤
  │ - FastAPI & WebSocket Real-time UI                          │
  │ - State Orchestrator & Pipeline Stages                      │
  └──────────────────────────────┬──────────────────────────────┘
                                 │
                   Reads/Writes  ▼ SQLite (queue.db, jobs.db)
  ┌─────────────────────────────────────────────────────────────┐
  │                  Background Task Worker                     │
  ├─────────────────────────────────────────────────────────────┤
  │ - Sequential Queue Runner                                   │
  │ - Remotion Rendering Engine (ffmpeg, Node.js)               │
  │ - Kokoro TTS Local Synthesis                                │
  └──────────────────────────────┬──────────────────────────────┘
                                 │
                   HTTP calls    ▼ (Port 8001)
  ┌─────────────────────────────────────────────────────────────┐
  │                       Browser Worker                        │
  ├─────────────────────────────────────────────────────────────┤
  │ - Playwright Driver API                                     │
  │ - Headless Session Management & Humanized Inputs            │
  └──────────────────────────────┬──────────────────────────────┘
                                 │
                   CDP Port 9222 ▼ (Internal Network)
  ┌─────────────────────────────────────────────────────────────┐
  │                     Browser Appliance                       │
  ├─────────────────────────────────────────────────────────────┤
  │ - persisted Chromium Profile (/browser_profiles/default)    │
  │ - KasmVNC Desktop Display (Port 7900 for manual sign-ins)   │
  └─────────────────────────────────────────────────────────────┘
```

### Key Services

1. **Web Dashboard (`app` service, Port `8000`)**:
   Provides a Single Page Application (SPA) dashboard to manage jobs, trigger runs, review stages, and download deliverables. Supports live log streaming and timeline status updates via WebSockets.
2. **Background Worker (`worker` service)**:
   A daemon worker running off a SQLite queue (`queue.db`) that executes long-running tasks such as text-to-speech (TTS) synthesis, asset retrieval, and video rendering.
3. **Browser Worker (`browser-worker` service, Port `8001`)**:
   Exposes APIs to drive Chromium via Playwright CDP (Chrome DevTools Protocol). Translates orchestrator commands into automated browser actions for ChatGPT and Gemini.
4. **Browser Appliance (`browser-runtime` service, Port `7900` VNC)**:
   A containerized Chromium running under Xvfb and Fluxbox. It mounts a persistent profile directory (`browser_profiles/default`) so that manual sign-ins to ChatGPT, Gemini, and the YouTube extension overlay persist across restarts.
   > [!IMPORTANT]
   > The system does **not** auto-login or inspect browser secrets. Operators must log in manually once via the VNC viewer.

---

## 2. Directory Layout & Artifacts

All job data, configurations, and rendered deliverables are stored locally in the `jobs/` directory. Files are structured in subdirectories to keep configurations separate from final outputs.

### Job Folder Structure
```text
jobs/<job_id>/
├── job.json                # State machine status for the pipeline
├── json/                   # Configurations, prompts, metadata, and JSON artifacts
│   ├── idea.json
│   ├── script.json
│   ├── scenes.json
│   ├── seo.json
│   ├── whisper_timestamps.json
│   ├── render_props.json
│   └── visual_review.json
└── outputs/                # Rendered deliverables and review reports
    ├── video.mp4           # Final rendered video file
    ├── thumbnail_1.jpg     # Generated A/B thumbnail variants
    ├── thumbnail_2.jpg
    ├── thumbnail.jpg       # Default cover thumbnail
    ├── report.md           # Summary markdown report
    └── operator_review.html # Self-contained HTML review page
```

### Backward Compatibility
The system includes fallback loaders on the backend (`resolve_long_job_artifact` in `timeline_helpers.py`, `resolve_short_json` in `paths.py`, and the `/artifact` endpoint in `_legacy.py`). If the `json/` or `outputs/` subdirectories do not exist for legacy jobs, the loaders fall back to looking at the job root directory.

---

## 3. Production Pipelines

The system supports two core production formats: **Long-Form Videos** (16:9 vertical horizontal) and **Shorts** (9:16 vertical).

### 3.1 Long-Form Sequential Pipeline
Long-form videos go through the following sequence:
1. **`idea_research`**: ChatGPT keyword analysis and volume validation.
2. **`script` / `script_promote`**: ChatGPT scripts the narration and details.
3. **`script_qa`**: Gemini reviews the script for medical safety, tone, and compliance.
4. **`scenes` / `scenes_promote`**: ChatGPT plans the on-screen visuals and texts.
5. **`scenes_qa`**: Gemini validates scene duration and consistency.
6. **`seo` / `seo_promote`**: ChatGPT generates metadata, tags, and pinned comment drafts.
7. **`seo_qa`**: Gemini checks tag counts, forbidden positioning, and target language.
8. **`thumbnail_image`**: ChatGPT plans and generates 3 A/B thumbnail variants.
9. **`whisper_timestamps`**: Local Whisper transcribes Kokoro audio for subtitle sync.
10. **`render`**: Remotion renders the output video.
11. **`review`**: Updates the `operator_review.html` report.

### 3.2 Shorts Studio Synthesis Pipeline (V5)
The Shorts Studio utilizes a **Human-Selected Synthesis Idea Flow** derived from completed long videos:
1. **Full Narration Extraction**: The system extracts the full long-form narration from `scenes.json`.
2. **ChatGPT Idea Generation**: ChatGPT proposes multiple distinct Short ideas (e.g., *Checklists*, *Mistake lists*, *Warning signs*) synthesizing content across non-contiguous long scenes.
3. **User Selection**: The Web UI presents interactive cards showing titles, hooks, payoffs, and uniqueness scores. The operator selects one or more ideas.
4. **Render Pipeline**: The worker builds and renders each selected Short into `shorts/<short_id>/` with descriptive identifiers (including candidate ID, timestamps, and slug).
5. **Autopilot**: Can be configured to automatically trigger Shorts generation as soon as a long-form video passes manual review (`Review PASS`).

---

## 4. Fresh Setup & Launch

### Prerequisites
- Docker Desktop or a running Docker daemon (with Docker Compose support).
- Node/Python are not required on the host system (all run containerized).

### 4.1 Setup
We provide **one-click installers** that verify prerequisites, bootstrap environment files, build containers, and launch all services in the background.

* **macOS / Linux**:
  ```bash
  bash install.sh
  ```
* **Windows (Native PowerShell)**:
  Open PowerShell as Administrator and run:
  ```powershell
  Set-ExecutionPolicy Bypass -Scope Process -Force; .\install.ps1
  ```

* **Manual Setup Option**:
  ```bash
  cp .env.example .env
  docker compose build
  ```

### 4.2 Running the Application
Start the dashboard and background services using the runners:
* **macOS / Linux**: `bash run.sh`
* **Windows**: `.\run.ps1`

### 4.3 URLs & Sign-In
Once launched, open the following pages:
- **Web Dashboard**: [http://localhost:8000](http://localhost:8000) (pipeline timeline, log tailing, downloads).
- **VNC Browser Appliance**: [http://localhost:7900/vnc.html](http://localhost:7900/vnc.html) (VNC console to manually log in to ChatGPT, Gemini, and the VidIQ YouTube scoring overlay extension).

---

## 5. Environment & Configurations

Copy `.env.example` to `.env` to configure stock photo keys and timeouts:
```ini
PEXELS_API_KEY=your-pexels-key
PIXABAY_API_KEY=your-pixabay-key

# Headless Driver Cadence Mode (balanced, fast, human)
BROWSER_HUMAN_MODE=balanced

# Timers
WHISPER_SYNTH_TIMEOUT_SEC=900
```
> [!TIP]
> The browser driver run in `balanced` mode by default. It emulates a human typing cadence while utilizing in-page `MutationObserver`s to detect stream endings, reducing DevTools network overhead by 50×.

### Local Voice Synthesis (Kokoro TTS)
Voice rendering runs locally inside Docker using Kokoro-82M. Voice, language code, and speeds are configured in `configs/vida-plena-45/channel.yaml`:
```yaml
tts:
  provider: "kokoro"
  voice_id: "ef_dora"     # Spanish female voice
  speed: 0.95             # ~145 WPM pace
```

---

## 6. Development & Command Reference

### Running Tests
To run the standard unit and integration test suite:
```bash
docker compose run --rm video-agent pytest -v
```

To run web-related dashboard and API tests:
```bash
docker compose run --rm video-agent pytest tests/test_web_jobs.py tests/test_shorts_api.py -v
```

### Command Line Interface (CLI)
You can still execute tasks via the CLI container.

* **Deterministic MVP run**:
  ```bash
  docker compose run --rm video-agent python -m video_agent.cli run \
    --channel configs/vida-plena-45/channel.yaml \
    --idea inputs/manual_idea.json
  ```
* **Batch runs**:
  ```bash
  docker compose run --rm video-agent python -m video_agent.cli batch \
    --channel configs/vida-plena-45/channel.yaml \
    --idea inputs/batch_idea_sleep_habits.json \
    --idea inputs/batch_idea_hydration.json \
    --audit-path jobs/latest_batch_audit.md
  ```
* **Manual Audits**:
  ```bash
  docker compose run --rm video-agent python -m video_agent.cli audit \
    --job jobs/job-id-1 \
    --job jobs/job-id-2
  ```
