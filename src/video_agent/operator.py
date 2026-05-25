from __future__ import annotations

import json
import re
from html import escape
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from video_agent.contracts import repo_root
from video_agent.operator_validators import load_operator_channel_config, validate_operator_artifact
from video_agent.utils.json_io import read_json, read_yaml, write_json
from video_agent.storage.atomic import atomic_write_text
from video_agent.utils.validation import validate_json

ARTIFACT_SCHEMAS = {
    "script": "schemas/script.schema.json",
    "scenes": "schemas/scenes.schema.json",
    "seo": "schemas/seo.schema.json",
}
OPERATOR_ARTIFACTS = tuple(ARTIFACT_SCHEMAS.keys())


def _qa_path(job_dir: Path, artifact: str) -> Path:
    """Preferred QA artifact path (Claude)."""
    return job_dir / "operator" / "claude" / f"{artifact}_qa.json"


def _legacy_qa_path(job_dir: Path, artifact: str) -> Path:
    """Legacy QA artifact path kept for backward compatibility."""
    return job_dir / "operator" / "gemini" / f"{artifact}_qa.json"


def _resolve_existing_qa_path(job_dir: Path, artifact: str) -> Path:
    p = _qa_path(job_dir, artifact)
    if p.exists():
        return p
    return _legacy_qa_path(job_dir, artifact)


@dataclass
class PromptWriteResult:
    paths: list[Path]


@dataclass
class PromoteResult:
    artifact: str
    raw_path: Path
    output_path: Path


@dataclass
class OperatorNextResult:
    step: str
    message: str
    prompt_paths: list[Path]
    commands: list[str]


def extract_json_objects(text: str) -> list[dict[str, Any]]:
    """Extract all parseable JSON objects found in ``text``.

    Useful when the model returns commentary plus multiple JSON blocks.
    """
    objects: list[dict[str, Any]] = []
    index = 0
    while True:
        start = text.find("{", index)
        if start == -1:
            break
        
        depth = 0
        in_string = False
        escape = False
        parsed_successfully = False
        
        for idx in range(start, len(text)):
            char = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0:
                        chunk = text[start : idx + 1]
                        try:
                            parsed = json.loads(chunk)
                            if isinstance(parsed, dict):
                                objects.append(parsed)
                                index = idx + 1
                                parsed_successfully = True
                                break
                        except Exception:
                            # ChatGPT sometimes emits literal newlines inside
                            # JSON string values (invalid per JSON spec).
                            # Retry after escaping them.
                            try:
                                repaired = chunk.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")
                                parsed = json.loads(repaired)
                                if isinstance(parsed, dict):
                                    objects.append(parsed)
                                    index = idx + 1
                                    parsed_successfully = True
                                    # Log so anomalies show up in operator
                                    # output without needing a debugger.
                                    print(
                                        "[operator] extract_json_objects: repaired raw newlines in model output",
                                        flush=True,
                                    )
                                    break
                            except Exception:
                                pass
        
        if not parsed_successfully:
            index = start + 1
            
    return objects


def extract_json_object(text: str) -> dict[str, Any]:
    candidates = extract_json_objects(text)
    if not candidates:
        raise ValueError("No JSON object found in model output.")
    return candidates[0]


def _json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    return read_json(path) if path.exists() else None


def _relative_href(path: Path, base_dir: Path) -> str:
    if not path.exists():
        return ""
    return escape(path.relative_to(base_dir).as_posix(), quote=True)


def _status_badge(status: str) -> str:
    normalized = status.upper() if status else "MISSING"
    class_name = "pass" if normalized == "PASS" else "warn"
    return f'<span class="badge {class_name}">{escape(normalized)}</span>'


def _docker_cli_command(*parts: str | Path) -> str:
    rendered = " ".join(str(part) for part in parts)
    return f"docker compose run --rm video-agent python -m video_agent.cli {rendered}"


def _normalize_script_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Normalize script outputs from alternate model formats.

    Some model responses return a richer script payload (title, tts, seo)
    but omit legacy required keys (sections, cta, qa). This adapter keeps
    the current script schema stable for downstream stages.
    """
    parsed = dict(candidate)
    narration = parsed.get("narration")
    if not isinstance(narration, str):
        return parsed

    if not isinstance(parsed.get("sections"), list):
        hook = str(parsed.get("hook") or "").strip()
        first_line = next((line.strip() for line in narration.splitlines() if line.strip()), "")
        section_title = hook or first_line or "Guion"
        parsed["sections"] = [{"title": section_title, "text": narration}]

    if not isinstance(parsed.get("cta"), str) or not str(parsed.get("cta")).strip():
        parsed["cta"] = "Comparte este video y cuéntanos cuál hábito aplicarás hoy."

    qa = parsed.get("qa")
    if not isinstance(qa, dict):
        parsed["qa"] = {"verdict": "PASS"}
    elif not str(qa.get("verdict") or "").strip():
        qa = dict(qa)
        qa["verdict"] = "PASS"
        parsed["qa"] = qa

    return parsed


def _normalize_scenes_candidate(
    candidate: dict[str, Any],
    *,
    script: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize scenes outputs from alternate model formats."""
    from video_agent.retention.layout_planner import apply_retention_layouts, normalize_payload

    parsed = dict(candidate)
    data = parsed.get("data")
    if parsed.get("artifact_type") == "scenes" and isinstance(data, dict):
        parsed = {
            **data,
            "channel_id": parsed.get("channel_id") or data.get("channel_id"),
            "job_id": parsed.get("job_id") or data.get("job_id"),
        }
    scenes = parsed.get("scenes")
    if not isinstance(scenes, list):
        return parsed

    normalized_scenes: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        current = dict(scene)
        scene_id = str(
            current.get("id")
            or current.get("scene_id")
            or f"scene-{index:02d}"
        )
        duration = current.get("duration_sec")
        if not isinstance(duration, int):
            try:
                duration = int(duration)
            except Exception:
                duration = 15

        visual_prompt = str(
            current.get("visual_prompt")
            or current.get("visual_direction")
            or current.get("visual")
            or ""
        )
        caption = str(current.get("caption") or "")
        on_screen_text = str(current.get("on_screen_text") or caption)
        narration = str(current.get("narration") or "")
        motion = str(current.get("motion") or current.get("camera_notes") or "slow push-in")
        asset_refs = current.get("asset_refs")
        if not isinstance(asset_refs, dict):
            asset_refs = {}

        normalized = {
            **current,
            "id": scene_id,
            "duration_sec": duration,
            "narration": narration,
            "on_screen_text": on_screen_text,
            "caption": caption,
            "visual_prompt": visual_prompt,
            "motion": motion,
            "asset_refs": asset_refs,
            "layout": str(current.get("layout") or "subtitle").strip().lower(),
            "layout_payload": normalize_payload(current.get("layout_payload")),
            "layout_reason": str(current.get("layout_reason") or "").strip(),
            "planner_warnings": list(current.get("planner_warnings") or []),
        }
        normalized_scenes.append(normalized)

    parsed["scenes"] = apply_retention_layouts(normalized_scenes, script=script)
    if not isinstance(parsed.get("total_duration_sec"), int):
        parsed["total_duration_sec"] = sum(
            int(item.get("duration_sec", 0)) for item in normalized_scenes
        )
    qa = parsed.get("qa")
    if not isinstance(qa, dict):
        parsed["qa"] = {"verdict": "PENDING_CLAUDE_QA"}
    else:
        # Scenes QA must be produced by the dedicated QA reviewer,
        # never prefilled by the writing model.
        qa_obj = dict(qa)
        qa_obj["verdict"] = "PENDING_CLAUDE_QA"
        parsed["qa"] = qa_obj
    return parsed


# YouTube chapter timestamp lines look like "00:00 - Section title" or "01:30 - Section".
_TIMESTAMP_LINE_RE = re.compile(r"^\s*\d{1,2}:\d{2}\s+-\s+.+")


def _normalize_youtube_description(desc: str) -> str:
    """Normalize an SEO description without collapsing YouTube chapter timestamps.

    The previous behavior replaced every `\\n` inside a paragraph with a space,
    which broke chapter blocks like::

        00:00 - Intro
        01:30 - Tema
        03:00 - Cierre

    into a single unreadable line. This helper preserves timestamp lines as
    separate lines while still collapsing whitespace inside ordinary
    paragraphs and capping consecutive blank lines.
    """
    desc = (desc or "").replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = desc.split("\n")
    out: list[str] = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            if out and out[-1] != "":
                out.append("")
            continue
        # Collapse internal whitespace for both timestamp and prose lines —
        # but keep timestamp lines on their own line so YouTube can still
        # parse them as chapters.
        out.append(" ".join(stripped.split()))

    # Collapse 3+ consecutive blank lines down to a single blank line.
    cleaned: list[str] = []
    blank_count = 0
    for line in out:
        if line == "":
            blank_count += 1
            if blank_count <= 1:
                cleaned.append(line)
        else:
            blank_count = 0
            cleaned.append(line)

    return "\n".join(cleaned).strip() + "\n"


def _score_and_sort_seo_variants(seo: dict[str, Any]) -> dict[str, Any]:
    """Score title_variants, sort best-first, backfill top-level title + thumbnail_text."""
    from video_agent.seo.title_scorer import score_variants
    variants = seo.get("title_variants") or []
    if not variants:
        return seo
    scored = score_variants(variants)
    seo = {**seo, "title_variants": scored}
    seo["title"] = scored[0]["title"]
    seo["thumbnail_text"] = scored[0]["thumbnail_text"]
    return seo


# A YouTube-chapter line looks like "00:00 - Section title". The first chapter
# must start at exactly 00:00 for the player to enable chapters at all.
_TIMESTAMP_TOKEN_RE = re.compile(r"\d{1,2}:\d{2}")


def _format_mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _compute_chapter_timestamps(
    scene_doc: dict[str, Any] | None,
    script: dict[str, Any] | None,
) -> list[tuple[str, str]]:
    """Compute up to ~10 YouTube chapter (timestamp, title) pairs from real scenes.

    Strategy:
    - Walk every scene in order; track cumulative offset using ``duration_sec``.
    - Prefer chapter boundaries that align with script sections (when the
      script provides ``sections``). Otherwise pick evenly spaced boundaries
      across the scene list and use ``on_screen_text`` / first narration words
      as the chapter title.
    - Always emit a first chapter at ``00:00``. Cap at the actual total
      duration so no chapter exceeds the video length.
    """
    if not isinstance(scene_doc, dict):
        return []
    scenes = scene_doc.get("scenes") or []
    if not isinstance(scenes, list) or not scenes:
        return []

    total_sec = float(scene_doc.get("total_duration_sec") or 0)
    if total_sec <= 0:
        total_sec = sum(float(s.get("duration_sec") or 0) for s in scenes)
    if total_sec <= 0:
        return []

    section_titles: list[str] = []
    if isinstance(script, dict):
        for section in script.get("sections") or []:
            title = ""
            if isinstance(section, dict):
                title = str(section.get("title") or "").strip()
            if title:
                section_titles.append(title)

    # Target between 5 and 10 chapters depending on video length. Roughly one
    # chapter per 80 seconds of narration; the max(5,...) floor handles very
    # short videos and the min(10,...) ceiling handles long-form.
    target_count = max(5, min(10, int(total_sec // 80)))
    if section_titles:
        target_count = max(5, min(target_count, len(section_titles) + 1))

    # Build per-scene cumulative offsets.
    offsets: list[tuple[float, dict[str, Any]]] = []
    cursor = 0.0
    for scene in scenes:
        offsets.append((cursor, scene))
        cursor += float(scene.get("duration_sec") or 0)

    # Pick boundary indices evenly across the scene list.
    boundary_indices: list[int] = []
    if target_count == 1:
        boundary_indices = [0]
    else:
        step = (len(scenes) - 1) / (target_count - 1) if len(scenes) > 1 else 0
        for i in range(target_count):
            idx = int(round(i * step))
            if idx >= len(scenes):
                idx = len(scenes) - 1
            if idx not in boundary_indices:
                boundary_indices.append(idx)
    # Ensure first chapter is scene index 0.
    if 0 not in boundary_indices:
        boundary_indices.insert(0, 0)
    boundary_indices.sort()

    chapters: list[tuple[str, str]] = []
    used_titles: set[str] = set()
    for chapter_pos, scene_idx in enumerate(boundary_indices):
        offset, scene = offsets[scene_idx]
        # First chapter must be 00:00 regardless of rounding.
        ts = "00:00" if chapter_pos == 0 else _format_mmss(offset)
        title = ""
        if chapter_pos < len(section_titles):
            title = section_titles[chapter_pos]
        if not title and isinstance(scene, dict):
            title = str(scene.get("on_screen_text") or "").strip()
            if not title:
                narration = str(scene.get("narration") or "").strip()
                # Take the first 4-7 words as a fallback chapter title.
                title = " ".join(narration.split()[:6]) if narration else ""
        if not title:
            title = f"Capítulo {chapter_pos + 1}"
        # Capitalize first letter for readability.
        title = title.strip().rstrip(".:,;").strip()
        if title and title.lower() in used_titles:
            # Skip duplicate titles by tagging with chapter number.
            title = f"{title} ({chapter_pos + 1})"
        used_titles.add(title.lower())
        chapters.append((ts, title))

    return chapters


def _rewrite_description_chapters(description: str, chapters: list[tuple[str, str]]) -> str:
    """Replace the YouTube chapter block inside ``description`` with ``chapters``.

    The block is detected as a contiguous run of lines that start with
    ``MM:SS`` (possibly several timestamps glued on one line by ChatGPT).
    If no existing block is found the chapters are inserted after the first
    paragraph that does not itself contain a timestamp, so the resulting
    description still flows: hook → summary → chapters → CTA.
    """
    if not chapters:
        return description

    chapter_block = "\n".join(f"{ts} - {title}" for ts, title in chapters)

    lines = description.split("\n")
    # Find the first and last line that contain a timestamp token.
    first_ts_idx = next(
        (i for i, line in enumerate(lines) if _TIMESTAMP_TOKEN_RE.search(line)),
        None,
    )
    if first_ts_idx is None:
        # No existing chapter block — insert after the first non-empty paragraph.
        insert_at = 0
        seen_text = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped:
                seen_text = True
            elif seen_text:
                insert_at = i
                break
        else:
            insert_at = len(lines)
        prefix = lines[:insert_at]
        suffix = lines[insert_at:]
        rebuilt = prefix + ["", chapter_block, ""] + suffix
        return "\n".join(rebuilt).strip() + "\n"

    last_ts_idx = first_ts_idx
    for i in range(first_ts_idx, len(lines)):
        if _TIMESTAMP_TOKEN_RE.search(lines[i]):
            last_ts_idx = i
        elif lines[i].strip() == "":
            break
        else:
            # Stop expanding the block as soon as we hit prose without a timestamp.
            break

    rebuilt = lines[:first_ts_idx] + [chapter_block] + lines[last_ts_idx + 1 :]
    return "\n".join(rebuilt).strip() + "\n"


def _normalize_seo_candidate(
    candidate: dict[str, Any],
    *,
    channel_config: dict[str, Any] | None = None,
    scene_doc: dict[str, Any] | None = None,
    script: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Backfill SEO fields for compatibility with older model/test payloads.

    When ``scene_doc`` is provided the YouTube-chapter timestamps inside the
    description are recomputed from real scene durations so they cannot drift
    past the actual video length (a ChatGPT failure mode where it invents
    timestamps such as ``13:20`` for a 9-minute video).
    """
    parsed = dict(candidate)
    # Strip literal newlines from title
    if "title" in parsed and isinstance(parsed["title"], str):
        parsed["title"] = parsed["title"].replace("\n", " ").replace("\r", " ").strip()
        parsed["title"] = " ".join(parsed["title"].split())

    # Normalize description while preserving YouTube chapter timestamp lines,
    # then rewrite the chapter block against the real scene timeline when we
    # have scenes.json available.
    if "description" in parsed and isinstance(parsed["description"], str):
        parsed["description"] = _normalize_youtube_description(parsed["description"])
        if scene_doc:
            chapters = _compute_chapter_timestamps(scene_doc, script)
            if chapters:
                parsed["description"] = _rewrite_description_chapters(
                    parsed["description"], chapters
                )

    # Normalize title_variants
    if "title_variants" in parsed and isinstance(parsed["title_variants"], list):
        for variant in parsed["title_variants"]:
            if isinstance(variant, dict):
                if "title" in variant and isinstance(variant["title"], str):
                    variant["title"] = variant["title"].replace("\n", " ").replace("\r", " ").strip()
                    variant["title"] = " ".join(variant["title"].split())
                if "thumbnail_text" in variant and isinstance(variant["thumbnail_text"], str):
                    variant["thumbnail_text"] = variant["thumbnail_text"].replace("\n", " ").replace("\r", " ").strip()
                    variant["thumbnail_text"] = " ".join(variant["thumbnail_text"].split())

    parsed = _score_and_sort_seo_variants(parsed)

    # Backfill thumbnail_path if empty
    if not parsed.get("thumbnail_path"):
        parsed["thumbnail_path"] = "thumbnail.jpg"

    title = str(parsed.get("title") or "").strip()
    thumbnail_text = str(parsed.get("thumbnail_text") or "").strip()
    if not thumbnail_text:
        words = [w for w in title.split() if w]
        fallback = " ".join(words[:5]).upper()
        parsed["thumbnail_text"] = fallback or "DUERME MEJOR HOY"
    if "suggested_pinned_comments" not in parsed:
        parsed["suggested_pinned_comments"] = (
            "¿Qué opinas de estos consejos? Cuéntanos en los comentarios. 👇\n\n"
            "Si te gustó el video, ¡suscríbete para más contenido de bienestar! 🔔 https://www.youtube.com/channel/UCKUswqsAaLsEkcsgzTuKAmw?sub_confirmation=1"
        )
    elif isinstance(parsed["suggested_pinned_comments"], dict):
        comments = parsed["suggested_pinned_comments"]
        eb = comments.get("engagement_boosting") or comments.get("engage") or ""
        sg = comments.get("subscriber_growth") or comments.get("subscriber") or ""
        if eb and sg:
            parsed["suggested_pinned_comments"] = f"{eb}\n\n{sg}"
        elif eb:
            parsed["suggested_pinned_comments"] = eb
        elif sg:
            parsed["suggested_pinned_comments"] = sg
        else:
            parsed["suggested_pinned_comments"] = (
                "¿Qué opinas de estos consejos? Cuéntanos en los comentarios. 👇\n\n"
                "Si te gustó el video, ¡suscríbete para más contenido de bienestar! 🔔 https://www.youtube.com/channel/UCKUswqsAaLsEkcsgzTuKAmw?sub_confirmation=1"
            )
    return parsed


def _locale_guidance(channel_config: dict[str, Any]) -> dict[str, Any]:
    """Resolve locale/language/lexical preferences from channel config.

    Resolution order for language: seo.language → audience.language → es-ES (default).
    target_locale defaults to ``Spain`` when language is es-ES, else ``Latin America``.
    """
    audience = (channel_config or {}).get("audience") or {}
    seo_cfg = (channel_config or {}).get("seo") or {}
    locale_style = (channel_config or {}).get("locale_style") or {}
    language = str(seo_cfg.get("language") or audience.get("language") or "es-ES")
    default_locale = "Spain" if language == "es-ES" else "Latin America"
    target_locale = str(locale_style.get("target_locale") or default_locale)
    lexical = locale_style.get("lexical_preferences") or {}
    prefer = list(lexical.get("prefer") or [])
    avoid = list(lexical.get("avoid") or [])
    return {
        "language": language,
        "target_locale": target_locale,
        "prefer": prefer,
        "avoid": avoid,
    }


def _locale_block_lines(channel_config: dict[str, Any], *, header: str = "LOCALE AND LANGUAGE RULES (MANDATORY):") -> list[str]:
    """Return prompt lines describing locale-specific writing rules from channel config."""
    locale = _locale_guidance(channel_config)
    lines = [
        header,
        f"• Write in Spanish for {locale['target_locale']}, language code {locale['language']}.",
        f"• Use a natural {locale['target_locale']}-first tone for adults 45+.",
    ]
    if locale["prefer"]:
        lines.append("• Prefer these terms when natural: " + ", ".join(locale["prefer"]) + ".")
    if locale["avoid"]:
        lines.append("• Avoid these terms: " + ", ".join(locale["avoid"]) + ".")
    lines.append("• Never use forbidden age-positioning terms from channel_config.positioning.forbidden_phrases.")
    lines.append("• Avoid calling the audience senior, elderly, ancianos, tercera edad, abuelos, or adultos mayores.")
    return lines


def _chatgpt_script_prompt(channel_config: dict[str, Any], idea: dict[str, Any]) -> str:
    cf = channel_config.get("content_format", {})
    target_sec = cf.get("target_duration_sec", 840)
    target_min = round(target_sec / 60)
    pace_wpm = channel_config.get("tts", {}).get("pace_wpm", 145)
    total_words = round(target_sec / 60 * pace_wpm)
    return "\n".join(
        [
            "You are exporting a SCRIPT artifact as a JSON file for a YouTube channel pipeline.",
            "",
            "⚠️ OUTPUT RULES — READ CAREFULLY:",
            "• Your ENTIRE response must be ONE raw JSON object — nothing else.",
            "• Do NOT write any text before or after the JSON.",
            "• Do NOT use markdown code fences (no ```json, no ```).",
            "• Do NOT add explanations, comments, or apologies.",
            "• Imagine you are writing directly to a .json file on disk.",
            "• If your response is long, that is fine — keep going until the JSON is complete and closed with }.",
            "",
            "Required JSON schema:",
            "- channel_id, job_id, hook, sections, narration, cta, qa",
            "- sections: array of 6-10 objects, each with: title, key_points (list), narration_text",
            f"- narration: natural Spanish for a {target_min}-minute video (~{total_words} words total)",
            f"- hook: opening sentence ≤28 words. Pattern: [relatable symptom] + [implicit promise].",
            "  Example: 'Si después de los 45 te cuesta conciliar el sueño o despiertas a las 3 de la mañana, esto es exactamente para ti.'",
            "- cta: closing call-to-action sentence",
            "- qa.verdict: set to PASS when you believe the script is ready",
            "",
            "⚠️ OPENING RETENTION RULES (FIRST 30 SECONDS — HIGHEST PRIORITY):",
            "• The render skips logo intro/outro entirely. The first frame the viewer sees is your narration hook, so the first ~12 words MUST hook them.",
            "• Do NOT start with the channel name, a greeting, 'En este video', 'Hoy', 'Bienvenidos', 'Hola', or any meta-introduction. Start IN the problem.",
            "• Open with one of these 4 retention patterns, picked to fit the idea:",
            "  1. Specific pain symptom: 'Si después de los 45 te despiertas a las 3 de la mañana mirando el techo, no es solo casualidad.'",
            "  2. Contradiction / pattern interrupt: 'Cenar pronto NO siempre te ayuda a dormir. Y la mayoría de personas de más de 45 no lo sabe.'",
            "  3. Concrete number + promise: 'En los próximos 7 minutos vas a ver 3 ajustes de la tarde que cambian la noche entera.'",
            "  4. Vivid micro-scene: 'Son las 22:30. La luz baja, el cuerpo cansado, pero la cabeza sigue corriendo. Esto es lo que está pasando y cómo cortarlo.'",
            "• The hook sentence ≤ 28 words. NO subordinate filler. Punch first, explain after.",
            "• Section 1 (first ~30 s of narration, roughly the first 70 words) MUST deliver the first concrete payoff. Do not save value for later sections.",
            "• Tease — do not summarise. Hint at the surprise, the contradiction, or the 3-step plan; do NOT list every section upfront.",
            "• Avoid promising 'al final del video' anything inside Section 1. Promise something the viewer gets in the NEXT 2 minutes.",
            "",
            "HOOK AND VALUE RULES (MANDATORY):",
            "• Do NOT open with generic teaching phrases such as 'En este video aprenderás', 'Hoy vamos a hablar de', or 'Te voy a enseñar'.",
            "• Open with a specific pain after 45: a concrete symptom, frustration, or hidden daily mistake the viewer recognizes immediately.",
            "• Make the hook feel like: pain + possible misunderstanding + gentle promise. Example: 'Si después de los 45 comes \"saludable\" pero sigues sin energía, quizá el problema no es tu fuerza de voluntad, sino cómo estás armando tu plato.'",
            "• Sections must give actions the viewer can apply today, not vague wellness slogans.",
            "• For nutrition topics, prefer concrete plate guidance: 1/2 plato verduras, 1/4 proteína, 1/4 carbohidrato, una grasa saludable, cena más ligera, evita picar por ansiedad.",
            "• Do not leave advice as generic slogans like 'come más verduras', 'bebe más agua', 'duerme mejor', or 'haz ejercicio' unless each one includes a specific how-to, amount, timing, or trigger.",
            "• Use this core narrative format for the viewer experience: pain after 45 -> common misunderstanding -> simple explanation -> 3-5 practical steps -> relief close.",
            "• This is a story framework, not a topic restriction. You can apply it across sleep, nutrition, movement, menopause, stress, energy, weight, digestion, and daily habits.",
            "• Choose ONE distinct angle for this video, based on the idea. Example angles: cena ligera, despertar cansada, hambre aunque ya comiste, rodillas, ansiedad, metabolismo cambió, rutina más simple.",
            "• Across different videos, do not reuse the same pain, misunderstanding, and steps. Keep the channel consistent in experience but varied in angle.",
            "",
            "STYLE ANTI-REPETITION RULES (MANDATORY):",
            "• Do NOT reuse a repetitive tail sentence pattern across sections.",
            "• Do NOT repeat phrases like 'hazlo simple y con calma' or close variants more than once.",
            "• Each section narration_text must end differently (different verb + image + rhythm).",
            "• Keep tone warm and natural, but avoid formulaic copy-paste cadence.",
            "",
            *_locale_block_lines(channel_config),
            "",
            "Channel config:",
            _json_block(channel_config),
            "",
            "Video idea:",
            _json_block(idea),
            "",
            "⚠️ REMINDER: Output ONLY the raw JSON object. No markdown. No commentary. Start with { and end with }.",
        ]
    )


def get_scenes_qa_feedback(job_dir: Path) -> str | None:
    """Helper to extract QA issues and required changes if the verdict is NEEDS_REWORK."""
    try:
        p = _resolve_existing_qa_path(job_dir, "scenes")
        if p.exists():
            qa_data = read_json(p)
            verdict = str(qa_data.get("verdict", "")).upper()
            if verdict == "NEEDS_REWORK":
                issues = qa_data.get("issues") or []
                changes = qa_data.get("required_changes") or []
                
                feedback_lines = []
                if issues:
                    feedback_lines.append("Issues found in previous version:")
                    for issue in issues:
                        feedback_lines.append(f"- {issue}")
                if changes:
                    feedback_lines.append("Required changes for this revision:")
                    for change in changes:
                        feedback_lines.append(f"- {change}")
                
                if feedback_lines:
                    return "\n".join(feedback_lines)
    except Exception:
        pass
    return None


def _chatgpt_scenes_prompt(
    channel_config: dict[str, Any],
    script: dict[str, Any],
    qa_feedback: str | None = None,
) -> str:
    cf = channel_config.get("content_format", {})
    target_sec = cf.get("target_duration_sec", 840)
    scenes_min = cf.get("scenes_count_min", 40)
    scenes_max = cf.get("scenes_count_max", 55)
    scene_dur_target = round(target_sec / ((scenes_min + scenes_max) / 2))
    
    prompt_parts = [
        "You are exporting a SCENES artifact as a JSON file for a YouTube channel pipeline.",
        "",
        "⚠️ OUTPUT RULES — READ CAREFULLY:",
        "• Your ENTIRE response must be ONE raw JSON object — nothing else.",
        "• Do NOT write any text before or after the JSON.",
        "• Do NOT use markdown code fences (no ```json, no ```).",
        "• Do NOT add explanations, comments, or apologies.",
        "• Imagine you are writing directly to a .json file on disk.",
        f"• This JSON will be large ({scenes_min}-{scenes_max} scenes). That is fine — write the complete JSON until the final }}.",
        "",
        "Required JSON schema:",
        "- channel_id, job_id, scenes (array), total_duration_sec, qa",
        "- each scene object: id, duration_sec, narration, on_screen_text, caption, visual_prompt, motion, asset_refs, layout, layout_payload, layout_reason",
        f"- create {scenes_min}-{scenes_max} scenes; each scene duration_sec should be {scene_dur_target-3}–{scene_dur_target+3} seconds",
        f"- total_duration_sec must be approximately {target_sec} (sum of all scene durations)",
        "- scene ids: sequential scene-01, scene-02, ...",
        "- HOOK RULE: scene-01 narration must match the script hook word-for-word.",
        "  scene-01 on_screen_text: bold 3-6 word question or statement (e.g. '¿Por qué no puedes dormir?').",
        "- ⚠️ OPENING RETENTION: the render skips logo intro/outro. scene-01 is the very first frame the viewer sees. Keep scene-01 duration_sec between 8 and 12 — short enough to feel snappy but long enough to land the hook.",
        "- scenes 01-03 (first ~30 s) must deliver the first concrete payoff promised by the script hook. Do NOT use them for channel name, greetings, or 'today we will talk about'. Open IN the pain or contradiction.",
        "- scenes 01-03 visual_prompt must show the pain/situation directly (e.g. 'Mature woman lying awake in dim bedroom looking at ceiling, soft moonlight, close-up'), not a generic logo card or wide establishing shot.",
        "- asset_refs: must be an object {}, never an array",
        "- on_screen_text MUST be 2-4 words (keyword hook), and MUST NOT duplicate caption text.",
        "- caption should be natural spoken sentence(s); never copy on_screen_text verbatim.",
        "- visual_prompt: ⚠️ MANDATORY ENGLISH ONLY. NEVER Spanish. visual_prompt is fed directly to Pexels stock search, which is English-keyword based. Spanish prompts produce off-topic stock footage (e.g. 'Bellagio fountains' for a 'rutina nocturna' scene). Required style: specific (person + setting + action + lighting + camera framing). Example: 'Mature woman in her 50s arranging pillows on a calm bedroom bed at dusk, warm tungsten light, close-up shot'. ALL OTHER FIELDS may be Spanish, but visual_prompt MUST be English.",
        "- visual_prompt must match sleep-wellness context for adults 45+: bedroom night routine, evening herbal tea, low-impact stretching, doctor consultation, calm morning sunlight.",
        "- avoid off-topic visuals (cars, highways, random city traffic, tech gadgets unless explicitly in narration).",
        "- motion: 'slow_zoom' / 'pan_right' / 'pan_left'; never repeat same motion 3x in a row",
        "- layout: one of [\"hook\", \"subtitle\", \"checklist\", \"warning\", \"quote\", \"cta\"].",
        "- layout_payload: object with exactly these fields: {\"title\": string, \"body\": string, \"bullets\": array of strings, \"cta\": string}.",
        "- layout_reason: short English reason explaining why the layout fits the narration.",
        "- scene-01 should use layout=\"hook\" with a 2-8 word Spanish title when safe.",
        "- final scene should use layout=\"cta\" only if it contains a clear final action.",
        "- Use layout=\"subtitle\" for normal explanation scenes.",
        "- Use layout=\"checklist\" only when the narration contains 2-4 concrete steps/items; bullets must come from narration/caption/on_screen_text.",
        "- Use layout=\"warning\" only when the narration describes a mistake, risk, or something to avoid.",
        "- Use layout=\"quote\" only for a short emotional or memorable sentence supported by the narration.",
        "- Every non-subtitle layout must include enough layout_payload for rendering.",
        "- Python will downgrade unsafe layouts; do not invent overlay facts that are not supported by the scene text.",
        "- qa.verdict: must be PENDING_CLAUDE_QA — never mark your own scenes as PASS",
        "",
        *_locale_block_lines(channel_config, header="LOCALE RULES:"),
        "• All Spanish scene fields (narration, caption, on_screen_text, layout_payload) must use the configured language.",
        "• on_screen_text must sound natural in the configured locale and remain 2-4 words.",
        "• visual_prompt must remain English (stock search/generation works better in English).",
        "",
    ]
    
    if qa_feedback:
        prompt_parts.extend([
            "⚠️ CRITICAL REWORK FEEDBACK FROM PREVIOUS QA REVIEW:",
            "The previous version of scenes was rejected by the QA reviewer with verdict NEEDS_REWORK.",
            "You MUST revise and improve the scenes to address the following issues:",
            qa_feedback,
            "",
        ])
        
    prompt_parts.extend([
        "Channel config:",
        _json_block(channel_config),
        "",
        "Approved script:",
        _json_block(script),
        "",
        "⚠️ REMINDER: Output ONLY the raw JSON object. No markdown. No commentary. Start with { and end with }.",
    ])
    
    return "\n".join(prompt_parts)


def _chatgpt_scenes_plan_prompt(channel_config: dict[str, Any], script: dict[str, Any]) -> str:
    cf = channel_config.get("content_format", {})
    scenes_min = int(cf.get("scenes_count_min", 40))
    scenes_max = int(cf.get("scenes_count_max", 55))
    target_scene_count = round((scenes_min + scenes_max) / 2)
    target_sec = int(cf.get("target_duration_sec", 840))
    channel_id = (
        channel_config.get("channel", {}).get("id")
        or script.get("channel_id")
        or "vida-plena-45"
    )
    job_id = script.get("job_id", "")
    return "\n".join(
        [
            "You are planning sharded SCENES generation for a YouTube channel pipeline.",
            "Return exactly one JSON envelope. No markdown. No commentary.",
            "",
            "Required envelope shape:",
            "{",
            '  "artifact_type": "scenes_plan",',
            '  "schema_version": "2026-05-json-shards-v1",',
            f'  "job_id": "{job_id}",',
            f'  "channel_id": "{channel_id}",',
            '  "status": "complete",',
            '  "batch_index": null,',
            '  "batch_total": null,',
            '  "data": {',
            f'    "target_scene_count": {target_scene_count},',
            f'    "target_total_duration_sec": {target_sec},',
            '    "batch_size": 6,',
            '    "batches": [',
            '      {',
            '        "batch_index": 1,',
            '        "scene_start": "scene-01",',
            '        "scene_end": "scene-06",',
            '        "purpose": "Opening hook",',
            '        "script_sections": ["Section Title"]',
            '      }',
            '    ]',
            "  },",
            '  "warnings": []',
            "}",
            "",
            "Plan rules:",
            "- batch_size must be between 6 and 8 scenes.",
            "- scene ranges must cover the full target_scene_count.",
            "- scene IDs must be sequential: scene-01, scene-02, ...",
            "- final batch must include the final scene.",
            "- Use exactly one JSON object; no markdown fences.",
            "",
            *_locale_block_lines(channel_config, header="Locale rules:"),
            "- Spanish text fields must use the configured language for the configured locale.",
            "- Prefer Spain-native terms from channel_config.locale_style.lexical_preferences.prefer.",
            "- Avoid terms from channel_config.locale_style.lexical_preferences.avoid.",
            "",
            "Channel config:",
            _json_block(channel_config),
            "",
            "Approved script:",
            _json_block(script),
        ]
    )


def _chatgpt_scenes_batch_prompt(
    channel_config: dict[str, Any],
    script: dict[str, Any],
    plan: dict[str, Any],
    batch: dict[str, Any],
    previous_batch_summary: str | None = None,
) -> str:
    channel_id = (
        channel_config.get("channel", {}).get("id")
        or script.get("channel_id")
        or "vida-plena-45"
    )
    job_id = script.get("job_id", "")
    batch_index = int(batch.get("batch_index") or 1)
    batch_total = len((plan.get("data") or {}).get("batches") or []) or int(batch.get("batch_total") or 1)
    scene_start = batch.get("scene_start", "scene-01")
    scene_end = batch.get("scene_end", scene_start)
    parts = [
        "You are exporting one small SCENES batch for a YouTube channel pipeline.",
        "Return exactly one JSON envelope. No markdown. No commentary.",
        "",
        "Required envelope:",
        "{",
        '  "artifact_type": "scenes_batch",',
        '  "schema_version": "2026-05-json-shards-v1",',
        f'  "job_id": "{job_id}",',
        f'  "channel_id": "{channel_id}",',
        '  "status": "complete",',
        f'  "batch_index": {batch_index},',
        f'  "batch_total": {batch_total},',
        '  "data": {',
        f'    "scene_start": "{scene_start}",',
        f'    "scene_end": "{scene_end}",',
        '    "scenes": []',
        "  },",
        '  "warnings": []',
        "}",
        "",
        "Batch rules:",
        f"- Generate only scenes {scene_start} through {scene_end}.",
        "- Scene IDs must exactly match the requested range.",
        "- Every scene must include: id, duration_sec, narration, on_screen_text, caption, visual_prompt, motion, asset_refs, layout, layout_payload, layout_reason.",
        "- asset_refs must be {}.",
        "- ⚠️ visual_prompt MANDATORY ENGLISH ONLY. NEVER Spanish. Fed directly to Pexels (English keyword search). Spanish visual_prompt = rejected, you will be asked to regenerate. Example: 'Mature adult woman drinking herbal tea on a sofa at night, warm tungsten lighting, medium shot'.",
        "- narration must follow the approved script context.",
        "- layout must be one of: hook, subtitle, checklist, warning, quote, cta.",
        "- layout_payload must be an object with {title, body, bullets, cta}; use empty strings/[] for unused fields.",
        "- layout_reason must be a short English reason explaining why the layout fits the narration.",
        "- scene-01 should use layout=\"hook\" with a 2-8 word Spanish title when safe.",
        "- final scene should use layout=\"cta\" only if it contains a clear final action.",
        "- Use layout=\"subtitle\" for normal explanation scenes.",
        "- Use layout=\"checklist\" only when the narration contains 2-4 concrete steps/items; bullets must come from narration/caption/on_screen_text.",
        "- Use layout=\"warning\" only when the narration describes a mistake, risk, or something to avoid.",
        "- Use layout=\"quote\" only for a short emotional or memorable sentence supported by the narration.",
        "- Every non-subtitle layout must include enough layout_payload for rendering.",
        "- Do not invent overlay facts that are not supported by narration/caption/on_screen_text.",
        "- Do not return markdown or more than one JSON object.",
        "",
        *_locale_block_lines(channel_config, header="Locale rules:"),
        "- Spanish text fields must use the configured language for the configured locale.",
        "- Prefer terms from channel_config.locale_style.lexical_preferences.prefer.",
        "- Avoid terms from channel_config.locale_style.lexical_preferences.avoid.",
        "- visual_prompt must stay English regardless of locale.",
        "",
    ]
    if previous_batch_summary:
        parts.extend(["Previous batch summary:", previous_batch_summary, ""])
    parts.extend(
        [
            "Channel config:",
            _json_block(channel_config),
            "",
            "Approved script:",
            _json_block(script),
            "",
            "Scenes plan:",
            _json_block(plan),
            "",
            "Requested batch:",
            _json_block(batch),
        ]
    )
    return "\n".join(parts)


def _claude_scenes_qa_batch_prompt(
    channel_config: dict[str, Any],
    scenes_batch: dict[str, Any],
    batch_index: int,
    batch_total: int,
) -> str:
    channel_id = channel_config.get("channel", {}).get("id", "vida-plena-45")
    job_id = scenes_batch.get("job_id", "")
    return "\n".join(
        [
            "You are QA reviewer for one SCENES batch of a Spanish-language YouTube health channel.",
            "Return exactly one JSON envelope. No markdown. No commentary.",
            "",
            "Required envelope:",
            "{",
            '  "artifact_type": "scenes_qa_batch",',
            '  "schema_version": "2026-05-json-shards-v1",',
            f'  "job_id": "{job_id}",',
            f'  "channel_id": "{channel_id}",',
            '  "status": "complete",',
            f'  "batch_index": {batch_index},',
            f'  "batch_total": {batch_total},',
            '  "data": {',
            '    "verdict": "PASS",',
            '    "youtube_policy": {"compliant": true, "risk_level": "none", "violations": []},',
            '    "scene_checks": [],',
            '    "issues": [],',
            '    "required_changes": [],',
            '    "scores": {"schema_fit": 5, "channel_fit": 5, "safety": 5, "clarity": 5, "youtube_policy": 5}',
            "  },",
            '  "warnings": []',
            "}",
            "",
            "QA rules:",
            "- Review only this batch.",
            "- Include scene_checks for every scene in the batch.",
            "- If any scene has policy, safety, or schema issue, verdict must be NEEDS_REWORK.",
            "- youtube_policy.compliant must be false if there is any concern.",
            "",
            "Channel config:",
            _json_block(channel_config),
            "",
            "Scenes batch:",
            _json_block(scenes_batch),
        ]
    )


def _chatgpt_seo_prompt(channel_config: dict[str, Any], script: dict[str, Any], scenes: dict[str, Any]) -> str:
    locale = _locale_guidance(channel_config)
    seo_language = locale["language"]
    is_spain = seo_language == "es-ES"
    tags_line = (
        "- tags: 5-8 concise Spain-first Spanish wellness search terms"
        if is_spain
        else "- tags: 5-8 concise Spanish wellness search terms matching the configured audience locale"
    )
    return "\n".join(
        [
            "You are exporting an SEO artifact as a JSON file for a YouTube channel pipeline.",
            "",
            "⚠️ OUTPUT RULES — READ CAREFULLY:",
            "• Your ENTIRE response must be ONE raw JSON object — nothing else.",
            "• Do NOT write any text before or after the JSON.",
            "• Do NOT use markdown code fences (no ```json, no ```).",
            "• Do NOT add explanations, comments, or apologies.",
            "• Imagine you are writing directly to a .json file on disk.",
            "",
            "Required JSON schema:",
            "- job_id, title, description, tags, language, ai_disclosure, thumbnail_path, thumbnail_text, suggested_pinned_comments",
            "- title_variants: array of EXACTLY 3 objects, each: {title, thumbnail_text}",
            "  • title: clear Spanish, searchable, 6-10 words, may include numbers or questions",
            "  • thumbnail_text: 3-5 words ALL-CAPS Spanish emotional hook (e.g. 'ADIÓS AL INSOMNIO')",
            "    - RULE 'COMPLEMENTARY, NOT REPETITIVE': The thumbnail_text must be used to trigger curiosity or hit a strong emotion. Do NOT use the thumbnail to summarize the video; let the Title handle the summarization. The thumbnail text should complement the title, not duplicate or paraphrase it.",
            "    - RULE 'SAME PAIN ANGLE': title and thumbnail_text must point to the same specific pain angle. If thumbnail_text points to one pain, the title must support that same pain clearly instead of switching to a generic wellness promise.",
            "    - Example alignment: thumbnail_text='TU PLATO TE HABLA' pairs with a title like 'Cómo saber si tu plato te está quitando energía después de los 45'. Do not pair it with a generic title like 'Cómo comer mejor después de los 45'.",
            "  • Make 3 variants MEANINGFULLY DIFFERENT — vary angle, emotion, or specificity",
            "  • Do NOT repeat the same hook with minor word swaps",
            "- title: copy from the best title_variants entry",
            "- thumbnail_text: copy from the best title_variants entry",
            "- description: YouTube video description in Spanish. It MUST follow this Golden Structure (structured into 6 distinct sections/paragraphs separated by blank lines):",
            "  1. Section 1 (Hook & SEO): 2-3 short sentences. Start with the primary keyword within the first 25 characters (e.g. 'Si después de los 45...').",
            "  2. Section 2 (Detailed Summary): 2-3 short paragraphs detailing what the video covers and what the viewer will learn, incorporating secondary/LSI keywords naturally.",
            "  3. Section 3 (Timestamps): A list of timestamps for key parts/scenes in 'MM:SS - Section title' format (derive these from the approved scenes narration and durations).",
            "    - Timestamps MUST be one timestamp per line, never combined on a single line.",
            "    - Each timestamp line MUST use 'MM:SS - Section title' exactly (two-digit minutes, two-digit seconds, dash with spaces).",
            "    - IMPORTANT: Do not include any primary or external links in this section.",
            "  4. Section 4 (CTA & Subscription Link): A call-to-action asking viewers to subscribe, accompanied by the subscription link 'https://www.youtube.com/channel/UCKUswqsAaLsEkcsgzTuKAmw?sub_confirmation=1'. Do NOT mention social links unless they are explicitly provided in channel_config.upload.social_links or channel_config.channel.social_links. Never write placeholder text such as 'Redes adicionales: no proporcionadas', 'no proporcionadas', 'not provided', or 'sin enlaces'.",
            "  5. Section 5 (Channel Info, Disclaimer & AI Disclosure): A short blurb about the channel's mission (Vida Plena 45+), the medical disclaimer (e.g., 'Aviso: El contenido es de carácter informativo y no sustituye la opinión médica.'), and the AI disclosure statement (disclosing that the video uses AI voice/visual assist).",
            "  6. Section 6 (Hashtags): 3-5 relevant hashtags at the very bottom (e.g., #vidasana #bienestar45).",
            "- suggested_pinned_comments: a single suggested pinned comment in Spanish (containing warm/engaging emojis) that combines two strategies: start with an engaging question to boost audience interaction (e.g. asking for opinions or experiences), and follow with a clear call-to-action to subscribe to the channel with the exact link: https://www.youtube.com/channel/UCKUswqsAaLsEkcsgzTuKAmw?sub_confirmation=1",
            f"- language: must be {seo_language}",
            tags_line,
            "- ai_disclosure: must be true",
            "- thumbnail_path: leave as empty string ''",
            "",
            "SEO LOCALE RULES:",
            f"• Optimize title, description, tags, and pinned comment for {locale['target_locale']}-first Spanish ({seo_language}).",
            "• Prefer 'móvil' over 'celular', 'ordenador' over 'computadora', 'por la tarde' over LatAm phrasing when natural." if is_spain else "• Use vocabulary natural to the configured audience locale.",
            "• Use 'personas de más de 45 años' or 'adultos 45+'; avoid 'adultos mayores', 'tercera edad', 'ancianos'.",
            "• Do not use LatAm label text like 'Spanish/LatAm' in output.",
            "• For thumbnail_text, use 2-5 words, all caps, Spain-natural Spanish, strong but not exaggerated." if is_spain else "• For thumbnail_text, use 2-5 words, all caps, natural Spanish for the configured locale, strong but not exaggerated.",
            "• Title and thumbnail_text must share the same pain angle.",
            "• Avoid medical certainty claims. Use 'puede ayudarte', 'hábitos sencillos', 'rutina realista'.",
            "",
            "MISSING-RESOURCE RULES (MANDATORY):",
            "Never mention missing resources. If social links, website, Instagram, Facebook, or other links are not explicitly provided in channel_config, omit them entirely. Do not write placeholders like 'no proporcionadas', 'not provided', 'sin enlaces', or 'redes adicionales'.",
            "",
            "Channel config:",
            _json_block(channel_config),
            "",
            "Approved script:",
            _json_block(script),
            "",
            "Approved scenes (summary + key visuals):",
            json.dumps(
                {
                    "total_duration_sec": scenes.get("total_duration_sec"),
                    "scene_count": len(scenes.get("scenes", [])),
                    "visual_prompts_sample": [
                        str(scene.get("visual_prompt") or "")
                        for scene in (scenes.get("scenes") or [])[:5]
                    ],
                },
                ensure_ascii=False,
            ),
            "",
            "⚠️ REMINDER: Output ONLY the raw JSON object. No markdown. No commentary. Start with { and end with }.",
        ]
    )


def _claude_qa_prompt(
    artifact_name: str,
    artifact: dict[str, Any] | None,
    channel_config: dict[str, Any] | None = None,
) -> str:
    artifact_text = _json_block(artifact) if artifact is not None else "<paste ChatGPT JSON artifact here>"
    locale = _locale_guidance(channel_config or {})
    locale_qa_lines = [
        "",
        "════════════════════════════════════════",
        "LOCALE QA (mandatory when channel_config is available)",
        "════════════════════════════════════════",
        "• Check that the artifact uses the configured language from channel_config.seo.language or channel_config.audience.language.",
        f"• For this channel, expected language is {locale['language']} unless config says otherwise.",
        "• If the artifact has a language field and it is not EXACTLY the expected language, verdict MUST be NEEDS_REWORK.",
        f"• Expected target locale: {locale['target_locale']}.",
    ]
    if locale["avoid"]:
        locale_qa_lines.append(
            "• Flag locale lexical mismatches if these terms appear repeatedly when a configured-locale equivalent is expected: "
            + ", ".join(locale["avoid"])
            + "."
        )
    locale_qa_lines.append(
        "• Flag forbidden age-positioning terms from channel_config.positioning.forbidden_phrases (senior, ancianos, tercera edad, abuelos, adultos mayores, abuelitos)."
    )
    locale_qa_lines.append(
        "• Flag placeholder missing-resource text such as 'no proporcionadas', 'redes adicionales', 'not provided', or 'sin enlaces' in any SEO field."
    )
    return "\n".join(
        [
            f"You are QA reviewer for the {artifact_name.upper()} artifact of a Spanish-language YouTube health channel.",
            "",
            "⚠️ OUTPUT RULES:",
            "• Return exactly ONE raw JSON object. No markdown. No commentary.",
            "• Start with { and end with }.",
            *locale_qa_lines,
            "",
            "═══════════════════════════════════════════",
            "MANDATORY CHECK 1 — YouTube Policy & Terms",
            "═══════════════════════════════════════════",
            "YouTube's policies are ZERO-TOLERANCE here. Even the SLIGHTEST suspicion = NEEDS_REWORK.",
            "Check every piece of content against ALL of the following:",
            "",
            "• MEDICAL MISINFORMATION: Any unproven health claims, cures, treatments, or medical advice",
            "  that contradicts established scientific consensus. Example: 'X cures diabetes'.",
            "• DANGEROUS HEALTH CONTENT: Content that encourages harmful behaviour, extreme diets,",
            "  unsafe supplements, or anything that could cause physical harm.",
            "• MISLEADING / CLICKBAIT: Title, thumbnail_text, or hook promises something the content",
            "  does not fully deliver. Exaggerated outcomes ('lose 20kg in a week').",
            "• SPAM OR DECEPTIVE PRACTICES: Repetitive content, fake engagement, misleading metadata.",
            "• HATE SPEECH OR DISCRIMINATION: Any content targeting groups by age, gender, race, etc.",
            "• PRIVACY VIOLATIONS: References to real people without consent, doxxing.",
            "• COPYRIGHT: Song lyrics, verbatim quotes from copyrighted works in narration.",
            "• CHILD SAFETY: Content inappropriate for minors that could reach them.",
            "• REGULATED PRODUCTS: Supplement promotion, pharmaceutical recommendations.",
            "• SENSATIONALISM ABOUT DEATH / DISEASE: Content designed to cause fear or panic.",
            "",
            "RULE: If ANY of the above applies — even weakly or by implication — set:",
            "  youtube_policy.compliant = false",
            "  youtube_policy.risk_level = 'medium' or 'high'",
            "  verdict = NEEDS_REWORK",
            "  required_changes must explain exactly what to fix.",
            "",
            "Only set youtube_policy.compliant = true AND risk_level = 'none' when you are",
            "100% certain NO policy concern exists.",
            "",
            "════════════════════════════════════════",
            "MANDATORY CHECK 2 — Schema & Content Quality",
            "════════════════════════════════════════",
            "• Schema fit: all required fields present, correct types, no nulls where strings expected",
            f"• Channel fit: content matches {locale['language']} Spanish health channel ({locale['target_locale']}-first) for adults 45+",
            "• Safety: no specific medical diagnoses, no supplement promotion, no miracle cures",
            "• Clarity: language is natural, readable, appropriate pace",
            f"• Duration accuracy (for scenes): total_duration_sec must match sum of scene durations",
            "",
            "════════════════════════════════════════",
            "REQUIRED JSON OUTPUT SCHEMA",
            "════════════════════════════════════════",
            "{",
            '  "verdict": "PASS" | "NEEDS_REWORK",',
            '  "youtube_policy": {',
            '    "compliant": true | false,',
            '    "risk_level": "none" | "low" | "medium" | "high",',
            '    "violations": ["exact quote or description of policy concern"]',
            '  },',
            '  "scores": {',
            '    "schema_fit": 1-5,',
            '    "channel_fit": 1-5,',
            '    "safety": 1-5,',
            '    "clarity": 1-5,',
            '    "youtube_policy": 1-5',
            '  },',
            '  "issues": ["list of problems found"],',
            '  "required_changes": ["specific actionable fix for each issue"]',
            "}",
            "",
            "VERDICT RULE: verdict = PASS only when:",
            "  • youtube_policy.compliant = true AND risk_level = 'none'",
            "  • All scores ≥ 4",
            "  • issues list is empty",
            "  • required_changes list is empty",
            "",
            f"Artifact to review ({artifact_name.upper()}):",
            artifact_text,
            "",
            "⚠️ REMINDER: Output ONLY the raw JSON. No markdown. No text before or after.",
        ]
    )


def write_operator_prompts(
    channel_path: Path,
    idea_path: Path,
    job_dir: Path,
    stage: str = "all",
) -> PromptWriteResult:
    root = repo_root()
    channel_config = read_yaml(channel_path)
    idea = read_json(idea_path)
    prompt_dir = job_dir / "operator"
    chatgpt_dir = prompt_dir / "chatgpt"
    claude_dir = prompt_dir / "claude"
    chatgpt_dir.mkdir(parents=True, exist_ok=True)
    claude_dir.mkdir(parents=True, exist_ok=True)

    stages = ["script", "scenes", "seo"] if stage == "all" else [stage]
    written: list[Path] = []

    script = _read_optional_json(job_dir / "script.json")
    scenes = _read_optional_json(job_dir / "scenes.json")

    for current_stage in stages:
        if current_stage == "script":
            paths_and_text = [
                (chatgpt_dir / "script_prompt.md", _chatgpt_script_prompt(channel_config, idea)),
                (claude_dir / "script_qa_prompt.md", _claude_qa_prompt("script", script, channel_config)),
            ]
        elif current_stage == "scenes":
            if script is None:
                raise FileNotFoundError(f"{job_dir / 'script.json'} is required before writing scenes prompts.")
            qa_feedback = get_scenes_qa_feedback(job_dir)
            paths_and_text = [
                (chatgpt_dir / "scenes_prompt.md", _chatgpt_scenes_prompt(channel_config, script, qa_feedback=qa_feedback)),
                (claude_dir / "scenes_qa_prompt.md", _claude_qa_prompt("scenes", scenes, channel_config)),
            ]
        elif current_stage == "seo":
            if script is None:
                raise FileNotFoundError(f"{job_dir / 'script.json'} is required before writing SEO prompts.")
            if scenes is None:
                raise FileNotFoundError(f"{job_dir / 'scenes.json'} is required before writing SEO prompts.")
            seo = _read_optional_json(job_dir / "seo.json")
            paths_and_text = [
                (chatgpt_dir / "seo_prompt.md", _chatgpt_seo_prompt(channel_config, script, scenes)),
                (claude_dir / "seo_qa_prompt.md", _claude_qa_prompt("seo", seo, channel_config)),
            ]
        else:
            raise ValueError(f"Unsupported operator prompt stage: {current_stage}")

        for path, text in paths_and_text:
            atomic_write_text(path, text + "\n", encoding="utf-8")
            written.append(path)

    return PromptWriteResult(paths=written)


def promote_operator_artifact(
    job_dir: Path,
    artifact: str,
    raw_path: Path,
    channel_path: Path | None = None,
) -> PromoteResult:
    if artifact not in ARTIFACT_SCHEMAS:
        raise ValueError(f"Unsupported operator artifact: {artifact}")

    raw_text = raw_path.read_text(encoding="utf-8")
    candidates = extract_json_objects(raw_text)
    if not candidates:
        raise ValueError("No JSON object found in model output.")
    root = repo_root()
    schema_path = root / ARTIFACT_SCHEMAS[artifact]
    parsed: dict[str, Any] | None = None
    validation_errors: list[str] = []
    for candidate in candidates:
        if artifact == "script":
            candidate = _normalize_script_candidate(candidate)
        elif artifact == "scenes":
            candidate = _normalize_scenes_candidate(
                candidate,
                script=_read_optional_json(job_dir / "script.json"),
            )
        elif artifact == "seo":
            candidate = _normalize_seo_candidate(
                candidate,
                channel_config=load_operator_channel_config(channel_path, candidate),
                scene_doc=_read_optional_json(job_dir / "scenes.json"),
                script=_read_optional_json(job_dir / "script.json"),
            )
        try:
            validate_json(candidate, schema_path)
            parsed = candidate
            break
        except Exception as exc:
            validation_errors.append(str(exc))
    if parsed is None:
        preview = "; ".join(validation_errors[:2]) if validation_errors else "unknown schema mismatch"
        raise ValueError(
            f"No JSON object matched {artifact} schema. "
            f"Found {len(candidates)} object(s). {preview}"
        )
    channel_config = load_operator_channel_config(channel_path, parsed)
    validation = validate_operator_artifact(artifact, parsed, job_dir.name, channel_config)
    if not validation.is_valid:
        raise ValueError(f"{artifact} validation failed:\n{validation.format_report()}")
    output_path = job_dir / f"{artifact}.json"
    write_json(output_path, parsed)
    return PromoteResult(artifact=artifact, raw_path=raw_path, output_path=output_path)


def _normalize_operator_qa(artifact: str, parsed: dict[str, Any]) -> dict[str, Any]:
    verdict = str(parsed.get("verdict", "")).upper()
    if verdict != "PASS":
        raise ValueError(f"QA verdict must be PASS before promotion. Got: {verdict or '<missing>'}")

    issues = parsed.get("issues") or []
    required_changes = parsed.get("required_changes")
    if required_changes is None:
        required_changes = parsed.get("suggested_fixes") or []
    scores = parsed.get("scores") or {}

    if not isinstance(issues, list):
        raise ValueError("QA issues must be a list.")
    if not isinstance(required_changes, list):
        raise ValueError("QA required_changes must be a list.")
    if not isinstance(scores, dict):
        raise ValueError("QA scores must be an object.")

    return {
        "artifact": artifact,
        "verdict": verdict,
        "issues": issues,
        "required_changes": required_changes,
        "scores": scores,
    }


def promote_operator_qa(job_dir: Path, artifact: str, raw_path: Path) -> PromoteResult:
    if artifact not in ARTIFACT_SCHEMAS:
        raise ValueError(f"Unsupported operator artifact QA: {artifact}")

    raw_text = raw_path.read_text(encoding="utf-8")
    candidates = extract_json_objects(raw_text)
    if not candidates:
        raise ValueError("No JSON object found in QA model output.")
    qa: dict[str, Any] | None = None
    normalize_errors: list[str] = []
    for candidate in reversed(candidates):
        try:
            qa = _normalize_operator_qa(artifact, candidate)
            break
        except Exception as exc:
            normalize_errors.append(str(exc))
    if qa is None:
        preview = "; ".join(normalize_errors[:2]) if normalize_errors else "unknown QA mismatch"
        raise ValueError(
            f"No QA JSON object could be promoted for {artifact}. "
            f"Found {len(candidates)} object(s). {preview}"
        )
    output_path = _qa_path(job_dir, artifact)
    write_json(output_path, qa)
    # Keep writing legacy path so older tooling/tests remain functional.
    write_json(_legacy_qa_path(job_dir, artifact), qa)
    return PromoteResult(artifact=artifact, raw_path=raw_path, output_path=output_path)


def assert_operator_qa_passed(job_dir: Path, artifacts: list[str] | tuple[str, ...] = OPERATOR_ARTIFACTS) -> None:
    for artifact in artifacts:
        if artifact not in ARTIFACT_SCHEMAS:
            raise ValueError(f"Unsupported operator artifact QA: {artifact}")
        qa_path = _resolve_existing_qa_path(job_dir, artifact)
        if not qa_path.exists():
            raise FileNotFoundError(f"{qa_path} is required before operator render.")
        qa = read_json(qa_path)
        verdict = str(qa.get("verdict", "")).upper()
        if verdict != "PASS":
            raise ValueError(f"{qa_path} must have verdict PASS before operator render. Got: {verdict or '<missing>'}")


def build_operator_status(job_dir: Path) -> dict[str, Any]:
    artifacts: dict[str, dict[str, str]] = {}
    for artifact in OPERATOR_ARTIFACTS:
        artifact_path = job_dir / f"{artifact}.json"
        qa_path = _resolve_existing_qa_path(job_dir, artifact)
        qa_status = "missing"
        if qa_path.exists():
            qa = _read_optional_json(qa_path) or {}
            qa_status = str(qa.get("verdict", "INVALID")).upper()
        artifacts[artifact] = {
            "artifact": "present" if artifact_path.exists() else "missing",
            "qa": qa_status,
        }

    if artifacts["script"]["artifact"] == "missing":
        next_step = "Generate and promote script.json, then run Claude QA for script."
    elif artifacts["script"]["qa"] != "PASS":
        next_step = "Promote a PASS Claude QA response for script."
    elif artifacts["scenes"]["artifact"] == "missing":
        next_step = "Generate and promote scenes.json, then run Claude QA for scenes."
    elif artifacts["scenes"]["qa"] != "PASS":
        next_step = "Promote a PASS Claude QA response for scenes."
    elif artifacts["seo"]["artifact"] == "missing":
        next_step = "Generate and promote seo.json, then run Claude QA for seo."
    elif artifacts["seo"]["qa"] != "PASS":
        next_step = "Promote a PASS Claude QA response for seo."
    elif not (job_dir / "render_props.json").exists():
        next_step = "Run operator-render to prepare assets and render props."
    elif not (job_dir / "operator_review.html").exists():
        next_step = "Run operator-review or operator-render to refresh operator_review.html."
    else:
        next_step = "Ready for human review or final render."

    overall = "READY" if next_step == "Ready for human review or final render." else "IN_PROGRESS"
    return {
        "job_dir": str(job_dir),
        "overall": overall,
        "artifacts": artifacts,
        "next_step": next_step,
    }


def build_operator_next(channel_path: Path, idea_path: Path, job_dir: Path) -> OperatorNextResult:
    status = build_operator_status(job_dir)

    for artifact in OPERATOR_ARTIFACTS:
        artifact_status = status["artifacts"][artifact]
        raw_artifact_path = job_dir / "operator" / "chatgpt" / f"{artifact}.raw.txt"
        raw_qa_path = job_dir / "operator" / "claude" / f"{artifact}_qa.raw.txt"

        if artifact_status["artifact"] == "missing":
            if raw_artifact_path.exists():
                return OperatorNextResult(
                    step=f"promote-{artifact}",
                    message=f"Raw ChatGPT response exists for {artifact}; promote it into {artifact}.json.",
                    prompt_paths=[],
                    commands=[
                        _docker_cli_command(
                            "operator-promote",
                            "--job-dir",
                            job_dir,
                            "--artifact",
                            artifact,
                            "--raw-file",
                            raw_artifact_path,
                            "--channel",
                            channel_path,
                        )
                    ],
                )
            write_operator_prompts(channel_path, idea_path, job_dir, stage=artifact)
            prompt_path = job_dir / "operator" / "chatgpt" / f"{artifact}_prompt.md"
            return OperatorNextResult(
                step=f"chatgpt-{artifact}",
                message=f"Copy the {artifact} prompt into ChatGPT, then save the response as {raw_artifact_path}.",
                prompt_paths=[prompt_path],
                commands=[
                    _docker_cli_command(
                        "operator-promote",
                        "--job-dir",
                        job_dir,
                        "--artifact",
                        artifact,
                        "--raw-file",
                        raw_artifact_path,
                        "--channel",
                        channel_path,
                    )
                ],
            )

        if artifact_status["qa"] != "PASS":
            if raw_qa_path.exists():
                return OperatorNextResult(
                    step=f"promote-{artifact}-qa",
                    message=f"Raw Claude QA exists for {artifact}; promote it into {artifact}_qa.json.",
                    prompt_paths=[],
                    commands=[
                        _docker_cli_command(
                            "operator-promote-qa",
                            "--job-dir",
                            job_dir,
                            "--artifact",
                            artifact,
                            "--raw-file",
                            raw_qa_path,
                        )
                    ],
                )
            write_operator_prompts(channel_path, idea_path, job_dir, stage=artifact)
            prompt_path = job_dir / "operator" / "claude" / f"{artifact}_qa_prompt.md"
            return OperatorNextResult(
                step=f"claude-{artifact}-qa",
                message=f"Copy the {artifact} QA prompt into Claude, then save the response as {raw_qa_path}.",
                prompt_paths=[prompt_path],
                commands=[
                    _docker_cli_command(
                        "operator-promote-qa",
                        "--job-dir",
                        job_dir,
                        "--artifact",
                        artifact,
                        "--raw-file",
                        raw_qa_path,
                    )
                ],
            )

    if not (job_dir / "video.mp4").exists():
        return OperatorNextResult(
            step="render-video",
            message="All operator artifacts and Claude QA are ready; render the video.",
            prompt_paths=[],
            commands=[
                _docker_cli_command(
                    "operator-render",
                    "--channel",
                    channel_path,
                    "--job-dir",
                    job_dir,
                )
            ],
        )

    return OperatorNextResult(
        step="review-video",
        message="Video exists; open the review page and do the final human QA pass.",
        prompt_paths=[],
        commands=[
            _docker_cli_command("operator-status", "--job-dir", job_dir),
            _docker_cli_command("operator-review", "--job-dir", job_dir),
        ],
    )


def write_operator_review(job_dir: Path, output_path: Path | None = None) -> Path:
    output_path = output_path or job_dir / "operator_review.html"
    script = _read_optional_json(job_dir / "script.json") or {}
    scenes = _read_optional_json(job_dir / "scenes.json") or {}
    seo = _read_optional_json(job_dir / "seo.json") or {}
    visual_review = _read_optional_json(job_dir / "visual_review.json") or {}

    title = str(seo.get("title") or script.get("hook") or job_dir.name)
    scene_items = scenes.get("scenes") if isinstance(scenes.get("scenes"), list) else []
    qa_rows = []
    for artifact in OPERATOR_ARTIFACTS:
        qa_path = _resolve_existing_qa_path(job_dir, artifact)
        qa = _read_optional_json(qa_path) or {}
        issues = qa.get("issues") if isinstance(qa.get("issues"), list) else []
        changes = qa.get("required_changes") if isinstance(qa.get("required_changes"), list) else []
        qa_rows.append(
            "<tr>"
            f"<td>{escape(artifact)}</td>"
            f"<td>{_status_badge(str(qa.get('verdict', 'MISSING')))}</td>"
            f"<td>{len(issues)}</td>"
            f"<td>{len(changes)}</td>"
            f"<td>{escape(qa_path.relative_to(job_dir).as_posix()) if qa_path.exists() else 'missing'}</td>"
            "</tr>"
        )

    artifact_rows = []
    for filename in ["script.json", "scenes.json", "seo.json", "render_props.json", "visual_review.json", "report.md"]:
        path = job_dir / filename
        artifact_rows.append(
            "<tr>"
            f"<td>{escape(filename)}</td>"
            f"<td>{_status_badge('PASS' if path.exists() else 'MISSING')}</td>"
            f'<td>{f"""<a href="{_relative_href(path, job_dir)}">{escape(filename)}</a>""" if path.exists() else ""}</td>'
            "</tr>"
        )

    video_href = _relative_href(job_dir / "video.mp4", job_dir)
    thumbnail_href = _relative_href(job_dir / "thumbnail.jpg", job_dir)
    contact_sheet = visual_review.get("contact_sheet", "visual_contact_sheet.jpg")
    contact_href = _relative_href(job_dir / str(contact_sheet), job_dir)
    visual_status = str((visual_review.get("qa") or {}).get("status", "MISSING")) if visual_review else "MISSING"
    provider_summary = visual_review.get("summary", {}).get("by_provider", {}) if visual_review else {}
    provider_text = ", ".join(f"{count} {provider}" for provider, count in sorted(provider_summary.items())) or "n/a"

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Operator Review - {escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: #171717; background: #f6f7f9; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 28px; }}
    section {{ margin-top: 18px; padding: 18px; background: #fff; border: 1px solid #dfe3ea; border-radius: 8px; }}
    h1, h2 {{ margin: 0 0 10px; }}
    h1 {{ font-size: 28px; }}
    h2 {{ font-size: 18px; }}
    .meta {{ color: #5f6673; margin: 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }}
    img, video {{ width: 100%; max-height: 420px; object-fit: contain; background: #111; border-radius: 6px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px; border-bottom: 1px solid #e5e7eb; text-align: left; vertical-align: top; }}
    .badge {{ display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
    .pass {{ color: #14532d; background: #dcfce7; }}
    .warn {{ color: #7c2d12; background: #ffedd5; }}
    .scene {{ padding: 10px 0; border-bottom: 1px solid #e5e7eb; }}
    a {{ color: #0f5fb8; }}
  </style>
</head>
<body>
  <main>
    <h1>Operator Review</h1>
    <p class="meta">{escape(job_dir.name)} · {len(scene_items)} scenes · Visual QA {_status_badge(visual_status)}</p>

    <section>
      <h2>{escape(title)}</h2>
      <p style="white-space: pre-wrap;">{escape(str(seo.get("description", "")))}</p>
      <p class="meta">Providers: {escape(provider_text)}</p>
    </section>

    <section>
      <h2>Suggested Pinned Comment</h2>
      <p style="white-space: pre-wrap;">{escape(str(seo.get("suggested_pinned_comments", "")))}</p>
    </section>

    <section class="grid">
      <div>
        <h2>Video</h2>
        {f'<video src="{video_href}" controls></video>' if video_href else '<p class="meta">video.mp4 missing</p>'}
      </div>
      <div>
        <h2>Thumbnail</h2>
        {f'<img src="{thumbnail_href}" alt="thumbnail">' if thumbnail_href else '<p class="meta">thumbnail.jpg missing</p>'}
      </div>
      <div>
        <h2>Contact Sheet</h2>
        {f'<img src="{contact_href}" alt="visual contact sheet">' if contact_href else '<p class="meta">visual_contact_sheet.jpg missing</p>'}
      </div>
    </section>

    <section>
      <h2>Claude QA</h2>
      <table>
        <thead><tr><th>Artifact</th><th>Verdict</th><th>Issues</th><th>Required Changes</th><th>File</th></tr></thead>
        <tbody>{''.join(qa_rows)}</tbody>
      </table>
    </section>

    <section>
      <h2>Artifacts</h2>
      <table>
        <thead><tr><th>File</th><th>Status</th><th>Open</th></tr></thead>
        <tbody>{''.join(artifact_rows)}</tbody>
      </table>
    </section>

    <section>
      <h2>Scenes</h2>
      {''.join(f'<div class="scene"><strong>{escape(str(scene.get("id", "")))}</strong><p>{escape(str(scene.get("narration", "")))}</p><p class="meta">{escape(str(scene.get("visual_prompt", "")))}</p></div>' for scene in scene_items)}
    </section>
  </main>
</body>
    </html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path, html, encoding="utf-8")
    return output_path


# Backward-compatible alias for callers not yet migrated.
_gemini_qa_prompt = _claude_qa_prompt

def _chatgpt_shorts_script_prompt(channel_config: dict[str, Any], long_script: dict[str, Any]) -> str:
    from video_agent.operator import escape
    from video_agent.utils.json_io import write_json
    return f"""You are an expert YouTube Shorts creator.
I have a script for a LONG YouTube video.
Your task is to extract and generate exactly 4 short scripts (Vertical 9:16 format, under 60 seconds) based on the long video script.
Each short should focus on ONE single sub-topic or highlight.
End each short with a CTA (Call to action) pointing to the long video on the channel.

The channel is:
{channel_config.get('name', 'Unknown')}
Topics: {', '.join(channel_config.get('content_topics', []))}

The long video script is:
{json.dumps(long_script, ensure_ascii=False, indent=2)}

RETURN A VALID JSON ARRAY OF 4 OBJECTS.
Schema for each object:
{{
  "title": "Short title",
  "hook": "Strong 3-second hook text",
  "narration": "Full narration script for the short...",
  "cta": "Call to action text"
}}

Output ONLY the raw JSON array.
"""
