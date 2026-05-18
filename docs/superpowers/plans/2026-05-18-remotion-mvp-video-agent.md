# Remotion MVP Video Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local/mock MVP that turns `inputs/manual_idea.json` into `video.mp4`, `thumbnail.jpg`, `seo.json`, and `report.md` using a Python orchestrator and Remotion renderer.

**Architecture:** Python owns pipeline orchestration, contracts, schemas, mock providers, QA loops, and job artifacts. Remotion reads only `render_props.json` and produces the final video/thumbnail. All providers are deterministic/local but shaped so real LLM, TTS, stock, and image providers can replace them later.

**Tech Stack:** Python 3 standard library plus `pytest`, `jsonschema`, `PyYAML`, `Pillow`; Node.js/TypeScript/React/Remotion for rendering; optional `ffmpeg`/`ffprobe` for audio support and verification.

---

## File Map

- `pyproject.toml`: Python package metadata, pytest config, console script.
- `requirements.txt`: Python dependencies for the MVP.
- `README.md`: Local run instructions and output expectations.
- `configs/vida-plena-45/*`: demo channel, style DNA, brand voice, personas, QA rules.
- `inputs/manual_idea.json`: sample manual idea for the Vida Plena demo.
- `schemas/*.schema.json`: JSON schemas for inputs and generated artifacts.
- `src/video_agent/cli.py`: command line entry point.
- `src/video_agent/pipeline.py`: stage orchestration and job directory creation.
- `src/video_agent/contracts.py`: shared constants and small helpers for artifact names.
- `src/video_agent/providers/base.py`: provider protocols/interfaces.
- `src/video_agent/providers/mock.py`: deterministic script, scene, SEO, and asset provider.
- `src/video_agent/stages/*.py`: one stage per artifact-producing step.
- `src/video_agent/qa/*.py`: deterministic critic-shaped QA gates.
- `src/video_agent/utils/*.py`: path, JSON/YAML IO, schema validation, event logging.
- `tests/`: focused unit tests plus one end-to-end pipeline test that can skip Remotion rendering.
- `remotion/package.json`: Remotion project scripts and dependencies.
- `remotion/src/Root.tsx`: Remotion composition registration.
- `remotion/src/ChannelVideo.tsx`: video composition reading `render_props.json`.
- `remotion/src/Thumbnail.tsx`: thumbnail still composition.
- `remotion/src/render-props.ts`: typed render props loader.

---

### Task 1: Bootstrap Python Package, Config, Schemas, And Sample Input

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `README.md`
- Create: `src/video_agent/__init__.py`
- Create: `configs/vida-plena-45/channel.yaml`
- Create: `configs/vida-plena-45/style-dna.json`
- Create: `configs/vida-plena-45/brand-voice.md`
- Create: `configs/vida-plena-45/personas/maria.md`
- Create: `configs/vida-plena-45/personas/carlos.md`
- Create: `configs/vida-plena-45/personas/rosa.md`
- Create: `configs/vida-plena-45/qa-rules/script.yaml`
- Create: `configs/vida-plena-45/qa-rules/scene.yaml`
- Create: `configs/vida-plena-45/qa-rules/asset.yaml`
- Create: `inputs/manual_idea.json`
- Create: `schemas/channel-config.schema.json`
- Create: `schemas/manual-idea.schema.json`
- Create: `schemas/script.schema.json`
- Create: `schemas/scenes.schema.json`
- Create: `schemas/seo.schema.json`
- Create: `schemas/render-props.schema.json`
- Test: `tests/test_config_and_schemas.py`

- [ ] **Step 1: Write the failing schema/config tests**

Create `tests/test_config_and_schemas.py`:

```python
from pathlib import Path

import json
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_sample_channel_config_matches_schema():
    schema = load_json("schemas/channel-config.schema.json")
    data = yaml.safe_load((ROOT / "configs/vida-plena-45/channel.yaml").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(data)
    assert data["channel"]["id"] == "vida-plena-45"
    assert data["audience"]["language"] == "es-LA"
    assert data["render"]["composition"] == "ChannelVideoStandard"


def test_manual_idea_matches_schema():
    schema = load_json("schemas/manual-idea.schema.json")
    data = load_json("inputs/manual_idea.json")
    Draft202012Validator(schema).validate(data)
    assert 45 <= data["target_duration_sec"] <= 60
    assert len(data["key_points"]) >= 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_config_and_schemas.py -v
```

Expected: FAIL because `pytest`, package files, schemas, and config files do not exist yet.

- [ ] **Step 3: Add package metadata and dependencies**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "youtube-ai-agent"
version = "0.1.0"
description = "Local MVP video agent that renders YouTube-ready artifacts with Remotion."
requires-python = ">=3.11"
dependencies = [
  "jsonschema>=4.22",
  "PyYAML>=6.0.1",
  "Pillow>=10.4",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2",
]

[project.scripts]
video-agent = "video_agent.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

Create `requirements.txt`:

```text
jsonschema>=4.22
PyYAML>=6.0.1
Pillow>=10.4
pytest>=8.2
```

Create `src/video_agent/__init__.py`:

```python
__all__ = ["__version__"]

__version__ = "0.1.0"
```

- [ ] **Step 4: Add demo channel files**

Create `configs/vida-plena-45/channel.yaml`:

```yaml
schema_version: "3.0-mvp"
channel:
  id: "vida-plena-45"
  name: "Vida Plena 45+"
  youtube_channel_id: null
  description: "Salud y bienestar práctico para personas de más de 45 años."
audience:
  language: "es-LA"
  age_range: [45, 75]
  primary_markets: ["MX", "CO", "ES"]
  secondary_markets: ["AR", "CL", "PE"]
niche:
  category: "health_wellness"
  sub_niches:
    - "nutrition_45plus"
    - "exercise_low_impact"
    - "sleep_quality"
  avoid_topics:
    - "specific_medical_diagnoses"
    - "supplement_promotion"
    - "miracle_cures"
brand_voice_path: "configs/vida-plena-45/brand-voice.md"
style_dna:
  path: "configs/vida-plena-45/style-dna.json"
  current_version: "mvp-v1"
personas:
  - id: "maria"
    profile_path: "configs/vida-plena-45/personas/maria.md"
    weight: 1.0
  - id: "carlos"
    profile_path: "configs/vida-plena-45/personas/carlos.md"
    weight: 1.0
  - id: "rosa"
    profile_path: "configs/vida-plena-45/personas/rosa.md"
    weight: 1.0
qa_rules:
  script_path: "configs/vida-plena-45/qa-rules/script.yaml"
  scene_path: "configs/vida-plena-45/qa-rules/scene.yaml"
  asset_path: "configs/vida-plena-45/qa-rules/asset.yaml"
  thresholds:
    max_retry_per_qa: 3
    max_average_sentence_words: 15
    max_thumbnail_words: 6
domain_plugins:
  - name: "medical_safety_checker"
    enabled: true
models:
  default: "mock-local"
tts:
  provider: "mock-local"
  voice_id: "vida-plena-calm"
  pace_wpm: 145
visuals:
  strategy: "mock_local"
  scene_count_target: 5
music:
  source: "mock-local"
  level_db: -22
render:
  composition: "ChannelVideoStandard"
  resolution: "1920x1080"
  fps: 30
  codec: "h264"
  crf: 18
upload:
  ai_disclosure: true
  default_privacy: "manual_upload_only"
budget:
  max_cost_per_video_usd: 0
```

Create `configs/vida-plena-45/style-dna.json`:

```json
{
  "version": "mvp-v1",
  "palette": {
    "background": "#F6F1E8",
    "primary": "#2F6B57",
    "secondary": "#D98C5F",
    "accent": "#F2C94C",
    "text": "#26332F"
  },
  "typography": {
    "headline": "Inter",
    "body": "Inter"
  },
  "visual_mood": ["calm", "warm", "trustworthy", "practical"],
  "motion": {
    "default": "slow_push",
    "caption_style": "bottom_bar"
  },
  "thumbnail": {
    "max_words": 6,
    "style": "warm_high_contrast"
  }
}
```

Create `configs/vida-plena-45/brand-voice.md`:

```markdown
# Vida Plena 45+ Brand Voice

Use calm, respectful Spanish for adults over 45. Prefer practical routines, gentle encouragement, and clear disclaimers. Avoid fear, miracle promises, supplement promotion, diagnosis, or treatment instructions.
```

Create `configs/vida-plena-45/personas/maria.md`:

```markdown
# Maria

Maria is 58, wants practical health habits, and prefers calm explanations with no medical jargon.
```

Create `configs/vida-plena-45/personas/carlos.md`:

```markdown
# Carlos

Carlos is 63, skeptical of exaggerated health claims, and values realistic routines he can do at home.
```

Create `configs/vida-plena-45/personas/rosa.md`:

```markdown
# Rosa

Rosa is 71, wants simple steps and large readable text. She dislikes fast captions and alarmist language.
```

Create `configs/vida-plena-45/qa-rules/script.yaml`:

```yaml
max_hook_words: 28
max_average_sentence_words: 15
blocked_terms:
  - cura milagrosa
  - diagnosticar
  - dosis exacta
required_disclaimer: "Este contenido es educativo y no reemplaza el consejo de un profesional de salud."
```

Create `configs/vida-plena-45/qa-rules/scene.yaml`:

```yaml
min_scene_duration_sec: 7
max_scene_duration_sec: 14
required_fields:
  - id
  - duration_sec
  - narration
  - visual_prompt
  - on_screen_text
  - caption
  - motion
```

Create `configs/vida-plena-45/qa-rules/asset.yaml`:

```yaml
thumbnail_max_words: 6
required_outputs:
  - video.mp4
  - thumbnail.jpg
  - seo.json
  - report.md
```

- [ ] **Step 5: Add sample manual idea**

Create `inputs/manual_idea.json`:

```json
{
  "topic": "Hábitos nocturnos para dormir mejor después de los 45",
  "angle": "Rutina simple, segura y realista para mejorar el descanso sin promesas médicas",
  "target_duration_sec": 54,
  "key_points": [
    "preparar una hora tranquila antes de dormir",
    "evitar cenas muy pesadas y pantallas brillantes",
    "usar respiración suave para bajar el ritmo",
    "mantener horarios constantes",
    "consultar a un profesional si el insomnio persiste"
  ],
  "title_seed": "5 hábitos nocturnos para dormir mejor después de los 45"
}
```

- [ ] **Step 6: Add schemas**

Create `schemas/channel-config.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["schema_version", "channel", "audience", "niche", "style_dna", "qa_rules", "render", "upload"],
  "properties": {
    "schema_version": { "type": "string" },
    "channel": {
      "type": "object",
      "required": ["id", "name", "description"],
      "properties": {
        "id": { "type": "string" },
        "name": { "type": "string" },
        "youtube_channel_id": { "type": ["string", "null"] },
        "description": { "type": "string" }
      }
    },
    "audience": {
      "type": "object",
      "required": ["language", "age_range", "primary_markets"],
      "properties": {
        "language": { "type": "string" },
        "age_range": { "type": "array", "items": { "type": "integer" }, "minItems": 2, "maxItems": 2 },
        "primary_markets": { "type": "array", "items": { "type": "string" } },
        "secondary_markets": { "type": "array", "items": { "type": "string" } }
      }
    },
    "niche": { "type": "object" },
    "style_dna": {
      "type": "object",
      "required": ["path", "current_version"],
      "properties": {
        "path": { "type": "string" },
        "current_version": { "type": "string" }
      }
    },
    "qa_rules": { "type": "object" },
    "render": {
      "type": "object",
      "required": ["composition", "resolution", "fps"],
      "properties": {
        "composition": { "type": "string" },
        "resolution": { "type": "string" },
        "fps": { "type": "integer" },
        "codec": { "type": "string" },
        "crf": { "type": "integer" }
      }
    },
    "upload": {
      "type": "object",
      "required": ["ai_disclosure"],
      "properties": {
        "ai_disclosure": { "type": "boolean" },
        "default_privacy": { "type": "string" }
      }
    }
  }
}
```

Create `schemas/manual-idea.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["topic", "angle", "target_duration_sec", "key_points"],
  "properties": {
    "topic": { "type": "string", "minLength": 5 },
    "angle": { "type": "string", "minLength": 10 },
    "target_duration_sec": { "type": "integer", "minimum": 30, "maximum": 90 },
    "key_points": { "type": "array", "items": { "type": "string" }, "minItems": 3 },
    "title_seed": { "type": "string" }
  }
}
```

Create minimal artifact schemas now; later tasks will validate against them:

```bash
python3 - <<'PY'
import json
from pathlib import Path

schemas = {
    "script.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["channel_id", "job_id", "hook", "sections", "narration", "cta", "qa"],
        "properties": {
            "channel_id": {"type": "string"},
            "job_id": {"type": "string"},
            "hook": {"type": "string"},
            "sections": {"type": "array", "items": {"type": "object"}},
            "narration": {"type": "string"},
            "cta": {"type": "string"},
            "qa": {"type": "object"}
        }
    },
    "scenes.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["channel_id", "job_id", "scenes", "total_duration_sec", "qa"],
        "properties": {
            "channel_id": {"type": "string"},
            "job_id": {"type": "string"},
            "total_duration_sec": {"type": "integer"},
            "scenes": {"type": "array", "minItems": 1, "items": {"type": "object"}},
            "qa": {"type": "object"}
        }
    },
    "seo.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["title", "description", "tags", "language", "ai_disclosure", "thumbnail_path"],
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "language": {"type": "string"},
            "ai_disclosure": {"type": "boolean"},
            "thumbnail_path": {"type": "string"}
        }
    },
    "render-props.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["channel", "style", "render", "scenes", "audio", "seo"],
        "properties": {
            "channel": {"type": "object"},
            "style": {"type": "object"},
            "render": {"type": "object"},
            "scenes": {"type": "array", "items": {"type": "object"}},
            "audio": {"type": "object"},
            "seo": {"type": "object"}
        }
    }
}

root = Path("schemas")
root.mkdir(exist_ok=True)
for filename, schema in schemas.items():
    (root / filename).write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
PY
```

- [ ] **Step 7: Add README skeleton**

Create `README.md`:

```markdown
# YouTube AI Agent MVP

Local MVP for producing YouTube-ready video artifacts from a manual idea.

Target flow:

```text
manual_idea.json -> script -> scenes -> assets -> Remotion video -> thumbnail -> seo.json -> report.md
```

The MVP uses deterministic mock providers. It does not use Hermes, YouTube upload, OAuth, Telegram, scheduled publishing, trend research, or real LLM/TTS/image APIs.

## Demo Command

```bash
python3 -m video_agent.cli run --channel configs/vida-plena-45/channel.yaml --idea inputs/manual_idea.json
```

Expected outputs are written under `jobs/<job_id>/`.
```

- [ ] **Step 8: Run tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_config_and_schemas.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml requirements.txt README.md src/video_agent/__init__.py configs inputs schemas tests/test_config_and_schemas.py
git commit -m "feat: add MVP config and schemas"
```

---

### Task 2: Add IO, Validation, Logging, Contracts, And Job Directory Helpers

**Files:**
- Create: `src/video_agent/contracts.py`
- Create: `src/video_agent/utils/json_io.py`
- Create: `src/video_agent/utils/paths.py`
- Create: `src/video_agent/utils/validation.py`
- Create: `src/video_agent/utils/logging.py`
- Create: `src/video_agent/utils/__init__.py`
- Test: `tests/test_utils.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_utils.py`:

```python
import json
from pathlib import Path

from video_agent.utils.json_io import read_json, write_json
from video_agent.utils.logging import EventLogger
from video_agent.utils.paths import create_job_dir, slugify
from video_agent.utils.validation import validate_json


def test_slugify_creates_stable_slug():
    assert slugify("Vida Plena 45+ Hábitos") == "vida-plena-45-habitos"


def test_write_and_read_json(tmp_path):
    path = tmp_path / "nested" / "data.json"
    write_json(path, {"ok": True})
    assert read_json(path) == {"ok": True}


def test_validate_json_accepts_valid_payload(tmp_path):
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps({"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}), encoding="utf-8")
    validate_json({"name": "Vida"}, schema_path)


def test_event_logger_writes_jsonl(tmp_path):
    logger = EventLogger(tmp_path / "events.jsonl")
    logger.log("SCRIPTED", {"job_id": "job-test", "cost_usd": 0})
    line = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["event"] == "SCRIPTED"
    assert event["data"]["job_id"] == "job-test"


def test_create_job_dir_contains_slug_and_timestamp(tmp_path):
    job_dir = create_job_dir(tmp_path, "vida-plena-45", "Dormir mejor", timestamp="20260518-120000")
    assert job_dir.name == "20260518-120000-vida-plena-45-dormir-mejor"
    assert job_dir.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_utils.py -v
```

Expected: FAIL because utilities do not exist.

- [ ] **Step 3: Implement contracts and utility modules**

Create `src/video_agent/contracts.py`:

```python
from __future__ import annotations

from pathlib import Path

ARTIFACT_SCRIPT = "script.json"
ARTIFACT_SCENES = "scenes.json"
ARTIFACT_ASSETS = "assets_manifest.json"
ARTIFACT_RENDER_PROPS = "render_props.json"
ARTIFACT_SEO = "seo.json"
ARTIFACT_REPORT = "report.md"
ARTIFACT_VIDEO = "video.mp4"
ARTIFACT_THUMBNAIL = "thumbnail.jpg"
EVENT_LOG = "events.jsonl"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
```

Create `src/video_agent/utils/__init__.py`:

```python
"""Utility helpers for the local MVP pipeline."""
```

Create `src/video_agent/utils/json_io.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))
```

Create `src/video_agent/utils/validation.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from video_agent.utils.json_io import read_json


def validate_json(data: Any, schema_path: Path) -> None:
    schema = read_json(schema_path)
    Draft202012Validator(schema).validate(data)
```

Create `src/video_agent/utils/logging.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class EventLogger:
    path: Path

    def log(self, event: str, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "data": data,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
```

Create `src/video_agent/utils/paths.py`:

```python
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value.lower()).strip("-")
    return re.sub(r"-+", "-", slug) or "untitled"


def create_job_dir(base_dir: Path, channel_id: str, topic: str, timestamp: str | None = None) -> Path:
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    job_dir = base_dir / f"{stamp}-{channel_id}-{slugify(topic)[:48]}"
    job_dir.mkdir(parents=True, exist_ok=False)
    return job_dir
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_utils.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/video_agent/contracts.py src/video_agent/utils tests/test_utils.py
git commit -m "feat: add MVP IO and validation utilities"
```

---

### Task 3: Implement Mock Providers And Deterministic QA Loops

**Files:**
- Create: `src/video_agent/providers/__init__.py`
- Create: `src/video_agent/providers/base.py`
- Create: `src/video_agent/providers/mock.py`
- Create: `src/video_agent/qa/__init__.py`
- Create: `src/video_agent/qa/common.py`
- Create: `src/video_agent/qa/script_qa.py`
- Create: `src/video_agent/qa/scene_qa.py`
- Create: `src/video_agent/qa/thumbnail_title_qa.py`
- Test: `tests/test_mock_provider_and_qa.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_mock_provider_and_qa.py`:

```python
from video_agent.providers.mock import MockProvider
from video_agent.qa.scene_qa import check_scenes
from video_agent.qa.script_qa import check_script
from video_agent.qa.thumbnail_title_qa import check_thumbnail_title


CHANNEL = {
    "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+"},
    "audience": {"language": "es-LA"},
    "upload": {"ai_disclosure": True},
    "qa_rules": {"thresholds": {"max_average_sentence_words": 15, "max_thumbnail_words": 6}},
}

IDEA = {
    "topic": "Hábitos nocturnos para dormir mejor después de los 45",
    "angle": "Rutina simple y segura",
    "target_duration_sec": 54,
    "key_points": ["calma", "pantallas", "respiración", "horarios", "consulta profesional"],
    "title_seed": "5 hábitos nocturnos para dormir mejor después de los 45",
}


def test_mock_script_passes_script_qa():
    provider = MockProvider()
    script = provider.generate_script(CHANNEL, IDEA, "job-1")
    qa = check_script(script, CHANNEL)
    assert qa["verdict"] == "PASS"
    assert script["channel_id"] == "vida-plena-45"
    assert "profesional de salud" in script["narration"]


def test_mock_scenes_pass_scene_qa():
    provider = MockProvider()
    script = provider.generate_script(CHANNEL, IDEA, "job-1")
    scenes = provider.generate_scenes(CHANNEL, IDEA, script, "job-1")
    qa = check_scenes(scenes, CHANNEL)
    assert qa["verdict"] == "PASS"
    assert 45 <= scenes["total_duration_sec"] <= 60
    assert len(scenes["scenes"]) == 5


def test_mock_seo_passes_thumbnail_title_qa():
    provider = MockProvider()
    seo = provider.generate_seo(CHANNEL, IDEA, "jobs/demo/thumbnail.jpg")
    qa = check_thumbnail_title(seo, CHANNEL)
    assert qa["verdict"] == "PASS"
    assert seo["ai_disclosure"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_mock_provider_and_qa.py -v
```

Expected: FAIL because provider and QA modules do not exist.

- [ ] **Step 3: Implement provider interfaces**

Create `src/video_agent/providers/__init__.py`:

```python
from video_agent.providers.mock import MockProvider

__all__ = ["MockProvider"]
```

Create `src/video_agent/providers/base.py`:

```python
from __future__ import annotations

from typing import Any, Protocol


class ContentProvider(Protocol):
    def generate_script(self, channel_config: dict[str, Any], idea: dict[str, Any], job_id: str) -> dict[str, Any]:
        ...

    def generate_scenes(
        self,
        channel_config: dict[str, Any],
        idea: dict[str, Any],
        script: dict[str, Any],
        job_id: str,
    ) -> dict[str, Any]:
        ...

    def generate_seo(self, channel_config: dict[str, Any], idea: dict[str, Any], thumbnail_path: str) -> dict[str, Any]:
        ...
```

- [ ] **Step 4: Implement deterministic mock provider**

Create `src/video_agent/providers/mock.py`:

```python
from __future__ import annotations

from typing import Any


class MockProvider:
    def generate_script(self, channel_config: dict[str, Any], idea: dict[str, Any], job_id: str) -> dict[str, Any]:
        channel_id = channel_config["channel"]["id"]
        topic = idea["topic"]
        key_points = idea["key_points"]
        hook = "¿Te cuesta dormir bien después de los 45? Empieza con una noche más tranquila."
        sections = [
            {
                "title": "Prepara el descanso",
                "text": f"Una hora antes de dormir, baja el ritmo. {key_points[0].capitalize()} puede ayudar a tu cuerpo a reconocer que el día terminó.",
            },
            {
                "title": "Cuida los estímulos",
                "text": f"Evita pantallas brillantes y cenas muy pesadas. {key_points[1].capitalize()} hace que el sueño llegue con menos resistencia.",
            },
            {
                "title": "Respira con suavidad",
                "text": f"Prueba una respiración lenta por dos minutos. {key_points[2].capitalize()} no fuerza el sueño, solo invita al cuerpo a calmarse.",
            },
            {
                "title": "Mantén constancia",
                "text": f"Intenta acostarte a una hora parecida cada noche. {key_points[3].capitalize()} crea una señal simple y repetible.",
            },
            {
                "title": "Busca apoyo si persiste",
                "text": f"Si el insomnio continúa, habla con un profesional. {key_points[4].capitalize()} es parte de cuidarte con responsabilidad.",
            },
        ]
        narration_parts = [hook] + [section["text"] for section in sections]
        narration_parts.append("Este contenido es educativo y no reemplaza el consejo de un profesional de salud.")
        narration = " ".join(narration_parts)
        return {
            "channel_id": channel_id,
            "job_id": job_id,
            "hook": hook,
            "sections": sections,
            "narration": narration,
            "cta": "Guarda esta rutina y compártela con alguien que quiera descansar mejor.",
            "qa": {"verdict": "PENDING", "iterations": []},
        }

    def generate_scenes(
        self,
        channel_config: dict[str, Any],
        idea: dict[str, Any],
        script: dict[str, Any],
        job_id: str,
    ) -> dict[str, Any]:
        target = int(idea["target_duration_sec"])
        durations = [10, 11, 11, 11, max(9, target - 43)]
        scene_texts = [
            ("Una noche más tranquila", script["hook"], "slow_push"),
            ("Baja el ritmo", script["sections"][0]["text"], "pan_left"),
            ("Menos pantallas", script["sections"][1]["text"], "pan_right"),
            ("Respira suave", script["sections"][2]["text"], "slow_zoom"),
            ("Constancia y apoyo", script["sections"][4]["text"], "fade_up"),
        ]
        scenes = []
        for index, (text, narration, motion) in enumerate(scene_texts, start=1):
            scenes.append(
                {
                    "id": f"scene-{index:02d}",
                    "duration_sec": durations[index - 1],
                    "narration": narration,
                    "visual_type": "generated_placeholder",
                    "visual_prompt": f"Warm editorial wellness scene for adults 45+, {text.lower()}, calm home environment",
                    "on_screen_text": text,
                    "caption": narration[:130],
                    "motion": motion,
                    "asset_refs": {"background": f"assets/scene-{index:02d}.jpg"},
                }
            )
        return {
            "channel_id": channel_config["channel"]["id"],
            "job_id": job_id,
            "scenes": scenes,
            "total_duration_sec": sum(durations),
            "qa": {"verdict": "PENDING", "iterations": []},
        }

    def generate_seo(self, channel_config: dict[str, Any], idea: dict[str, Any], thumbnail_path: str) -> dict[str, Any]:
        title = idea.get("title_seed") or "5 hábitos nocturnos para dormir mejor"
        return {
            "title": title,
            "description": (
                "Una rutina educativa y simple para preparar mejor la noche después de los 45. "
                "Este contenido no reemplaza el consejo de un profesional de salud."
            ),
            "tags": ["sueño", "bienestar", "vida plena 45", "hábitos saludables"],
            "language": channel_config["audience"]["language"],
            "ai_disclosure": bool(channel_config["upload"]["ai_disclosure"]),
            "thumbnail_path": thumbnail_path,
        }
```

- [ ] **Step 5: Implement QA modules**

Create `src/video_agent/qa/__init__.py`:

```python
"""Deterministic QA gates for the MVP."""
```

Create `src/video_agent/qa/common.py`:

```python
from __future__ import annotations


def pass_result(scores: dict[str, int] | None = None) -> dict:
    return {"verdict": "PASS", "scores": scores or {}, "issues": [], "retry_action": None}


def revise_result(issue_type: str, message: str, retry_action: str) -> dict:
    return {
        "verdict": "REVISE",
        "scores": {},
        "issues": [{"type": issue_type, "severity": "medium", "message": message}],
        "retry_action": retry_action,
    }


def average_sentence_words(text: str) -> float:
    sentences = [part.strip() for part in text.replace("?", ".").replace("!", ".").split(".") if part.strip()]
    if not sentences:
        return 0.0
    return sum(len(sentence.split()) for sentence in sentences) / len(sentences)
```

Create `src/video_agent/qa/script_qa.py`:

```python
from __future__ import annotations

from typing import Any

from video_agent.qa.common import average_sentence_words, pass_result, revise_result

BLOCKED_TERMS = ("cura milagrosa", "diagnosticar", "dosis exacta")


def check_script(script: dict[str, Any], channel_config: dict[str, Any]) -> dict[str, Any]:
    hook_words = len(script.get("hook", "").split())
    if hook_words > 28:
        return revise_result("HOOK_TOO_LONG", "Hook must stay under 28 words.", "rewrite_hook_only")
    narration = script.get("narration", "").lower()
    for term in BLOCKED_TERMS:
        if term in narration:
            return revise_result("MEDICAL_SAFETY", f"Blocked medical phrase found: {term}", "rewrite_unsafe_sentence")
    max_average = channel_config.get("qa_rules", {}).get("thresholds", {}).get("max_average_sentence_words", 15)
    avg = average_sentence_words(script.get("narration", ""))
    if avg > max_average:
        return revise_result("SENTENCES_TOO_LONG", f"Average sentence length is {avg:.1f} words.", "shorten_sentences")
    if "profesional de salud" not in narration:
        return revise_result("MISSING_DISCLAIMER", "Health content needs an educational disclaimer.", "add_disclaimer")
    return pass_result({"hook_words": hook_words, "average_sentence_words": round(avg)})
```

Create `src/video_agent/qa/scene_qa.py`:

```python
from __future__ import annotations

from typing import Any

from video_agent.qa.common import pass_result, revise_result

REQUIRED_SCENE_FIELDS = ("id", "duration_sec", "narration", "visual_prompt", "on_screen_text", "caption", "motion")


def check_scenes(scene_doc: dict[str, Any], channel_config: dict[str, Any]) -> dict[str, Any]:
    scenes = scene_doc.get("scenes", [])
    if not scenes:
        return revise_result("NO_SCENES", "At least one scene is required.", "regenerate_scenes")
    for scene in scenes:
        missing = [field for field in REQUIRED_SCENE_FIELDS if not scene.get(field)]
        if missing:
            return revise_result("SCENE_FIELD_MISSING", f"{scene.get('id', 'scene')} is missing {missing}.", "repair_scene_fields")
        if not 7 <= int(scene["duration_sec"]) <= 14:
            return revise_result("SCENE_DURATION_RANGE", f"{scene['id']} duration must be 7-14 seconds.", "rebalance_scene_duration")
    total = sum(int(scene["duration_sec"]) for scene in scenes)
    if not 45 <= total <= 60:
        return revise_result("TOTAL_DURATION_RANGE", f"Total duration {total}s must be 45-60s.", "rebalance_timeline")
    return pass_result({"scene_count": len(scenes), "total_duration_sec": total})
```

Create `src/video_agent/qa/thumbnail_title_qa.py`:

```python
from __future__ import annotations

from typing import Any

from video_agent.qa.common import pass_result, revise_result


def check_thumbnail_title(seo: dict[str, Any], channel_config: dict[str, Any]) -> dict[str, Any]:
    title = seo.get("title", "").strip()
    if not title:
        return revise_result("MISSING_TITLE", "SEO title is required.", "generate_title")
    if len(title) > 90:
        return revise_result("TITLE_TOO_LONG", "SEO title must be 90 characters or fewer.", "shorten_title")
    thumbnail_words = title.replace(":", " ").split()[:8]
    max_words = channel_config.get("qa_rules", {}).get("thresholds", {}).get("max_thumbnail_words", 6)
    if len(thumbnail_words) > max_words + 2:
        return revise_result("THUMBNAIL_TEXT_DENSE", "Thumbnail text candidate is too dense.", "shorten_thumbnail_text")
    if seo.get("ai_disclosure") is not True:
        return revise_result("MISSING_AI_DISCLOSURE", "AI disclosure must be true.", "set_ai_disclosure")
    return pass_result({"title_chars": len(title)})
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_mock_provider_and_qa.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/video_agent/providers src/video_agent/qa tests/test_mock_provider_and_qa.py
git commit -m "feat: add mock providers and deterministic QA"
```

---

### Task 4: Implement Python Stages And Pipeline Without Remotion Rendering

**Files:**
- Create: `src/video_agent/stages/__init__.py`
- Create: `src/video_agent/stages/script.py`
- Create: `src/video_agent/stages/scene.py`
- Create: `src/video_agent/stages/assets.py`
- Create: `src/video_agent/stages/thumbnail.py`
- Create: `src/video_agent/pipeline.py`
- Test: `tests/test_pipeline_artifacts.py`

- [ ] **Step 1: Write failing pipeline artifact test**

Create `tests/test_pipeline_artifacts.py`:

```python
from pathlib import Path

from video_agent.pipeline import PipelineOptions, run_pipeline
from video_agent.utils.json_io import read_json

ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_writes_structured_artifacts_without_render(tmp_path):
    result = run_pipeline(
        PipelineOptions(
            channel_path=ROOT / "configs/vida-plena-45/channel.yaml",
            idea_path=ROOT / "inputs/manual_idea.json",
            jobs_dir=tmp_path,
            render=False,
        )
    )
    assert result.video_path is None
    assert (result.job_dir / "script.json").exists()
    assert (result.job_dir / "scenes.json").exists()
    assert (result.job_dir / "assets_manifest.json").exists()
    assert (result.job_dir / "render_props.json").exists()
    assert (result.job_dir / "seo.json").exists()
    assert (result.job_dir / "thumbnail.jpg").exists()
    assert (result.job_dir / "report.md").exists()
    render_props = read_json(result.job_dir / "render_props.json")
    assert render_props["channel"]["id"] == "vida-plena-45"
    assert len(render_props["scenes"]) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_pipeline_artifacts.py -v
```

Expected: FAIL because stages and pipeline do not exist.

- [ ] **Step 3: Implement stage package and script stage**

Create `src/video_agent/stages/__init__.py`:

```python
"""Pipeline stages for the MVP video agent."""
```

Create `src/video_agent/stages/script.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.contracts import ARTIFACT_SCRIPT
from video_agent.qa.script_qa import check_script
from video_agent.utils.json_io import write_json


def run_script_stage(provider: Any, channel_config: dict[str, Any], idea: dict[str, Any], job_id: str, job_dir: Path) -> dict[str, Any]:
    script = provider.generate_script(channel_config, idea, job_id)
    iterations = []
    for iteration in range(1, 4):
        qa = check_script(script, channel_config)
        iterations.append({"iteration": iteration, **qa})
        if qa["verdict"] == "PASS":
            script["qa"] = {"verdict": "PASS", "iterations": iterations}
            write_json(job_dir / ARTIFACT_SCRIPT, script)
            return script
        if qa["retry_action"] == "add_disclaimer":
            script["narration"] += " Este contenido es educativo y no reemplaza el consejo de un profesional de salud."
    script["qa"] = {"verdict": "FAIL", "iterations": iterations}
    write_json(job_dir / ARTIFACT_SCRIPT, script)
    raise RuntimeError("Script QA failed after 3 iterations.")
```

- [ ] **Step 4: Implement scene, asset, thumbnail, and report stages**

Create `src/video_agent/stages/scene.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.contracts import ARTIFACT_SCENES
from video_agent.qa.scene_qa import check_scenes
from video_agent.utils.json_io import write_json


def run_scene_stage(
    provider: Any,
    channel_config: dict[str, Any],
    idea: dict[str, Any],
    script: dict[str, Any],
    job_id: str,
    job_dir: Path,
) -> dict[str, Any]:
    scene_doc = provider.generate_scenes(channel_config, idea, script, job_id)
    iterations = []
    for iteration in range(1, 4):
        qa = check_scenes(scene_doc, channel_config)
        iterations.append({"iteration": iteration, **qa})
        if qa["verdict"] == "PASS":
            scene_doc["qa"] = {"verdict": "PASS", "iterations": iterations}
            write_json(job_dir / ARTIFACT_SCENES, scene_doc)
            return scene_doc
    scene_doc["qa"] = {"verdict": "FAIL", "iterations": iterations}
    write_json(job_dir / ARTIFACT_SCENES, scene_doc)
    raise RuntimeError("Scene QA failed after 3 iterations.")
```

Create `src/video_agent/stages/assets.py`:

```python
from __future__ import annotations

import math
import wave
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from video_agent.contracts import ARTIFACT_ASSETS
from video_agent.utils.json_io import write_json


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _write_silent_wav(path: Path, duration_sec: int, sample_rate: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = duration_sec * sample_rate
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        chunk = b"\x00\x00" * sample_rate
        for _ in range(math.ceil(frame_count / sample_rate)):
            handle.writeframes(chunk)


def prepare_assets(job_dir: Path, style_dna: dict[str, Any], scene_doc: dict[str, Any]) -> dict[str, Any]:
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    palette = style_dna["palette"]
    colors = [_hex_to_rgb(palette["background"]), _hex_to_rgb(palette["primary"]), _hex_to_rgb(palette["secondary"])]
    scene_assets = []
    for index, scene in enumerate(scene_doc["scenes"]):
        image_path = assets_dir / f"{scene['id']}.jpg"
        image = Image.new("RGB", (1920, 1080), colors[index % len(colors)])
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 760, 1920, 1080), fill=_hex_to_rgb(palette["text"]))
        draw.text((96, 820), scene["on_screen_text"], fill=_hex_to_rgb(palette["accent"]))
        image.save(image_path, quality=92)
        absolute_image_path = image_path.resolve()
        scene["asset_refs"]["background"] = str(absolute_image_path)
        scene_assets.append({"scene_id": scene["id"], "background": str(absolute_image_path)})
    narration_path = assets_dir / "narration.wav"
    _write_silent_wav(narration_path, int(scene_doc["total_duration_sec"]))
    manifest = {
        "audio": {"narration": str(narration_path.resolve()), "music": None},
        "scenes": scene_assets,
        "thumbnail_source": scene_assets[0]["background"],
    }
    write_json(job_dir / ARTIFACT_ASSETS, manifest)
    return manifest
```

Create `src/video_agent/stages/thumbnail.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from video_agent.contracts import ARTIFACT_SEO, ARTIFACT_THUMBNAIL
from video_agent.qa.thumbnail_title_qa import check_thumbnail_title
from video_agent.utils.json_io import write_json


def create_thumbnail_and_seo(
    provider: Any,
    channel_config: dict[str, Any],
    style_dna: dict[str, Any],
    idea: dict[str, Any],
    job_dir: Path,
) -> dict[str, Any]:
    thumbnail_path = job_dir / ARTIFACT_THUMBNAIL
    palette = style_dna["palette"]
    image = Image.new("RGB", (1280, 720), palette["primary"])
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 470, 1280, 720), fill=palette["background"])
    draw.text((70, 500), "DORMIR MEJOR", fill=palette["text"])
    draw.text((70, 580), "DESPUES DE LOS 45", fill=palette["secondary"])
    image.save(thumbnail_path, quality=92)

    seo = provider.generate_seo(channel_config, idea, str(thumbnail_path))
    qa = check_thumbnail_title(seo, channel_config)
    seo["qa"] = {"iterations": [{"iteration": 1, **qa}], "verdict": qa["verdict"]}
    if qa["verdict"] != "PASS":
        raise RuntimeError("Thumbnail/title QA failed.")
    write_json(job_dir / ARTIFACT_SEO, seo)
    return seo
```

- [ ] **Step 5: Implement pipeline**

Create `src/video_agent/pipeline.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from video_agent.contracts import (
    ARTIFACT_REPORT,
    ARTIFACT_RENDER_PROPS,
    ARTIFACT_VIDEO,
    EVENT_LOG,
    repo_root,
)
from video_agent.providers.mock import MockProvider
from video_agent.stages.assets import prepare_assets
from video_agent.stages.scene import run_scene_stage
from video_agent.stages.script import run_script_stage
from video_agent.stages.thumbnail import create_thumbnail_and_seo
from video_agent.utils.json_io import read_json, read_yaml, write_json
from video_agent.utils.logging import EventLogger
from video_agent.utils.paths import create_job_dir
from video_agent.utils.validation import validate_json


@dataclass
class PipelineOptions:
    channel_path: Path
    idea_path: Path
    jobs_dir: Path = Path("jobs")
    render: bool = True


@dataclass
class PipelineResult:
    job_id: str
    job_dir: Path
    video_path: Path | None
    thumbnail_path: Path
    seo_path: Path
    report_path: Path


def _load_style(channel_config: dict) -> dict:
    return read_json(repo_root() / channel_config["style_dna"]["path"])


def _write_report(job_dir: Path, job_id: str, channel_config: dict, idea: dict, render_enabled: bool) -> Path:
    report_path = job_dir / ARTIFACT_REPORT
    report_path.write_text(
        "\n".join(
            [
                f"# Job Report: {job_id}",
                "",
                f"- Channel: {channel_config['channel']['name']}",
                f"- Topic: {idea['topic']}",
                f"- Render enabled: {render_enabled}",
                "- Outputs:",
                "  - script.json",
                "  - scenes.json",
                "  - assets_manifest.json",
                "  - render_props.json",
                "  - seo.json",
                "  - thumbnail.jpg",
                "  - video.mp4" if render_enabled else "  - video.mp4 skipped",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return report_path


def run_pipeline(options: PipelineOptions) -> PipelineResult:
    root = repo_root()
    channel_config = read_yaml(options.channel_path)
    idea = read_json(options.idea_path)
    validate_json(channel_config, root / "schemas/channel-config.schema.json")
    validate_json(idea, root / "schemas/manual-idea.schema.json")

    job_dir = create_job_dir(options.jobs_dir, channel_config["channel"]["id"], idea["topic"])
    job_id = job_dir.name
    logger = EventLogger(job_dir / EVENT_LOG)
    provider = MockProvider()
    style = _load_style(channel_config)

    logger.log("JOB_STARTED", {"job_id": job_id, "channel_id": channel_config["channel"]["id"], "cost_usd": 0})
    script = run_script_stage(provider, channel_config, idea, job_id, job_dir)
    validate_json(script, root / "schemas/script.schema.json")
    logger.log("SCRIPTED", {"job_id": job_id, "cost_usd": 0})

    scene_doc = run_scene_stage(provider, channel_config, idea, script, job_id, job_dir)
    validate_json(scene_doc, root / "schemas/scenes.schema.json")
    logger.log("SCENED", {"job_id": job_id, "cost_usd": 0})

    assets = prepare_assets(job_dir, style, scene_doc)
    seo = create_thumbnail_and_seo(provider, channel_config, style, idea, job_dir)
    validate_json(seo, root / "schemas/seo.schema.json")
    logger.log("ASSETS_READY", {"job_id": job_id, "cost_usd": 0})

    render_props = {
        "channel": channel_config["channel"],
        "style": style,
        "render": channel_config["render"] | {"duration_sec": scene_doc["total_duration_sec"]},
        "scenes": scene_doc["scenes"],
        "audio": assets["audio"],
        "seo": seo,
    }
    write_json(job_dir / ARTIFACT_RENDER_PROPS, render_props)
    validate_json(render_props, root / "schemas/render-props.schema.json")

    video_path = None
    if options.render:
        video_path = job_dir / ARTIFACT_VIDEO
        logger.log("RENDER_SKIPPED_UNIMPLEMENTED", {"job_id": job_id, "cost_usd": 0})

    report_path = _write_report(job_dir, job_id, channel_config, idea, options.render)
    logger.log("JOB_COMPLETED", {"job_id": job_id, "cost_usd": 0})
    return PipelineResult(
        job_id=job_id,
        job_dir=job_dir,
        video_path=video_path if video_path and video_path.exists() else None,
        thumbnail_path=job_dir / "thumbnail.jpg",
        seo_path=job_dir / "seo.json",
        report_path=report_path,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_pipeline_artifacts.py -v
```

Expected: PASS.

- [ ] **Step 7: Run all Python tests**

Run:

```bash
python3 -m pytest -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/video_agent/stages src/video_agent/pipeline.py tests/test_pipeline_artifacts.py
git commit -m "feat: generate structured MVP job artifacts"
```

---

### Task 5: Add CLI Entry Point

**Files:**
- Create: `src/video_agent/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI test**

Create `tests/test_cli.py`:

```python
from pathlib import Path

from video_agent.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_cli_run_without_render(tmp_path, capsys):
    exit_code = main(
        [
            "run",
            "--channel",
            str(ROOT / "configs/vida-plena-45/channel.yaml"),
            "--idea",
            str(ROOT / "inputs/manual_idea.json"),
            "--jobs-dir",
            str(tmp_path),
            "--no-render",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Job completed:" in captured.out
    assert "video.mp4: skipped" in captured.out
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_cli.py -v
```

Expected: FAIL because `video_agent.cli` does not exist.

- [ ] **Step 3: Implement CLI**

Create `src/video_agent/cli.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from video_agent.pipeline import PipelineOptions, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="video-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run the local MVP pipeline.")
    run_parser.add_argument("--channel", required=True, type=Path)
    run_parser.add_argument("--idea", required=True, type=Path)
    run_parser.add_argument("--jobs-dir", default=Path("jobs"), type=Path)
    run_parser.add_argument("--no-render", action="store_true", help="Generate artifacts but skip Remotion render.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        result = run_pipeline(
            PipelineOptions(
                channel_path=args.channel,
                idea_path=args.idea,
                jobs_dir=args.jobs_dir,
                render=not args.no_render,
            )
        )
        print(f"Job completed: {result.job_dir}")
        print(f"thumbnail.jpg: {result.thumbnail_path}")
        print(f"seo.json: {result.seo_path}")
        print(f"report.md: {result.report_path}")
        print(f"video.mp4: {result.video_path if result.video_path else 'skipped'}")
        return 0
    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run CLI test to verify it passes**

Run:

```bash
python3 -m pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 5: Run CLI manually without render**

Run:

```bash
python3 -m video_agent.cli run --channel configs/vida-plena-45/channel.yaml --idea inputs/manual_idea.json --no-render
```

Expected: output includes `Job completed: jobs/...` and `video.mp4: skipped`.

- [ ] **Step 6: Commit**

```bash
git add src/video_agent/cli.py tests/test_cli.py
git commit -m "feat: add MVP pipeline CLI"
```

---

### Task 6: Add Remotion Project And Static Render Prop Loading

**Files:**
- Create: `remotion/package.json`
- Create: `remotion/tsconfig.json`
- Create: `remotion/src/index.ts`
- Create: `remotion/src/Root.tsx`
- Create: `remotion/src/render-props.ts`
- Create: `remotion/src/ChannelVideo.tsx`
- Create: `remotion/src/Thumbnail.tsx`
- Create: `remotion/src/styles.ts`
- Modify: `README.md`

- [ ] **Step 1: Create Remotion package**

Create `remotion/package.json`:

```json
{
  "name": "youtube-ai-agent-remotion",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "render": "remotion render src/index.ts ChannelVideoStandard",
    "still": "remotion still src/index.ts ThumbnailStandard"
  },
  "dependencies": {
    "@remotion/cli": "^4.0.240",
    "@remotion/media-utils": "^4.0.240",
    "typescript": "^5.5.4",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "remotion": "^4.0.240"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0"
  }
}
```

Create `remotion/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "jsx": "react-jsx",
    "moduleResolution": "Bundler",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": ["src"]
}
```

- [ ] **Step 2: Add render prop types and loader**

Create `remotion/src/render-props.ts`:

```typescript
import {staticFile} from 'remotion';

export type Scene = {
  id: string;
  duration_sec: number;
  narration: string;
  visual_type: string;
  visual_prompt: string;
  on_screen_text: string;
  caption: string;
  motion: string;
  asset_refs: {background: string};
};

export type RenderProps = {
  channel: {id: string; name: string; description: string};
  style: {
    palette: {
      background: string;
      primary: string;
      secondary: string;
      accent: string;
      text: string;
    };
  };
  render: {fps: number; resolution: string; duration_sec: number};
  scenes: Scene[];
  audio: {narration: string | null; music: string | null};
  seo: {title: string; description: string; thumbnail_path: string};
};

export const mediaSrc = (path: string): string => {
  if (path.startsWith('http://') || path.startsWith('https://') || path.startsWith('file://')) {
    return path;
  }
  if (path.startsWith('/')) {
    return `file://${path}`;
  }
  return path;
};

export const defaultRenderProps: RenderProps = {
  channel: {id: 'vida-plena-45', name: 'Vida Plena 45+', description: 'Demo'},
  style: {
    palette: {
      background: '#F6F1E8',
      primary: '#2F6B57',
      secondary: '#D98C5F',
      accent: '#F2C94C',
      text: '#26332F',
    },
  },
  render: {fps: 30, resolution: '1920x1080', duration_sec: 54},
  scenes: [
    {
      id: 'scene-01',
      duration_sec: 10,
      narration: 'Demo scene',
      visual_type: 'generated_placeholder',
      visual_prompt: 'Warm wellness scene',
      on_screen_text: 'DORMIR MEJOR',
      caption: 'Demo scene',
      motion: 'slow_push',
      asset_refs: {background: staticFile('fallback.jpg')},
    },
  ],
  audio: {narration: null, music: null},
  seo: {title: '5 hábitos nocturnos', description: 'Demo', thumbnail_path: ''},
};
```

The Python renderer bridge will pass the real `render_props.json` through Remotion input props, so the default props only make local Remotion development less brittle.

- [ ] **Step 3: Add Remotion entry and root**

Create `remotion/src/index.ts`:

```typescript
import {registerRoot} from 'remotion';
import {Root} from './Root';

registerRoot(Root);
```

Create `remotion/src/Root.tsx`:

```tsx
import React from 'react';
import {Composition} from 'remotion';
import {ChannelVideo} from './ChannelVideo';
import {Thumbnail} from './Thumbnail';
import {defaultRenderProps} from './render-props';

export const Root: React.FC = () => {
  const fps = defaultRenderProps.render.fps;
  const durationInFrames = defaultRenderProps.render.duration_sec * fps;
  return (
    <>
      <Composition
        id="ChannelVideoStandard"
        component={ChannelVideo}
        durationInFrames={durationInFrames}
        fps={fps}
        width={1920}
        height={1080}
        defaultProps={defaultRenderProps}
      />
      <Composition
        id="ThumbnailStandard"
        component={Thumbnail}
        durationInFrames={1}
        fps={fps}
        width={1280}
        height={720}
        defaultProps={defaultRenderProps}
      />
    </>
  );
};
```

- [ ] **Step 4: Add composition components**

Create `remotion/src/styles.ts`:

```typescript
import {CSSProperties} from 'react';

export const fullFrame: CSSProperties = {
  flex: 1,
  width: '100%',
  height: '100%',
  position: 'relative',
  overflow: 'hidden',
  fontFamily: 'Inter, Arial, sans-serif',
};
```

Create `remotion/src/ChannelVideo.tsx`:

```tsx
import React from 'react';
import {AbsoluteFill, Audio, Img, interpolate, Sequence, useCurrentFrame, useVideoConfig} from 'remotion';
import {mediaSrc, RenderProps, Scene} from './render-props';
import {fullFrame} from './styles';

const SceneView: React.FC<{scene: Scene; startFrame: number; palette: RenderProps['style']['palette']}> = ({scene, startFrame, palette}) => {
  const frame = useCurrentFrame() - startFrame;
  const scale = interpolate(frame, [0, 90], [1, 1.04], {extrapolateRight: 'clamp'});
  const translate = interpolate(frame, [0, 90], [0, scene.motion === 'pan_right' ? -28 : 28], {extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{...fullFrame, backgroundColor: palette.background}}>
      <Img
        src={mediaSrc(scene.asset_refs.background)}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          transform: `scale(${scale}) translateX(${translate}px)`,
          opacity: 0.86,
        }}
      />
      <div style={{position: 'absolute', inset: 0, background: 'linear-gradient(90deg, rgba(38,51,47,0.74), rgba(38,51,47,0.12))'}} />
      <div style={{position: 'absolute', left: 96, top: 150, width: 820, color: palette.background}}>
        <div style={{fontSize: 34, marginBottom: 26, color: palette.accent, fontWeight: 700}}>{'Vida Plena 45+'}</div>
        <div style={{fontSize: 78, lineHeight: 1.05, fontWeight: 800}}>{scene.on_screen_text}</div>
      </div>
      <div style={{position: 'absolute', left: 96, right: 96, bottom: 72, padding: '24px 32px', backgroundColor: 'rgba(246,241,232,0.92)', color: palette.text, fontSize: 34, lineHeight: 1.25}}>
        {scene.caption}
      </div>
    </AbsoluteFill>
  );
};

export const ChannelVideo: React.FC<RenderProps> = (props) => {
  const {fps} = useVideoConfig();
  let start = 0;
  return (
    <AbsoluteFill style={{backgroundColor: props.style.palette.background}}>
      {props.audio.narration ? <Audio src={mediaSrc(props.audio.narration)} /> : null}
      {props.scenes.map((scene) => {
        const duration = Math.round(scene.duration_sec * fps);
        const startFrame = start;
        start += duration;
        return (
          <Sequence key={scene.id} from={startFrame} durationInFrames={duration}>
            <SceneView scene={scene} startFrame={startFrame} palette={props.style.palette} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
```

Create `remotion/src/Thumbnail.tsx`:

```tsx
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {RenderProps} from './render-props';

export const Thumbnail: React.FC<RenderProps> = (props) => {
  const palette = props.style.palette;
  return (
    <AbsoluteFill style={{backgroundColor: palette.primary, fontFamily: 'Inter, Arial, sans-serif'}}>
      <div style={{position: 'absolute', inset: 0, background: `linear-gradient(135deg, ${palette.primary}, ${palette.secondary})`}} />
      <div style={{position: 'absolute', left: 70, right: 70, top: 72, color: palette.background}}>
        <div style={{fontSize: 42, fontWeight: 700, color: palette.accent}}>Vida Plena 45+</div>
        <div style={{fontSize: 92, lineHeight: 1.02, fontWeight: 900, marginTop: 58}}>DORMIR MEJOR</div>
        <div style={{fontSize: 70, lineHeight: 1.05, fontWeight: 800, marginTop: 18}}>DESPUES DE LOS 45</div>
      </div>
      <div style={{position: 'absolute', left: 70, bottom: 56, padding: '18px 28px', backgroundColor: palette.background, color: palette.text, fontSize: 34, fontWeight: 700}}>
        5 habitos simples
      </div>
    </AbsoluteFill>
  );
};
```

- [ ] **Step 5: Install Remotion dependencies**

Run:

```bash
npm --prefix remotion install
```

Expected: `remotion/node_modules` and `remotion/package-lock.json` are created.

- [ ] **Step 6: Verify Remotion can list compositions**

Run:

```bash
npx --prefix remotion remotion compositions remotion/src/index.ts
```

Expected: output includes `ChannelVideoStandard` and `ThumbnailStandard`.

- [ ] **Step 7: Commit**

```bash
git add remotion/package.json remotion/package-lock.json remotion/tsconfig.json remotion/src README.md
git commit -m "feat: add Remotion video compositions"
```

---

### Task 7: Add Remotion Renderer Bridge And End-To-End Render

**Files:**
- Modify: `src/video_agent/stages/render.py`
- Modify: `src/video_agent/pipeline.py`
- Modify: `src/video_agent/cli.py`
- Test: `tests/test_render_command.py`

- [ ] **Step 1: Write failing renderer command test**

Create `tests/test_render_command.py`:

```python
from pathlib import Path

from video_agent.stages.render import build_remotion_commands


def test_build_remotion_commands_include_props_and_outputs(tmp_path):
    render_props = tmp_path / "render_props.json"
    render_props.write_text("{}", encoding="utf-8")
    video_path = tmp_path / "video.mp4"
    thumbnail_path = tmp_path / "thumbnail.jpg"
    commands = build_remotion_commands(render_props, video_path, thumbnail_path)
    video_command = " ".join(commands.video)
    still_command = " ".join(commands.thumbnail)
    assert "ChannelVideoStandard" in video_command
    assert "ThumbnailStandard" in still_command
    assert str(video_path) in video_command
    assert str(thumbnail_path) in still_command
    assert str(render_props) in video_command
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_render_command.py -v
```

Expected: FAIL because render stage does not exist.

- [ ] **Step 3: Implement renderer bridge**

Create `src/video_agent/stages/render.py`:

```python
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from video_agent.contracts import repo_root
from video_agent.utils.json_io import read_json


@dataclass
class RemotionCommands:
    video: list[str]
    thumbnail: list[str]


def _input_props_arg(render_props_path: Path) -> str:
    props = read_json(render_props_path)
    return json.dumps(props, ensure_ascii=False)


def build_remotion_commands(render_props_path: Path, video_path: Path, thumbnail_path: Path) -> RemotionCommands:
    remotion_root = repo_root() / "remotion"
    entry = remotion_root / "src/index.ts"
    input_props = _input_props_arg(render_props_path)
    base = ["npx", "--prefix", str(remotion_root), "remotion"]
    return RemotionCommands(
        video=[
            *base,
            "render",
            str(entry),
            "ChannelVideoStandard",
            str(video_path),
            "--props",
            input_props,
            "--codec",
            "h264",
        ],
        thumbnail=[
            *base,
            "still",
            str(entry),
            "ThumbnailStandard",
            str(thumbnail_path),
            "--props",
            input_props,
        ],
    )


def render_with_remotion(render_props_path: Path, video_path: Path, thumbnail_path: Path) -> None:
    commands = build_remotion_commands(render_props_path, video_path, thumbnail_path)
    subprocess.run(commands.video, cwd=repo_root(), check=True)
    subprocess.run(commands.thumbnail, cwd=repo_root(), check=True)
```

- [ ] **Step 4: Wire renderer into pipeline**

Modify `src/video_agent/pipeline.py`:

```python
# Add import near other stage imports
from video_agent.stages.render import render_with_remotion
```

Replace the current render block:

```python
    video_path = None
    if options.render:
        video_path = job_dir / ARTIFACT_VIDEO
        logger.log("RENDER_SKIPPED_UNIMPLEMENTED", {"job_id": job_id, "cost_usd": 0})
```

with:

```python
    video_path = None
    if options.render:
        video_path = job_dir / ARTIFACT_VIDEO
        render_with_remotion(job_dir / ARTIFACT_RENDER_PROPS, video_path, job_dir / "thumbnail.jpg")
        logger.log("RENDERED", {"job_id": job_id, "video_path": str(video_path), "cost_usd": 0})
```

- [ ] **Step 5: Run renderer command tests**

Run:

```bash
python3 -m pytest tests/test_render_command.py -v
```

Expected: PASS.

- [ ] **Step 6: Run full pipeline with render**

Run:

```bash
python3 -m video_agent.cli run --channel configs/vida-plena-45/channel.yaml --idea inputs/manual_idea.json
```

Expected:

- CLI prints `Job completed: jobs/...`
- `jobs/<job_id>/video.mp4` exists and is non-empty.
- `jobs/<job_id>/thumbnail.jpg` exists and is non-empty.
- `jobs/<job_id>/seo.json` exists.
- `jobs/<job_id>/report.md` exists.

- [ ] **Step 7: Verify media with ffprobe when available**

Run:

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$(find jobs -name video.mp4 | tail -1)"
```

Expected if `ffprobe` is installed: `1920,1080`.

If `ffprobe` is not installed, record that verification was skipped and continue.

- [ ] **Step 8: Commit**

```bash
git add src/video_agent/stages/render.py src/video_agent/pipeline.py tests/test_render_command.py
git commit -m "feat: render MVP videos with Remotion"
```

---

### Task 8: Final Documentation, E2E Test Script, And Verification Pass

**Files:**
- Modify: `README.md`
- Create: `scripts/run_mvp.sh`
- Create: `tests/test_required_outputs.py`

- [ ] **Step 1: Write required output test**

Create `tests/test_required_outputs.py`:

```python
from pathlib import Path

from video_agent.pipeline import PipelineOptions, run_pipeline

ROOT = Path(__file__).resolve().parents[1]


def test_required_outputs_exist_without_render(tmp_path):
    result = run_pipeline(
        PipelineOptions(
            channel_path=ROOT / "configs/vida-plena-45/channel.yaml",
            idea_path=ROOT / "inputs/manual_idea.json",
            jobs_dir=tmp_path,
            render=False,
        )
    )
    required = ["thumbnail.jpg", "seo.json", "report.md", "render_props.json", "script.json", "scenes.json"]
    for filename in required:
        path = result.job_dir / filename
        assert path.exists(), filename
        assert path.stat().st_size > 0, filename
```

- [ ] **Step 2: Add MVP run script**

Create `scripts/run_mvp.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

python3 -m video_agent.cli run \
  --channel configs/vida-plena-45/channel.yaml \
  --idea inputs/manual_idea.json
```

Run:

```bash
chmod +x scripts/run_mvp.sh
```

- [ ] **Step 3: Update README with setup and output paths**

Replace `README.md` with:

```markdown
# YouTube AI Agent MVP

Local MVP for producing YouTube-ready video artifacts from a manual idea.

## What It Does

```text
manual_idea.json -> script -> scenes -> assets -> Remotion video -> thumbnail -> seo.json -> report.md
```

The MVP uses deterministic mock providers. It does not use Hermes, YouTube upload, OAuth, Telegram, scheduled publishing, trend research, or real LLM/TTS/image APIs.

## Setup

```bash
python3 -m pip install -r requirements.txt
npm --prefix remotion install
```

## Run

```bash
python3 -m video_agent.cli run --channel configs/vida-plena-45/channel.yaml --idea inputs/manual_idea.json
```

Or:

```bash
scripts/run_mvp.sh
```

Outputs are written under `jobs/<job_id>/`:

- `video.mp4`
- `thumbnail.jpg`
- `seo.json`
- `report.md`
- `script.json`
- `scenes.json`
- `assets_manifest.json`
- `render_props.json`
- `events.jsonl`

## Run Without Rendering

```bash
python3 -m video_agent.cli run --channel configs/vida-plena-45/channel.yaml --idea inputs/manual_idea.json --no-render
```

## Test

```bash
python3 -m pytest -v
```
```

- [ ] **Step 4: Run all tests**

Run:

```bash
python3 -m pytest -v
```

Expected: PASS.

- [ ] **Step 5: Run full MVP script**

Run:

```bash
scripts/run_mvp.sh
```

Expected: a new `jobs/<job_id>/` directory with non-empty `video.mp4`, `thumbnail.jpg`, `seo.json`, and `report.md`.

- [ ] **Step 6: Inspect latest report**

Run:

```bash
find jobs -name report.md | sort | tail -1 | xargs sed -n '1,120p'
```

Expected: report shows channel, topic, render enabled, and outputs.

- [ ] **Step 7: Commit**

```bash
git add README.md scripts/run_mvp.sh tests/test_required_outputs.py
git commit -m "docs: add MVP run and verification workflow"
```

---

## Final Verification Checklist

- [ ] `python3 -m pytest -v` passes.
- [ ] `npm --prefix remotion install` has completed.
- [ ] `npx --prefix remotion remotion compositions remotion/src/index.ts` lists `ChannelVideoStandard` and `ThumbnailStandard`.
- [ ] `python3 -m video_agent.cli run --channel configs/vida-plena-45/channel.yaml --idea inputs/manual_idea.json` completes.
- [ ] Latest job has non-empty `video.mp4`.
- [ ] Latest job has non-empty `thumbnail.jpg`.
- [ ] Latest job has valid `seo.json` with `title`.
- [ ] Latest job has readable `report.md`.
- [ ] Upload automation, OAuth, Telegram, and trend research remain out of scope.

## Self-Review Notes

- Spec coverage: tasks cover config, Style DNA, manual idea input, script generation, QA/reflection loops, scene planning, asset prep, thumbnail/title QA, Remotion render, and structured job output.
- Placeholder scan: no unresolved placeholder markers remain. Each implementation task includes concrete file paths and code.
- Type consistency: artifact names, provider method names, QA result shape, and `render_props.json` fields are consistent across Python tests, pipeline, and Remotion components.
