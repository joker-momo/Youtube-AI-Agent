"""Long-form ``graphic_images`` pipeline stage (Phase 4).

Runs after ``seo_qa``. For each graphic-layout scene (``checklist``/``warning``/
``quote``/``cta``) that wants a graphic, generates a ChatGPT image from the
scene's ``graphic.prompt`` and records ``scene.graphic.image_ref`` on scenes.json.
The compiled visual timeline then renders that image for the scene's span instead
of a Remotion card.

The image generator is injected (``image_fn(prompt=, project_name=, out_path=)``,
typically ``BrowserClient.generate_image``) so the stage is testable without the
live browser provider. A single image failure is non-fatal: the scene keeps no
``image_ref`` and the renderer falls back safely. Independent of
``video_agent.shorts``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.contracts import ARTIFACT_SCENES, EVENT_LOG
from video_agent.orchestrator.job_state import load_job
from video_agent.orchestrator.stages._shared import (
    StageInputMissingError,
    _complete_stage,
    _resolve_artifact,
    _start_stage,
)
from video_agent.storage.atomic import atomic_write_json
from video_agent.utils.json_io import read_json
from video_agent.utils.logging import EventLogger
from video_agent.visual.spans import GRAPHIC_LAYOUTS

__all__ = ["run_graphic_images_stage"]

_STAGE = "graphic_images"
_PROMPT_PREFIX = (
    "Premium editorial illustration for a Spanish-language wellness video aimed at "
    "adults 45+. Calm, trustworthy, warm palette, clean typography, highly legible. "
    "Render this on-screen graphic: "
)


def _wants_graphic(scene: dict[str, Any]) -> bool:
    if str(scene.get("layout") or "").strip().lower() not in GRAPHIC_LAYOUTS:
        return False
    graphic = scene.get("graphic")
    # Default: a graphic-layout scene wants an image unless explicitly opted out.
    if isinstance(graphic, dict) and graphic.get("needed") is False:
        return False
    return True


def _graphic_prompt(scene: dict[str, Any]) -> str:
    graphic = scene.get("graphic") if isinstance(scene.get("graphic"), dict) else {}
    return str(
        graphic.get("prompt")
        or scene.get("visual_prompt")
        or scene.get("on_screen_text")
        or ""
    ).strip()


async def run_graphic_images_stage(job_dir: Path, channel_path: Path | None, image_fn) -> Path:
    """Generate ChatGPT images for graphic scenes; record ``graphic.image_ref``.

    Returns the scenes.json path. Per-scene failures are logged and skipped so the
    pipeline never halts on a single image error.
    """
    state = load_job(job_dir)
    if state.current_stage != _STAGE:
        raise StageInputMissingError(
            f"Cannot run {_STAGE} stage from current_stage={state.current_stage!r}"
        )

    scenes_path = _resolve_artifact(job_dir, ARTIFACT_SCENES, "scenes.json")
    if not scenes_path.exists():
        raise StageInputMissingError(f"{_STAGE}: scenes.json not found (looked at {scenes_path})")

    _start_stage(job_dir, _STAGE)
    scene_doc = read_json(scenes_path) or {}
    logger = EventLogger(job_dir / EVENT_LOG)
    assets_dir = job_dir / "assets"

    generated = 0
    failed = 0
    for scene in scene_doc.get("scenes") or []:
        if not _wants_graphic(scene):
            continue
        scene_id = str(scene.get("id") or "")
        prompt = _graphic_prompt(scene)
        if not scene_id or not prompt:
            failed += 1
            logger.log("SCENE_GRAPHIC_SKIPPED", {"scene_id": scene_id, "reason": "no_prompt"})
            continue
        out_path = assets_dir / f"graphic-{scene_id}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await image_fn(
                prompt=_PROMPT_PREFIX + prompt,
                project_name=f"{state.job_id}-graphic-{scene_id}",
                out_path=str(out_path),
            )
        except Exception as exc:  # provider/transport error — non-fatal per scene
            failed += 1
            logger.log("SCENE_GRAPHIC_FAILED", {"scene_id": scene_id, "error": str(exc)})
            continue
        graphic = scene.get("graphic") if isinstance(scene.get("graphic"), dict) else {}
        graphic["needed"] = True
        graphic["image_ref"] = str(out_path.relative_to(job_dir))
        scene["graphic"] = graphic
        generated += 1
        logger.log("SCENE_GRAPHIC_GENERATED", {"scene_id": scene_id, "image_ref": graphic["image_ref"]})

    atomic_write_json(scenes_path, scene_doc)
    logger.log("GRAPHIC_IMAGES_DONE", {"generated": generated, "failed": failed})
    _complete_stage(job_dir, _STAGE, scenes_path)
    return scenes_path
