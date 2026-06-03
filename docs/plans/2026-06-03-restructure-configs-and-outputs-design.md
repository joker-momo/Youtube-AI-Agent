# Design: Restructure Shorts Configs and Outputs, Clarify IDs, and Clean up Public Jobs

## Goal

Restructure the subdirectory layout of each individual Short folder under `jobs/<job_id>/shorts/<short_id>/` to separate configuration files (`json/` directory) and output files (`outputs/` directory). Additionally, clarify `short_id` names with descriptive metadata (name slug, idea/candidate ID, and timestamp) and clean up temporary public rendering folders under `remotion/public/jobs/` upon job or short deletion.

## Proposed Changes

### 1. Descriptive `short_id` Format

Instead of generic names like `short-01`, `short_id` (which is also the directory name under `shorts/`) will follow this format:
`short-{idx:02d}_{idea_or_candidate_id}_{timestamp}_{title_slug}`

Where:
* `idx:02d`: The sequential number of the short.
* `idea_or_candidate_id`: The ID of the synthesis idea or long-video candidate.
* `timestamp`: Creation time in `YYYYMMDD_HHMMSS` format.
* `title_slug`: Clean slug of the short title or hook, lowercase, limited to 20 characters.

**Files to modify:**
* **[video_agent/shorts/planner.py](file:///Users/joker/Documents/Youtube-AI-Agent/src/video_agent/shorts/planner.py)**: Formats `short_id` using candidate ID, current local timestamp, and candidate narration slug.
* **[video_agent/shorts/synthesis.py](file:///Users/joker/Documents/Youtube-AI-Agent/src/video_agent/shorts/synthesis.py)**: Formats `short_id` using selected idea ID, current local timestamp, and idea title/hook slug. Updates directory matching regex in `_next_short_number`.
* **[video_agent/web/routes/shorts_studio.py](file:///Users/joker/Documents/Youtube-AI-Agent/src/video_agent/web/routes/shorts_studio.py)**: Updates `_SHORT_DIR_RE` regex to `r"^short-\d+(?:_.*)?$"` so it matches both legacy and new descriptive short IDs.

### 2. Restructuring Shorts Directories

New layout for newly-run shorts:
* `jobs/<job_id>/shorts/<short_id>/json/`:
  * `short_idea.json`
  * `short_script.json`
  * `short_scenes.json`
  * `short_source_map.json`
  * `short_seo.json`
  * `short_script_qa.json`
  * `short_scenes_qa.json`
  * `short_render_props.json`
  * `thumbnail_prompt_meta.json`
* `jobs/<job_id>/shorts/<short_id>/outputs/`:
  * `short.mp4`
  * `short_cover.jpg`
  * `thumbnail.jpg`
* `jobs/<job_id>/shorts/<short_id>/` (root):
  * `short_status.json` (state marker)

**Files to modify:**
* **[video_agent/shorts/short_thumbnail_builder.py](file:///Users/joker/Documents/Youtube-AI-Agent/src/video_agent/shorts/short_thumbnail_builder.py)**:
  * Reads `short_seo.json` through `paths.resolve_short_json`.
  * Writes `thumbnail.jpg` to `paths.short_outputs_dir(...)` instead of root.
  * Writes `thumbnail_prompt_meta.json` to `paths.short_json_dir(...)` instead of root.
* **[video_agent/shorts/qa.py](file:///Users/joker/Documents/Youtube-AI-Agent/src/video_agent/shorts/qa.py)**:
  * Uses `paths.resolve_short_json` to load `short_script.json` in `_run_gemini_scenes_qa` to remain fully backward compatible.
* **[video_agent/shorts/renderer.py](file:///Users/joker/Documents/Youtube-AI-Agent/src/video_agent/shorts/renderer.py)**:
  * Suffix friendly name generator loads `short_idea.json` via `paths.resolve_short_json`.

### 3. Cleanup of Machine Folders on Deletion

**Files to modify:**
* **[video_agent/web/routes/_legacy.py](file:///Users/joker/Documents/Youtube-AI-Agent/src/video_agent/web/routes/_legacy.py)**:
  * In `delete_job`, clean up `remotion/public/jobs/<job_id>` using `shutil.rmtree` if it exists.
* **[video_agent/web/routes/shorts_studio.py](file:///Users/joker/Documents/Youtube-AI-Agent/src/video_agent/web/routes/shorts_studio.py)**:
  * In `delete_short`, clean up `remotion/public/jobs/<short_id>` using `shutil.rmtree` if it exists.

## Verification Plan

### Automated Tests
* Run `pytest tests/test_shorts_*.py` and verify all tests pass.
* Specifically update and verify:
  * `tests/test_shorts_build.py` (test assertions and stub outputs)
  * `tests/test_shorts_render.py` (assert Remotion aliases location)
  * `tests/test_shorts_api.py` (set up test files under `json/`)
