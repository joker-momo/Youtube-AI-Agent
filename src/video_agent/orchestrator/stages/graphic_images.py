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

from video_agent.contracts import ARTIFACT_SCENES, EVENT_LOG, repo_root
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
_PROMPT_PREFIX = (
    "Premium editorial illustration for a Spanish-language wellness video aimed at "
    "adults 45+. Calm, trustworthy, warm palette, clean modern typography. This image "
    "is shown FULL-SCREEN as-is with NO captions added afterwards, so all on-screen "
    "text must be rendered directly INTO the image. "
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


_DEFAULT_STYLE: dict[str, Any] = {
    "palette": {
        "background": "#F6F1E8",
        "primary": "#2F6B57",
        "secondary": "#D98C5F",
        "accent": "#F5C24B",
        "text": "#26332F",
    },
    "typography": {"headline": "Manrope"},
    "visual_mood": ["calm", "warm", "trustworthy", "practical"],
}


def _load_style_dna(channel_path: Path | None) -> dict[str, Any]:
    """The channel brand DNA (``style-dna.json`` beside channel.yaml) — palette,
    typography, mood. Drives the gen-image colors/font so graphics match the
    channel + Remotion video style. Falls back to the known brand default."""
    if channel_path is None:
        return _DEFAULT_STYLE
    try:
        sp = Path(channel_path).parent / "style-dna.json"
        if sp.exists():
            data = read_json(sp)
            return data if isinstance(data, dict) and data.get("palette") else _DEFAULT_STYLE
    except Exception:
        pass
    return _DEFAULT_STYLE


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


def _brand_style(style: dict[str, Any]) -> str:
    """A brand-style directive (palette hex + mood + layout) so ChatGPT renders the
    card ON-brand — warm editorial, not the generic navy/white/yellow clickbait look."""
    p = (style or {}).get("palette") or _DEFAULT_STYLE["palette"]
    bg, primary, sec = p.get("background", "#F6F1E8"), p.get("primary", "#2F6B57"), p.get("secondary", "#D98C5F")
    accent, text = p.get("accent", "#F5C24B"), p.get("text", "#26332F")
    mood = ", ".join((style or {}).get("visual_mood") or _DEFAULT_STYLE["visual_mood"])
    return (
        f"Brand style — {mood} editorial for a wellness channel for adults 45+ (NOT clickbait). "
        f"Use ONLY this palette: warm cream {bg}, deep green {primary}, terracotta {sec}, warm "
        f"yellow {accent}, dark green {text}. Set the text block on a SOFT panel/card in the brand "
        f"green {primary} (or cream {bg}) with high-contrast brand text — cream {bg} on the green, "
        f"or dark green {text} on cream — using yellow {accent} or terracotta {sec} ONLY as a small "
        "accent (one word or an underline/check-mark). Do NOT use navy, pure black, stark white "
        "blocks, neon, or a harsh full-bleed gradient. Lay it out as a calm, premium wellness-"
        "magazine card with generous padding, rounded corners and a clear text hierarchy. "
    )


_CARD_KIND = {
    "hook": "a bold, full-bleed, attention-grabbing hook title card with the headline very large",
    "checklist": "a clean checklist card",
    "quote": "an editorial quote card",
    "cta": "a call-to-action card",
    "warning": "a cautionary highlight card",
}


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

    lines: list[str] = []
    if title:
        lines.append(f'Heading: "{title}".')
    if bullets:
        items = "; ".join(f'"{b}"' for b in bullets)
        lines.append(f"Checklist items, each on its own row with a check-mark icon: {items}.")
    if body:
        lines.append(f'Supporting line: "{body}".')
    if cta:
        lines.append(f'Call-to-action button labelled: "{cta}".')

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
            "CONTRAST against the brand panel described above so it is crisp and easy to read, "
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
    style = _load_style_dna(channel_path)
    font = str((style.get("typography") or {}).get("headline") or "Manrope").strip() or "Manrope"
    brand = _brand_style(style)
    channel_name = _load_channel_name(channel_path)

    generated = 0
    failed = 0
    for scene in scene_doc.get("scenes") or []:
        if not _wants_graphic(scene):
            continue
        scene_id = str(scene.get("id") or "")
        prompt = _graphic_prompt(scene, font, brand, channel_name)
        if not scene_id or not prompt:
            failed += 1
            logger.log("SCENE_GRAPHIC_SKIPPED", {"scene_id": scene_id, "reason": "no_prompt"})
            continue
        out_path = assets_dir / f"graphic-{scene_id}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Idempotent / resumable: if a prior run already produced this image,
        # record the ref and skip the (slow, paid) regeneration. Lets the stage
        # resume after an interruption without re-generating every graphic.
        if out_path.exists() and out_path.stat().st_size > 0:
            graphic = scene.get("graphic") if isinstance(scene.get("graphic"), dict) else {}
            graphic["needed"] = True
            graphic["image_ref"] = _publish_graphic(state.job_id, out_path)
            scene["graphic"] = graphic
            generated += 1
            logger.log(
                "SCENE_GRAPHIC_GENERATED",
                {"scene_id": scene_id, "image_ref": graphic["image_ref"], "reused": True},
            )
            continue
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
        graphic["image_ref"] = _publish_graphic(state.job_id, out_path)
        scene["graphic"] = graphic
        generated += 1
        logger.log("SCENE_GRAPHIC_GENERATED", {"scene_id": scene_id, "image_ref": graphic["image_ref"]})

    atomic_write_json(scenes_path, scene_doc)
    logger.log("GRAPHIC_IMAGES_DONE", {"generated": generated, "failed": failed})
    _complete_stage(job_dir, _STAGE, scenes_path)
    return scenes_path
