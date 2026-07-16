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

import asyncio
import hashlib
import os
import time
from pathlib import Path
from typing import Any

from video_agent.audience_age import resolve_target_min_age
from video_agent.contracts import ARTIFACT_SCENES, ARTIFACT_SEO, EVENT_LOG, repo_root
from video_agent.orchestrator.image_prompt_log import (
    safe_log_image_prompt as _log_image_prompt,
)
from video_agent.orchestrator.job_state import load_job
from video_agent.orchestrator.stages._shared import (
    StageInputMissingError,
    _complete_stage,
    _resolve_artifact,
    _start_stage,
    dag_mode,
)
from video_agent.storage.atomic import atomic_write_json
from video_agent.utils.json_io import read_json, read_yaml
from video_agent.utils.logging import EventLogger
from video_agent.visual.spans import GRAPHIC_LAYOUTS

__all__ = ["run_graphic_images_stage"]

_STAGE = "graphic_images"


def _late_recovery_window_sec() -> float:
    """How long (seconds) after a client-side generation failure the
    end-of-stage sweep keeps waiting for the browser-worker's late server-side
    PNG write. Observed lag in production: 123s (scene-27, Codex
    20260704-130051). Read at run time so tests can set
    GRAPHIC_LATE_RECOVERY_WINDOW_SEC=0 to skip the wait."""
    return float(os.environ.get("GRAPHIC_LATE_RECOVERY_WINDOW_SEC", "180"))
def _prompt_prefix(min_age: int) -> str:
    """Content-first image prefix targeting THIS video's audience age.

    ``min_age`` follows the idea (a 60+ idea renders 60+ people), not a hardcoded
    45 — resolved from the video's SEO title / channel floor by the stage.
    """
    return (
        "Content-first editorial image for a Spanish-language YouTube video aimed at "
        f"adults {min_age}+. The visual concept must come from THIS scene's narration and "
        "background prompt, not from a fixed channel template. This image "
        "is shown FULL-SCREEN as-is with NO captions added afterwards, so all on-screen "
        "text must be rendered directly INTO the image. "
        "Anatomy must look natural: avoid close-up hands, fingers, or utensils held mid-air; "
        "keep hands relaxed, partially out of frame or softly out of focus; absolutely no "
        "malformed hands, extra fingers, or distorted faces. "
    )


def _text_to_render(scene: dict[str, Any]) -> str:
    """The short on-screen label to bake into the graphic (``on_screen_text`` then
    ``caption``)."""
    for key in ("on_screen_text", "caption"):
        text = str(scene.get(key) or "").strip()
        if text:
            return text
    return ""


def _wants_graphic(scene: dict[str, Any]) -> bool:
    if str(scene.get("layout") or "").strip().lower() not in GRAPHIC_LAYOUTS:
        return False
    graphic = scene.get("graphic")
    # Default: a graphic-layout scene wants an image unless explicitly opted out.
    if isinstance(graphic, dict) and graphic.get("needed") is False:
        return False
    return True


def _load_channel_name(channel_path: Path | None) -> str:
    """Channel display name from channel.yaml (``channel.name``). Baked into CTA
    card images so viewers see the exact channel to subscribe to."""
    if channel_path is None:
        return ""
    try:
        cfg = read_yaml(Path(channel_path))
        return str(((cfg or {}).get("channel") or {}).get("name") or "").strip()
    except Exception:
        return ""


def _content_first_style() -> str:
    """Scene-specific art direction for long-form graphic images.

    User feedback (2026-07-09): channel style-DNA made ChatGPT graphics collapse
    into one repeated wellness card, flattening the creative content from the
    script. Long-form graphics should vary with the scene; thumbnails keep their
    own prompt path and are not affected here.

    bug-542: this prohibition must not NAME the colours it forbids — a negative
    instruction still conditions the image model on those tokens (same leak class
    as bug-541). Describe the anti-pattern structurally instead.
    """
    return (
        "CONTENT-FIRST ART DIRECTION: use the scene's specific idea, setting, object, "
        "tension, and action as the main creative driver. Do NOT force a recurring "
        "brand palette, a repeated house colour scheme, a fixed soft panel, a recurring "
        "channel card, or a generic living-room/kitchen template unless the scene itself "
        "calls for it. Choose colours, materials, camera angle, background, and graphic "
        "treatment that fit the concrete moment described by this scene. Keep the text "
        "large, sharp, and legible at video size, but let the composition vary from scene "
        "to scene."
    )


_CARD_KIND = {
    "hook": "a bold full-bleed hook TITLE BANNER — headline set VERY large across the whole frame with minimal panel, no bullet list and no check-marks",
    "checklist": "a clean checklist card, each item on its own row led by a small check-mark or clear marker",
    "quote": "an editorial quote card: one large centered sentence in quotation marks with a visual treatment that fits the scene, no check-marks, no bullet list",
    "cta": "a call-to-action card with a clear button",
    "warning": "a cautionary card using cross / caution markers (not check-marks), with an 'avoid this' tone",
    "stat": "a bold statistic card built around ONE ENORMOUS number as the hero, the number filling roughly half the card with only a small label — center or left aligned",
    "steps": "a numbered step-by-step timeline card on a side panel: ordered items 1, 2, 3 connected by arrows or chevrons, no check-marks",
    "comparison": "a two-column comparison card: two options side by side, each on its own half, separated by a clear central divider, using neutral equal-weight colours for both halves",
    "myth": "a myth-versus-fact card: two stacked contrasting rows, a cross/caution 'Mito' row above a check/confirmation 'Realidad' row",
    "plate_map": "a top-down 'healthy plate' card: ONE round plate of real food, each component labelled DIRECTLY on/beside its section with a short marker line",
    "recipe_snapshot": "a recipe-snapshot card: 2-3 real food photos as clean side-by-side tiles, each with a short label — a practical example, not a text list",
    "quote_portrait": (
        "a magazine-style quote-portrait card: ONE large quotation beside a warm candid "
        "portrait of an adult 60+, editorial cover feel, no boxed text panel — but the "
        "graphic element still needs a real, sizable presence without a boxed panel: "
        "render the opening quotation mark OVERSIZED and solid, AND add a bold rule/bar "
        "beneath the quote spanning at least half its width"
    ),
    "evidence_nugget": "an evidence-nugget card: ONE number/fact as a large documentary-style lower-third over a real photo, serious and credible, minimal extra text",
    "do_dont": "a do-versus-don't card: TWO real photos side by side — the worse choice desaturated with a cross marker, the better choice bright with a check marker",
}


def _content_lines(layout: str, title: str, body: str, bullets: list[str], cta: str) -> list[str]:
    """Structural render instructions per layout — each type gets a DISTINCT shape so the
    cards stop collapsing into a universal check-mark list (the old behaviour)."""
    items = "; ".join(f'"{b}"' for b in bullets)
    lines: list[str] = []
    if layout == "stat":
        if title:
            lines.append(f'ONE ENORMOUS focal number/stat as the hero, filling about half the card: "{title}".')
        if body:
            lines.append(f'A small label beneath the number, MUCH smaller than the number: "{body}".')
        return lines
    if layout == "steps":
        if title:
            lines.append(f'Heading: "{title}".')
        if bullets:
            lines.append(
                "An ordered, numbered step-by-step flow (1, 2, 3 …) connected by arrows or "
                f"chevrons, NOT check-marks: {items}."
            )
        if body:
            lines.append(f'Closing line: "{body}".')
        return lines
    if layout == "comparison":
        if title:
            lines.append(f'Heading: "{title}".')
        if len(bullets) >= 2:
            lines.append(
                "TWO side-by-side columns separated by a central divider — "
                f'left: "{bullets[0]}", right: "{bullets[1]}".'
            )
        elif title and body:
            lines.append(
                f'TWO side-by-side columns separated by a central divider — left: "{title}", right: "{body}".'
            )
        # Semantics: the two sides may be two EXTREMES or two neutral options,
        # not a good-vs-bad pair (real incident: a column tinted as "good" but
        # labelled "too little" read as the recommended choice and contradicted
        # the heading). Recommendation styling is gated on ONE side being
        # explicitly correct. Stated WITHOUT naming colours: a negative
        # instruction that names them still conditions the model (bug-542).
        lines.append(
            "Give BOTH columns neutral, EQUAL-WEIGHT treatment and a plain central "
            "divider: neither side may read as the recommended or 'correct' one "
            "through tint, contrast, iconography, or emphasis. Do NOT apply a "
            "good-versus-bad styling by default. Use approval/rejection marks or an "
            "emphasis treatment on one column ONLY if that column is explicitly the "
            "recommended answer in the text above."
        )
        return lines
    if layout == "myth":
        if title:
            lines.append(f'Top row labelled "Mito" with a cross or caution icon: "{title}".')
        if body:
            lines.append(f'Bottom row labelled "Realidad" with a check or confirmation icon: "{body}".')
        return lines
    if layout == "plate_map":
        if title:
            lines.append(f'Small heading: "{title}".')
        if bullets:
            lines.append(f"Label each plate component DIRECTLY on/beside its portion with a short marker line: {items}.")
        return lines
    if layout == "recipe_snapshot":
        if title:
            lines.append(f'Small heading: "{title}".')
        if bullets:
            lines.append(f"Show {len(bullets)} real food-photo tiles side by side, each with its short label: {items}.")
        return lines
    if layout == "quote_portrait":
        quote = body or title
        if quote:
            lines.append(f'ONE large magazine-style quotation in quote marks beside a warm candid portrait of an adult 60+: "{quote}". No bullet list.')
        return lines
    if layout == "evidence_nugget":
        if title:
            lines.append(f'ONE bold number/fact as a large documentary lower-third over a real photo: "{title}".')
        if body:
            lines.append(f'A small context line beneath it: "{body}".')
        return lines
    if layout == "do_dont":
        bad = bullets[0] if len(bullets) >= 1 else title
        good = bullets[1] if len(bullets) >= 2 else body
        if bad:
            lines.append(f'LEFT photo (the WORSE choice): desaturated, with a cross marker — "{bad}".')
        if good:
            lines.append(f'RIGHT photo (the BETTER choice): bright, with a check marker — "{good}".')
        return lines
    if layout == "warning":
        if title:
            lines.append(f'Heading: "{title}".')
        if bullets:
            lines.append(
                f"Each item on its own row led by a cross / caution icon (not a check-mark): {items}."
            )
        if body:
            lines.append(f'Supporting line: "{body}".')
        return lines
    if layout == "quote":
        quote = body or title
        if quote:
            lines.append(f'ONE large centered sentence in quotation marks, no bullets and no check-marks: "{quote}".')
        return lines
    if layout == "hook":
        if title:
            lines.append(f'ONE very large headline, no bullet list and no check-marks: "{title}".')
        if body:
            lines.append(f'At most one short support line beneath it: "{body}".')
        return lines
    # checklist + cta + default: heading plus readable row markers.
    if title:
        lines.append(f'Heading: "{title}".')
    if bullets:
        lines.append(f"Checklist items, each on its own row with a small check-mark or clear marker icon: {items}.")
    if body:
        lines.append(f'Supporting line: "{body}".')
    if cta and layout == "cta":
        lines.append(f'Call-to-action button labelled: "{cta}".')
    return lines


def _graphic_prompt(
    scene: dict[str, Any], font: str = "Manrope", brand: str = "", channel_name: str = ""
) -> str:
    g = scene.get("graphic")
    graphic = g if isinstance(g, dict) else {}
    visual = str(graphic.get("prompt") or scene.get("visual_prompt") or "").strip()

    # The renderer no longer overlays Remotion text on graphic scenes, so the image
    # must carry the FULL message — not just the short label. Pull the structured
    # content from layout_payload (title/body/bullets/cta), the same fields the old
    # Remotion templates rendered.
    layout = str(scene.get("layout") or "").strip().lower()
    lp_raw = scene.get("layout_payload")
    lp = lp_raw if isinstance(lp_raw, dict) else {}
    title = str(lp.get("title") or _text_to_render(scene) or "").strip()
    body = str(lp.get("body") or scene.get("caption") or "").strip()
    bullets = [str(b).strip() for b in (lp.get("bullets") or []) if str(b).strip()]
    cta = str(lp.get("cta") or "").strip()

    lines = _content_lines(layout, title, body, bullets, cta)

    parts: list[str] = []
    if visual:
        parts.append(f"Scene background: {visual}.")
    if lines:
        card = _CARD_KIND.get(layout, "an editorial text card")
        if brand:
            parts.append(brand)
        parts.append(
            f"Compose {card} with ALL of this Spanish text rendered legibly ON the image: "
            + " ".join(lines)
            + f" Set every word in {font} (or a near-identical clean geometric bold sans-serif). "
            "Lay it out with a clear visual hierarchy; make each line LARGE and BOLD with HIGH "
            "CONTRAST against the chosen background or panel so it is crisp and easy to read, "
            "never faint or washed out. Spell everything exactly with correct Spanish accents; "
            "add no other text."
        )
        if layout == "cta" and channel_name:
            # The final CTA card must show the exact channel name so viewers know
            # which channel to subscribe to (smaller than the heading, near the CTA).
            parts.append(
                f'Render the channel name "{channel_name}" clearly and correctly spelled '
                "near the call-to-action (smaller than the heading), exact accents, no other text."
            )
    return " ".join(parts).strip() or title or visual


def _prompt_hash(prompt: str) -> str:
    """Short stable hash of the effective prompt, used to detect when a
    cached graphic PNG was generated from a DIFFERENT prompt/palette than the
    current run would produce (bug-465) — e.g. seo.topic_accent_color changed
    between runs. Not a security hash, just cheap change-detection."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def _publish_graphic(job_id: str, out_path: Path) -> str:
    """Copy a generated graphic into ``remotion/public/jobs/<job_id>/assets/`` and
    return the public-resolvable ``image_ref`` (``jobs/<job_id>/assets/<name>``).

    Remotion ``staticFile`` resolves against ``remotion/public``, so a bare
    job-relative ``assets/graphic-XX.png`` ref 404s at render time (the file lives
    under ``jobs/<id>/``) and the render hangs on the first graphic scene. Mirror
    the background path: materialize into the public job dir AND carry the
    ``jobs/<job_id>/`` prefix.
    """
    import shutil

    public_dir = repo_root() / "remotion" / "public" / "jobs" / job_id / "assets"
    public_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(out_path, public_dir / out_path.name)
    except Exception:
        pass
    return f"jobs/{job_id}/assets/{out_path.name}"


async def run_graphic_images_stage(job_dir: Path, channel_path: Path | None, image_fn) -> Path:
    """Generate ChatGPT images for graphic scenes; record ``graphic.image_ref``.

    Returns the scenes.json path. Per-scene failures are logged and skipped so the
    pipeline never halts on a single image error.
    """
    state = load_job(job_dir)
    if not dag_mode() and state.current_stage != _STAGE:
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
    # Card typography is locked to Montserrat to match the Remotion subtitle font
    # (one typeface across the whole video). Overrides any style-DNA headline font.
    font = "Montserrat"
    brand = _content_first_style()
    channel_name = _load_channel_name(channel_path)

    # Per-video audience age: a 60+ idea must render 60+ people, not a hardcoded
    # 45. Resolve from the SEO title (carries the idea's age phrase) + early
    # scene narration, falling back to the channel's configured audience floor.
    channel_cfg = read_yaml(channel_path) if channel_path else {}
    seo_title = ""
    try:
        seo_path = _resolve_artifact(job_dir, ARTIFACT_SEO, "seo.json")
        if seo_path.exists():
            seo_title = str((read_json(seo_path) or {}).get("title") or "")
    except Exception:
        seo_title = ""
    early_narration = " ".join(
        str(s.get("narration") or "") for s in (scene_doc.get("scenes") or [])[:6]
    )
    min_age = resolve_target_min_age(channel_cfg, seo_title, early_narration)

    generated = 0
    failed = 0
    def _stamp_graphic(scene: dict, scene_id: str, out_path: Path, prompt_hash: str) -> dict:
        """Persist the full per-scene graphic metadata block (image_ref, prompt
        hash, and style-DNA status) — shared by the reuse, fresh-generation and
        late-recovery paths so the three can never drift apart."""
        existing = scene.get("graphic") if isinstance(scene.get("graphic"), dict) else {}
        graphic = dict(existing)
        graphic.pop("failed", None)
        graphic.pop("error", None)
        for key in (
            "effective_palette",
            "raw_topic_accent_color",
            "brand_anchor_color",
            "resolved_accent_color",
            "mix_ratio",
            "brand_background_color",
            "resolved_background_color",
            "background_mix_ratio",
            "brand_text_color",
            "resolved_text_color",
            "text_mix_ratio",
        ):
            graphic.pop(key, None)
        graphic["needed"] = True
        graphic["image_ref"] = _publish_graphic(state.job_id, out_path)
        graphic["prompt_hash"] = prompt_hash
        graphic["style_dna_disabled"] = True
        scene["graphic"] = graphic
        return graphic

    # Failed generations kept for the end-of-stage late-recovery sweep. The
    # browser-worker often finishes and writes the PNG a minute or two AFTER
    # the client-side request already failed (observed: scene-27 request
    # failed 09:50:40Z, valid PNG landed 09:52:43Z) — without the sweep that
    # finished card is silently abandoned and the scene downgrades to a plain
    # video background (Codex bridge 20260704-130051).
    pending_failures: list[dict[str, Any]] = []

    for scene in scene_doc.get("scenes") or []:
        if not _wants_graphic(scene):
            continue
        scene_id = str(scene.get("id") or "")
        prompt = _graphic_prompt(scene, font, brand, channel_name)
        if not scene_id or not prompt:
            failed += 1
            logger.log("SCENE_GRAPHIC_SKIPPED", {"scene_id": scene_id, "reason": "no_prompt"})
            continue
        prompt_hash = _prompt_hash(prompt)
        out_path = assets_dir / f"graphic-{scene_id}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        existing_graphic = scene.get("graphic") if isinstance(scene.get("graphic"), dict) else {}
        stored_hash = existing_graphic.get("prompt_hash")
        # A cached PNG is stale when it was generated from a DIFFERENT prompt/
        # palette than this run would produce (e.g. seo.topic_accent_color
        # changed between runs, bug-465) — regenerate instead of silently
        # shipping the OLD colour treatment. A MISSING stored hash counts as
        # stale too (bug-468): a scene with no prompt_hash yet has no proof
        # its cached PNG matches the CURRENT prompt logic, and this stage only
        # ever re-runs on a job when a human deliberately resets it (job.json
        # stages are otherwise skipped once "completed") -- so "no hash on
        # record" here means "please verify/regenerate", never "leave
        # untouched". Treating it as fresh silently shipped stale images with
        # scenes.json metadata falsely claiming the NEW palette (real incident,
        # 2026-07-03: all 22 graphics reused pre-bug-465 PNGs while resolved_*
        # fields were overwritten with the current run's colours).
        is_stale = stored_hash != prompt_hash
        # Idempotent / resumable: if a prior run already produced this image
        # from the SAME prompt, record the ref and skip the (slow, paid)
        # regeneration. Lets the stage resume after an interruption without
        # re-generating every graphic.
        if out_path.exists() and out_path.stat().st_size > 0 and not is_stale:
            graphic = _stamp_graphic(scene, scene_id, out_path, prompt_hash)
            generated += 1
            logger.log(
                "SCENE_GRAPHIC_GENERATED",
                {"scene_id": scene_id, "image_ref": graphic["image_ref"], "reused": True},
            )
            atomic_write_json(scenes_path, scene_doc)
            continue
        if is_stale:
            logger.log(
                "SCENE_GRAPHIC_STALE",
                {"scene_id": scene_id, "reason": "prompt_hash_changed", "old_hash": stored_hash, "new_hash": prompt_hash},
            )
        pre_attempt_mtime_ns = out_path.stat().st_mtime_ns if out_path.exists() else None
        full_prompt = _prompt_prefix(min_age) + prompt
        _log_image_prompt(
            job_dir,
            stage=_STAGE,
            kind="graphic",
            prompt=full_prompt,
            scene_id=scene_id,
            out_path=str(out_path),
            project_name=f"{state.job_id}-graphic-{scene_id}",
        )
        try:
            await image_fn(
                prompt=full_prompt,
                project_name=f"{state.job_id}-graphic-{scene_id}",
                out_path=str(out_path),
            )
        except Exception as exc:  # provider/transport error — non-fatal per scene
            logger.log("SCENE_GRAPHIC_FAILED", {"scene_id": scene_id, "error": str(exc)})
            pending_failures.append({
                "scene": scene,
                "scene_id": scene_id,
                "out_path": out_path,
                "prompt_hash": prompt_hash,
                "pre_attempt_mtime_ns": pre_attempt_mtime_ns,
                "failed_at_monotonic": time.monotonic(),
                "error": str(exc),
            })
            continue
        graphic = _stamp_graphic(scene, scene_id, out_path, prompt_hash)
        generated += 1
        logger.log("SCENE_GRAPHIC_GENERATED", {"scene_id": scene_id, "image_ref": graphic["image_ref"]})
        # Persist after each image (not just at loop end) so a live dashboard
        # can show cards as they land, and a hard crash mid-stage doesn't lose
        # already-generated image_refs.
        atomic_write_json(scenes_path, scene_doc)

    # ── Late-recovery sweep for failed generations (Codex 20260704-130051) ──
    # A client-side "failed" request (timeout/5xx) often still completes
    # server-side: the browser-worker downloads and writes the PNG minutes
    # later. Adopt any such file that landed (fresh mtime) instead of
    # abandoning a finished, paid-for card. Bounded wait: up to
    # _LATE_RECOVERY_WINDOW_SEC after each failure, so a failure on the LAST
    # scene still gets its chance without stalling the stage indefinitely.
    for failure in pending_failures:
        scene = failure["scene"]
        scene_id = failure["scene_id"]
        out_path: Path = failure["out_path"]

        def _landed() -> bool:
            if not out_path.exists() or out_path.stat().st_size == 0:
                return False
            if failure["pre_attempt_mtime_ns"] is None:
                return True  # file did not exist before the attempt — any valid write is ours
            return out_path.stat().st_mtime_ns > failure["pre_attempt_mtime_ns"]

        deadline = failure["failed_at_monotonic"] + _late_recovery_window_sec()
        while not _landed() and time.monotonic() < deadline:
            await asyncio.sleep(10)

        if _landed():
            graphic = _stamp_graphic(scene, scene_id, out_path, failure["prompt_hash"])
            generated += 1
            logger.log(
                "SCENE_GRAPHIC_RECOVERED_LATE",
                {"scene_id": scene_id, "image_ref": graphic["image_ref"]},
            )
            atomic_write_json(scenes_path, scene_doc)
            continue

        failed += 1
        # No recovery. If a PRE-EXISTING file is still sitting at out_path it
        # is provably from a DIFFERENT prompt (it was stale — that is why we
        # tried to regenerate) — delete it so audits never find an orphan PNG
        # that scenes.json/render_props do not reference.
        if out_path.exists() and failure["pre_attempt_mtime_ns"] is not None:
            try:
                out_path.unlink()
                logger.log("SCENE_GRAPHIC_ORPHAN_REMOVED", {"scene_id": scene_id, "path": str(out_path)})
            except OSError:
                pass
        # Stamp an explicit failure marker so downstream QA (visual_review)
        # can flag the lost card instead of the scene silently downgrading
        # to a plain video background.
        existing = scene.get("graphic") if isinstance(scene.get("graphic"), dict) else {}
        scene["graphic"] = {**existing, "needed": True, "failed": True, "error": failure["error"]}
        atomic_write_json(scenes_path, scene_doc)

    atomic_write_json(scenes_path, scene_doc)
    logger.log("GRAPHIC_IMAGES_DONE", {"generated": generated, "failed": failed})
    _complete_stage(job_dir, _STAGE, scenes_path)
    return scenes_path
