from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from video_agent.contracts import (
    ARTIFACT_SCENES,
    ARTIFACT_SEO,
    ARTIFACT_THUMBNAIL,
    EVENT_LOG,
)
from video_agent.audience_age import resolve_target_min_age
from video_agent.orchestrator.image_prompt_log import (
    safe_log_image_prompt as _log_image_prompt,
)
from video_agent.orchestrator.job_state import load_job
from video_agent.orchestrator.stages._shared import (
    StageInputMissingError,
    _complete_stage,
    _resolve_artifact,
    _resolve_idea_path,
    dag_mode,
)
from video_agent.storage.public_jobs import prepare_public_job_dir
from video_agent.style_dna import load_style_dna
from video_agent.utils.json_io import read_yaml
from video_agent.utils.json_io import write_json as _write_json
from video_agent.utils.logging import EventLogger

__all__ = [
    "RESEARCH_FILE",
    "_idea_keywords",
    "auto_idea_research_stage",
    "_ASSET_GEN_PROMPT_PREFIX",
    "_scene_project_name",
    "_VARIANT_STRATEGY",
    "_topic_category_guidance",
    "_build_thumbnail_prompt",
    "_legacy_build_thumbnail_prompt",
    "generate_scene_asset",
    "auto_assets_chatgpt_stage",
    "auto_thumbnail_image_stage",
]


RESEARCH_FILE = "research.json"


def _idea_keywords(idea: dict) -> list[str]:
    """Build 3-5 keyword variants from idea.topic + title_seed."""
    base = idea.get("topic", "").strip()
    seed = idea.get("title_seed", "").strip()
    keywords = []
    if base:
        keywords.append(base)
    if seed and seed.lower() != base.lower():
        keywords.append(seed)
    # short variant (first 6 words)
    words = base.split()
    if len(words) > 4:
        short = " ".join(words[:5])
        if short not in keywords:
            keywords.append(short)
    return keywords[:5]


async def auto_idea_research_stage(
    job_dir: Path,
    channel_path: Path,
) -> Path:
    """Record topic keyword variants and advance the research stage."""
    stage_name = "idea_research"
    state = load_job(job_dir)
    if not dag_mode() and state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} from current_stage={state.current_stage!r}"
        )
    idea_path = _resolve_idea_path(job_dir)
    if not idea_path.exists():
        raise StageInputMissingError(f"Missing {idea_path}")

    idea = json.loads(idea_path.read_text(encoding="utf-8"))
    keywords = _idea_keywords(idea)
    if not keywords:
        raise StageInputMissingError("idea.json has no topic or title_seed for research")

    research = {
        "keywords": keywords,
        "verdict": "pass",
    }
    output_path = job_dir / RESEARCH_FILE
    _write_json(output_path, research)

    EventLogger(job_dir / EVENT_LOG).log(
        "IDEA_RESEARCH_COMPLETE",
        {"job_id": state.job_id, "keywords": keywords, "verdict": "pass"},
    )

    _complete_stage(job_dir, stage_name, output_path)
    return output_path


# ---------------------------------------------------------------------------
# Per-scene image generation via ChatGPT projects.
# ---------------------------------------------------------------------------


_ASSET_GEN_PROMPT_PREFIX = (
    "Photorealistic, 16:9 cinematic, soft natural light, no text overlay, "
    "no watermark, no logos. Audience: adultos 45+. Scene visual: "
)


def _asset_gen_prefix(min_age: int) -> str:
    """Scene-image prefix targeting THIS video's audience age.

    A 60+ idea must render people in their 60s, not a hardcoded 45.
    ``_ASSET_GEN_PROMPT_PREFIX`` is kept for back-compat (default 45).
    """
    return (
        "Photorealistic, 16:9 cinematic, soft natural light, no text overlay, "
        f"no watermark, no logos. Audience: adultos {min_age}+. Scene visual: "
    )


def _scene_project_name(job_id: str, scene_id: str) -> str:
    return f"{job_id[:35]}-{scene_id}"[:45]


_VARIANT_STRATEGY = {
    1: (
        "FACE-DRIVEN: subject's emotional close-up is the dominant element. "
        "Tight framing, intense eye contact toward the viewer, the prop is "
        "secondary and partly out of focus."
    ),
    2: (
        "OBJECT-DRIVEN: the single topic prop is large, sharply lit, and "
        "instantly communicates the topic at a glance. The subject is present "
        "but positioned so the prop carries the meaning."
    ),
    3: (
        "COMPARISON-DRIVEN: a clean visual contrast or simple choice — two "
        "options, before/after of a daily habit, or right vs wrong routine — "
        "without medical fear, weight-loss before/after, or shaming framing."
    ),
}


def _topic_category_guidance() -> str:
    return (
        "If the topic is about sleep or rest, show evening routine cues (bed, "
        "lamp, calm tea, phone put down). "
        "If the topic is about food or digestion, show the specific food, "
        "plate, or kitchen choice. "
        "If the topic is about stiffness, mobility, or movement, show body "
        "signals or gentle home movement (neck, back, hands, stairs, walk). "
        "If the topic is about stress or mental load, show calm body language, "
        "warm light, breathing or a simple pause. "
        "If the topic is about energy, show daylight, water, balanced breakfast, "
        "or a clear morning routine. "
        "Never reuse a generic wellness portrait."
    )


def _build_thumbnail_prompt(
    title: str,
    thumbnail_text: str,
    accent_color: str,
    channel_description: str,
    *,
    variant_index: int = 1,
) -> str:
    """Backward-compatible wrapper around :func:`thumbnail_planner.plan_thumbnail_prompts`.

    Phase 2 of spec v1.3 routes all thumbnail prompt construction through
    the planner. Direct callers/tests using the old signature still work
    via this shim; the planner produces the actual prompt body.
    """
    from video_agent.thumbnail_planner import plan_thumbnail_prompts

    seo = {
        "title": title,
        "title_variants": [
            {"title": title, "thumbnail_text": thumbnail_text}
        ],
    }
    channel_config = {
        "description": channel_description,
        "thumbnail": {"accent_color": accent_color},
    }
    plans = plan_thumbnail_prompts(seo, channel_config)
    plan = plans[0]
    # If the wrapper was asked for variant_index > 1, switch the visual strategy
    # accordingly so the prompt reflects the requested face/object/comparison
    # composition without rerouting the underlying classification.
    if variant_index in {2, 3}:
        from video_agent.thumbnail_planner import (
            ART_DIRECTION_BY_STRATEGY,
            VISUAL_STRATEGIES,
            build_thumbnail_prompt,
            describe_strategy,
            select_thumbnail_persona,
        )

        strategy = VISUAL_STRATEGIES.get(variant_index, "face_driven")
        plan = dict(plan)
        plan["visual_strategy"] = strategy
        plan["visual_strategy_description"] = describe_strategy(strategy)
        plan["variant_art_direction"] = ART_DIRECTION_BY_STRATEGY[strategy]
        plan["persona"] = select_thumbnail_persona(plan, strategy, variant_index)
        plan["persona_locked"] = False
        return build_thumbnail_prompt(plan)
    return plan["prompt"]


# Deprecated P0+P1 helpers kept temporarily for any test importing them.
def _legacy_build_thumbnail_prompt(
    title: str,
    thumbnail_text: str,
    accent_color: str,
    channel_description: str,
    *,
    variant_index: int = 1,
) -> str:
    """Original inline template prior to v1.3 planner integration."""
    variant_style = _VARIANT_STRATEGY.get(variant_index, _VARIANT_STRATEGY[1])
    topic_guidance = _topic_category_guidance()
    safe_text = (thumbnail_text or "").strip()

    return (
        f"Create a complete photorealistic YouTube thumbnail, 16:9, 1920x1080, "
        f"editorial magazine quality, sharp details, warm natural light.\n"
        f"\n"
        f"TOPIC: \"{title}\"\n"
        f"CHANNEL: {channel_description}\n"
        f"AUDIENCE: Spanish adults 45+, practical wellness, nutrition and "
        f"lifestyle, Spain-first tone (not Latin American).\n"
        f"\n"
        f"VARIANT STRATEGY: {variant_style}\n"
        f"\n"
        f"VISUAL ANGLE: The image must express the same specific pain angle "
        f"as the topic and hook. {topic_guidance}\n"
        f"\n"
        f"SUBJECT: A natural-looking Mediterranean Spanish adult aged 45-55. "
        f"Skin texture realistic (not plastic, not airbrushed). Hair, eyes, "
        f"and skin reflect a Spain/Mediterranean look, not Latin American "
        f"styling. Expressive but not panicked — concerned and hopeful, "
        f"practical urgency. Place the subject in the LEFT 45% of the frame, "
        f"face clearly visible, sharp eyes, tasteful warm key + fill + rim "
        f"lighting. Avoid frail-elderly stereotype, doctor framing, sad "
        f"isolated senior cliché, or overly polished fitness model.\n"
        f"\n"
        f"TOPIC PROP: Include exactly ONE realistic physical object that "
        f"signals the topic instantly — a genuine in-scene photograph, NOT "
        f"an icon, sticker, emoji, or illustration. Lit and focused to match "
        f"the scene. Do not cover the face or the text.\n"
        f"\n"
        f"TEXT OVERLAY — render this EXACT text only, baked into the image:\n"
        f"\"{safe_text}\"\n"
        f"Render the text EXACTLY as written, preserving Spanish accents and "
        f"punctuation (ñ, á, é, í, ó, ú, ü, ¿, ¡). Do not add, drop, or "
        f"transliterate any character. All caps, very bold, white, huge "
        f"(~40% of image width). Place text on the RIGHT half of the frame. "
        f"Use a thick black outline (5-6px, clean even width) plus a heavy "
        f"dark drop shadow. If the area behind the text is busy or light, "
        f"add a subtle dark gradient behind the text — never a hard box. "
        f"Font style similar to Impact, Anton, or Bebas Neue. Accent color "
        f"for a thin underline or glow under the text: {accent_color}.\n"
        f"\n"
        f"NEGATIVE: no extra text, no captions, no watermarks, no logos, no "
        f"UI elements. No hospital scene, no doctor diagnosis, no pills, no "
        f"miracle cure, no before/after weight-loss, no fear-based medical "
        f"setup, no LatAm or US Hispanic stock styling. No blurry, soft "
        f"focus, plastic skin, extra fingers, or warped anatomy.\n"
        f"\n"
        f"RULES: Only the subject, the prop, the background, and the exact "
        f"hook text \"{safe_text}\". Final result must hold up at 200% zoom."
    )


async def generate_scene_asset(
    job_dir: Path,
    channel_path: Path,
    scene_id: str,
    image_fn,
) -> dict:
    """Generate a ChatGPT image for ``scene_id`` and update scenes.json.

    Looks up the scene in ``scenes.json`` by id, builds an image prompt
    from its ``visual_prompt`` (plus a brand-consistent style prefix),
    calls ``image_fn(prompt, project_name, out_path)`` (typically
    ``BrowserClient.generate_image``), saves the bytes under
    ``jobs/<id>/assets/<scene_id>.png``, and patches the scene's
    ``asset_refs.primary`` to the relative path so the v2 render
    stage picks it up.

    Returns the image_fn payload plus the scene id.
    """
    scenes_path = _resolve_artifact(job_dir, ARTIFACT_SCENES)
    if not scenes_path.exists():
        raise StageInputMissingError(f"Missing {scenes_path}")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", scene_id or ""):
        raise StageInputMissingError(f"Invalid scene_id: {scene_id!r}")
    scenes_doc = json.loads(scenes_path.read_text(encoding="utf-8"))
    target = None
    for s in scenes_doc.get("scenes", []):
        if s.get("id") == scene_id:
            target = s
            break
    if target is None:
        raise StageInputMissingError(
            f"Scene {scene_id!r} not found in {scenes_path}"
        )
    visual_prompt = target.get("visual_prompt") or target.get("caption") or ""
    if not visual_prompt:
        raise StageInputMissingError(
            f"Scene {scene_id} has no visual_prompt to feed image gen."
        )

    state = load_job(job_dir)
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    out_path = assets_dir / f"{scene_id}.png"
    project_name = _scene_project_name(state.job_id, scene_id)

    # Per-video audience age: a 60+ idea must render 60+ people, not a hardcoded
    # 45. Resolve from the SEO title (carries the idea's age phrase) + this
    # scene's own text, falling back to the channel's configured audience floor.
    channel_cfg = read_yaml(channel_path) or {}
    seo_title = ""
    try:
        seo_path = _resolve_artifact(job_dir, ARTIFACT_SEO, "seo.json")
        if seo_path.exists():
            seo_title = str((json.loads(seo_path.read_text(encoding="utf-8")) or {}).get("title") or "")
    except Exception:
        seo_title = ""
    min_age = resolve_target_min_age(channel_cfg, seo_title, str(visual_prompt))
    prompt = _asset_gen_prefix(min_age) + visual_prompt

    # Presenter identity is described in TEXT (no reference photo attached): any
    # person in the scene image is the channel presenter; person-less scenes are
    # unaffected. resolve_persona_reference is reused only as "presenter configured?"
    from video_agent.persona import PERSONA_SCENE_INSTRUCTION, resolve_persona_reference

    if resolve_persona_reference(read_yaml(channel_path) or {}):
        prompt = prompt + PERSONA_SCENE_INSTRUCTION

    _log_image_prompt(
        job_dir,
        stage="assets_chatgpt",
        kind="scene_asset",
        prompt=prompt,
        scene_id=scene_id,
        out_path=str(out_path),
        project_name=project_name,
    )

    result = await image_fn(
        prompt=prompt,
        project_name=project_name,
        out_path=str(out_path),
    )

    # Update scenes.json -> asset_refs.primary with the job-relative path.
    rel = str(out_path.relative_to(job_dir))
    refs = target.get("asset_refs")
    if not isinstance(refs, dict):
        refs = {}
    refs["primary"] = rel
    refs["primary_source"] = "chatgpt_image"
    refs["primary_url"] = result.get("src", "")
    refs["primary_project"] = project_name
    target["asset_refs"] = refs
    _write_json(scenes_path, scenes_doc)

    EventLogger(job_dir / EVENT_LOG).log(
        "SCENE_ASSET_GENERATED",
        {
            "job_id": state.job_id,
            "scene_id": scene_id,
            "local_path": rel,
            "bytes": result.get("bytes"),
        },
    )
    return {"scene_id": scene_id, **result, "asset_refs_primary": rel}


# ---------------------------------------------------------------------------
# Batch image generation stage: assets_chatgpt.
# ---------------------------------------------------------------------------


async def auto_assets_chatgpt_stage(
    job_dir: Path,
    channel_path: Path,
    image_fn,
    *,
    throttle_sec: float = 8.0,
) -> Path:
    """Generate ChatGPT images for every scene, with per-scene throttle + fallback.

    Iterates all scenes in scenes.json and calls ``generate_scene_asset()``
    for each. A failed scene logs a ``SCENE_ASSET_FAILED`` event and
    continues — the render stage falls back to stock/placeholder for that
    scene so the pipeline never halts on a single image failure.

    Returns ``scenes.json`` (updated in-place with ``asset_refs.primary``
    for each successfully generated scene).
    """
    stage_name = "assets_chatgpt"
    state = load_job(job_dir)
    if not dag_mode() and state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} from current_stage={state.current_stage!r}"
        )

    scenes_path = _resolve_artifact(job_dir, ARTIFACT_SCENES)
    if not scenes_path.exists():
        raise StageInputMissingError(f"Missing {scenes_path}")

    scenes_doc = json.loads(scenes_path.read_text(encoding="utf-8"))
    logger = EventLogger(job_dir / EVENT_LOG)
    scene_ids: list[str] = []
    for s in scenes_doc.get("scenes", []):
        sid = s.get("id")
        if sid:
            scene_ids.append(sid)
        else:
            logger.log(
                "SCENE_ASSET_FAILED",
                {"job_id": state.job_id, "scene_id": None, "error": "scene missing id"},
            )

    for idx, scene_id in enumerate(scene_ids):
        if idx > 0:
            await asyncio.sleep(throttle_sec)
        try:
            await generate_scene_asset(job_dir, channel_path, scene_id, image_fn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.log(
                "SCENE_ASSET_FAILED",
                {"job_id": state.job_id, "scene_id": scene_id, "error": str(exc)},
            )

    _complete_stage(job_dir, stage_name, scenes_path)
    return scenes_path


# ---------------------------------------------------------------------------
# Thumbnail background image generation stage.
# ---------------------------------------------------------------------------


async def auto_thumbnail_image_stage(
    job_dir: Path,
    channel_path: Path,
    image_fn,
    *,
    throttle_sec: float = 8.0,
) -> Path:
    """Generate full-composite thumbnails (background + text baked in) via ChatGPT.

    Generates one JPEG per title_variant (up to 3) so each has its own
    visually coherent hook text. Outputs:
      jobs/<id>/thumbnail_1.jpg  ← variant 1 (primary)
      jobs/<id>/thumbnail_2.jpg  ← variant 2
      jobs/<id>/thumbnail_3.jpg  ← variant 3
      jobs/<id>/thumbnail.jpg    ← alias of thumbnail_1.jpg (backward compat)

    The render stage detects these files and skips the Remotion still step.
    """
    import shutil as _shutil

    from PIL import Image as _PilImage
    from PIL import ImageOps as _PilImageOps

    stage_name = "thumbnail_image"
    state = load_job(job_dir)
    if not dag_mode() and state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} from current_stage={state.current_stage!r}"
        )

    seo_path = _resolve_artifact(job_dir, ARTIFACT_SEO)
    if not seo_path.exists():
        raise StageInputMissingError(f"Missing {seo_path}")
    seo = json.loads(seo_path.read_text(encoding="utf-8"))

    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")
    channel_config = read_yaml(channel_path)

    title = seo.get("title") or ""
    # Brand-wide fallback accent (used only when the seo stage didn't produce a
    # valid per-video topic_accent_color) comes from style-dna.json, the single
    # source of truth for this channel's brand palette — not a hardcoded hex.
    brand_style = load_style_dna(channel_path)
    accent_color = (brand_style.get("palette") or {}).get("accent", "#F2C94C")

    # English channel context for ChatGPT image prompt. The raw `channel.description`
    # is Spanish (Vida Plena 45+); injecting it into an English prompt confuses
    # the image model. Use a stable English summary instead.
    channel_description = (
        "Vida Plena 45+, practical wellness, nutrition, sleep, movement, and "
        "daily lifestyle for Spanish adults aged 45 and over, Spain-first tone."
    )

    # Spec v1.3 Phase 2: route the variant list, classification, and prompt
    # body through the topic-aware planner. The planner enforces variant
    # title binding, three distinct visual strategies (face/object/comparison),
    # category presets, avoid-list merge, and Spain-first persona.
    from video_agent.thumbnail_planner import plan_thumbnail_prompts

    planner_channel_config = dict(channel_config or {})
    planner_channel_config["description"] = channel_description
    planner_channel_config["style"] = brand_style
    planner_thumbnail_config = dict(
        planner_channel_config.get("thumbnail") or {}
    )
    planner_thumbnail_config.setdefault("accent_color", accent_color)
    planner_channel_config["thumbnail"] = planner_thumbnail_config
    plans = plan_thumbnail_prompts(seo, planner_channel_config)

    variants: list[dict[str, str]] = [
        {
            "title": plan["variant_title"],
            "thumbnail_text": plan["thumbnail_text"],
        }
        for plan in plans
    ]

    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "json").mkdir(parents=True, exist_ok=True)
    (job_dir / "outputs").mkdir(parents=True, exist_ok=True)
    prompt_log_dir = job_dir / "operator" / "chatgpt"
    prompt_log_dir.mkdir(parents=True, exist_ok=True)

    # Write structured metadata JSON excluding full prompts
    plans_json = []
    for plan in plans:
        item = dict(plan)
        item.pop("prompt", None)
        plans_json.append(item)
    (job_dir / "json" / "thumbnail_prompt_plans.json").write_text(
        json.dumps(plans_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger = EventLogger(job_dir / EVENT_LOG)
    generated: list[Path] = []   # successfully created .jpg files
    errors: list[str] = []
    last_exc: Exception | None = None

    def _save_thumbnail(source_path: Path, jpg_path: Path) -> None:
        """Convert PNG → JPG and enforce 1920x1080 16:9 dimensions.

        ChatGPT image models do not honor the requested output dimensions
        consistently. We crop-fit to the canonical YouTube thumbnail size so
        downstream Remotion stills and uploads always see the expected ratio.
        """
        img = _PilImage.open(source_path).convert("RGB")
        img = _PilImageOps.fit(
            img, (1920, 1080), method=_PilImage.Resampling.LANCZOS
        )
        img.save(jpg_path, "JPEG", quality=94, optimize=True)

    def _write_prompt_log(
        index: int,
        prompt_text: str,
        variant: dict[str, str],
        *,
        strategy: str | None = None,
        out_path: str | None = None,
        project_name: str | None = None,
    ) -> None:
        log_path = prompt_log_dir / f"thumbnail_prompt_{index}.md"
        body = (
            f"# Thumbnail prompt — variant {index}\n\n"
            f"- job_id: `{state.job_id}`\n"
            f"- variant_title: {variant.get('title', '')}\n"
            f"- thumbnail_text: {variant.get('thumbnail_text', '')}\n\n"
            f"```text\n{prompt_text}\n```\n"
        )
        log_path.write_text(body, encoding="utf-8")
        # Unified per-job image-prompt audit trail (thumbnail + scene asset +
        # graphic all funnel here) — record the exact prompt sent to ChatGPT.
        _log_image_prompt(
            job_dir,
            stage=stage_name,
            kind="thumbnail",
            prompt=prompt_text,
            index=index,
            strategy=strategy,
            out_path=out_path,
            project_name=project_name,
        )

    has_batch = hasattr(image_fn, "generate_images")
    if has_batch:
        prompts = []
        png_paths = []
        jpg_paths = []
        project_name = f"{state.job_id[:30]}-thumbnails"[:45]
        for i, (plan, variant) in enumerate(zip(plans, variants), start=1):
            prompt = plan["prompt"]
            _write_prompt_log(
                i, prompt, variant,
                strategy=plan.get("visual_strategy"),
                out_path=str((assets_dir / f"thumbnail_{i}.png").resolve()),
                project_name=project_name,
            )
            prompts.append(prompt)
            png_paths.append((assets_dir / f"thumbnail_{i}.png").resolve())
            jpg_paths.append((job_dir / "outputs" / f"thumbnail_{i}.jpg").resolve())

        # Presenter identity is described in the prompt text (build_thumbnail_prompt),
        # not attached as a reference photo — the attachment path was unreliable.
        try:
            await image_fn.generate_images(
                prompts=prompts,
                project_name=project_name,
                out_paths=[str(p) for p in png_paths],
            )
            for i, (png_path, jpg_path, variant) in enumerate(
                zip(png_paths, jpg_paths, variants), start=1
            ):
                thumb_text = variant["thumbnail_text"]
                if png_path.exists():
                    _save_thumbnail(png_path, jpg_path)
                    png_path.unlink(missing_ok=True)  # remove intermediate PNG
                    generated.append(jpg_path)
                    logger.log(
                        "THUMBNAIL_IMAGE_GENERATED",
                        {
                            "job_id": state.job_id,
                            "variant": i,
                            "path": str(jpg_path),
                            "text": thumb_text,
                            "variant_title": variant.get("title"),
                        },
                    )
                else:
                    errors.append(f"variant {i} ('{thumb_text}'): Output image file not found.")
                    logger.log(
                        "THUMBNAIL_IMAGE_FAILED",
                        {"job_id": state.job_id, "variant": i, "error": "Output image file missing"},
                    )
        except Exception as exc:
            last_exc = exc
            errors.append(f"Batch generation failed: {exc}")
            logger.log(
                "THUMBNAIL_IMAGE_BATCH_FAILED",
                {"job_id": state.job_id, "error": str(exc)},
            )
    else:
        for i, (plan, variant) in enumerate(zip(plans, variants), start=1):
            if i > 1:
                await asyncio.sleep(throttle_sec)

            thumb_text = variant["thumbnail_text"]
            prompt = plan["prompt"]
            project_name = f"{state.job_id[:30]}-thumb{i}"[:45]
            png_path = (assets_dir / f"thumbnail_{i}.png").resolve()
            _write_prompt_log(
                i, prompt, variant,
                strategy=plan.get("visual_strategy"),
                out_path=str(png_path),
                project_name=project_name,
            )
            jpg_path = (job_dir / "outputs" / f"thumbnail_{i}.jpg").resolve()

            try:
                response = await image_fn(
                    prompt=prompt,
                    project_name=project_name,
                    out_path=str(png_path),
                )

                # Convert PNG → JPG (Pillow — already a project dependency)
                source_path = png_path
                if not source_path.exists() and isinstance(response, dict):
                    returned_path = response.get("local_path")
                    if returned_path:
                        source_path = Path(str(returned_path)).expanduser()
                if not source_path.exists():
                    raise FileNotFoundError(f"Generated image file not found: {png_path}")
                _save_thumbnail(source_path, jpg_path)
                source_path.unlink(missing_ok=True)  # remove intermediate PNG

                generated.append(jpg_path)
                logger.log(
                    "THUMBNAIL_IMAGE_GENERATED",
                    {
                        "job_id": state.job_id,
                        "variant": i,
                        "path": str(jpg_path),
                        "text": thumb_text,
                        "variant_title": variant.get("title"),
                    },
                )
            except Exception as exc:
                last_exc = exc
                errors.append(f"variant {i} ('{thumb_text}'): {exc}")
                logger.log(
                    "THUMBNAIL_IMAGE_FAILED",
                    {"job_id": state.job_id, "variant": i, "error": str(exc)},
                )

    if not generated:
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(
            "All thumbnail variants failed: " + "; ".join(errors)
        )

    # thumbnail.jpg = alias of the FIRST successfully generated variant.
    # Uses generated[0] (not hardcoded thumbnail_1.jpg) so that if variant 1
    # failed but variant 2+ succeeded, thumbnail.jpg is still populated.
    primary = generated[0]
    _shutil.copy2(primary, job_dir / ARTIFACT_THUMBNAIL)

    # Copy all generated thumbnails to remotion/public/ so Remotion Studio
    # and the Thumbnail.tsx preview component can load them via staticFile().
    workspace_root = job_dir.parent
    for parent in job_dir.parents:
        if (parent / "remotion").is_dir():
            workspace_root = parent
            break
    public_job_dir = prepare_public_job_dir(workspace_root, job_dir.name)
    public_outputs_dir = public_job_dir / "outputs"
    public_outputs_dir.mkdir(exist_ok=True)
    for jpg in generated:
        _shutil.copy2(jpg, public_outputs_dir / jpg.name)
    _shutil.copy2(primary, public_outputs_dir / "thumbnail.jpg")

    # seo.thumbnail_path: use public-relative path of the primary thumbnail.
    # staticFile()-compatible so Remotion Studio can load it in Thumbnail.tsx.
    public_ref = f"jobs/{job_dir.name}/outputs/{primary.name}"
    _persist_thumbnail_path(seo_path, seo, public_ref)

    if errors:
        logger.log(
            "THUMBNAIL_IMAGE_PARTIAL",
            {"job_id": state.job_id, "generated": len(generated), "errors": errors},
        )

    _complete_stage(job_dir, stage_name, seo_path)
    return seo_path


def _persist_thumbnail_path(seo_path: Path, seo_snapshot: dict, public_ref: str) -> dict:
    """Attach ``thumbnail_path`` to seo.json via a serialized single-field update.

    bug-528/bug-529: the thumbnail stage reads seo.json at start, then spends
    ~40 minutes on image generation while whisper_timestamps concurrently
    rewrites the description's chapter block. A fresh-read-then-full-write
    only NARROWED the race; ``operator.update_seo_fields`` eliminates it —
    an exclusive lock around read-merge-write, and this writer only ever
    touches its OWN field, so it can never resurrect a stale snapshot of
    fields it does not own."""
    from video_agent.operator import update_seo_fields

    # NO stale-snapshot fallback (bug-529 acceptance rule): if the current
    # artifact is unreadable, failing the stage loudly is safer than silently
    # resurrecting a full stale snapshot over newer fields written by
    # concurrent stages. ``seo_snapshot`` is intentionally unused for writing.
    del seo_snapshot
    return update_seo_fields(seo_path, {"thumbnail_path": public_ref})
