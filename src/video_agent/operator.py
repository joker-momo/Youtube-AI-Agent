from __future__ import annotations

import fcntl
import json
import math
import re
import unicodedata
from html import escape
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from video_agent.contracts import repo_root
from video_agent.operator_validators import load_operator_channel_config, validate_operator_artifact
from video_agent.style_dna import is_valid_hex, load_style_dna
from video_agent.utils.json_io import read_json, read_yaml, write_json
from video_agent.storage.atomic import atomic_write_json, atomic_write_text
from video_agent.utils.validation import validate_json

ARTIFACT_SCHEMAS = {
    "script": "schemas/script.schema.json",
    "scenes": "schemas/scenes.schema.json",
    "seo": "schemas/seo.schema.json",
}
OPERATOR_ARTIFACTS = tuple(ARTIFACT_SCHEMAS.keys())


def _qa_path(job_dir: Path, artifact: str) -> Path:
    """Preferred QA artifact path (Gemini)."""
    return job_dir / "operator" / "gemini" / f"{artifact}_qa.json"


def _resolve_existing_qa_path(job_dir: Path, artifact: str) -> Path:
    return _qa_path(job_dir, artifact)


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


from video_agent.operator_json import (
    _JSON_CTRL_ESCAPES,
    _escape_control_chars_in_strings,
    _repair_truncated_json_object,
    extract_json_objects,
    extract_json_object,
    _json_block,
    _json_file_directive,
)


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if path.exists():
        return read_json(path)
    if path.name.endswith(".json"):
        job_dir = path.parent
        if (job_dir / "job.json").exists() or job_dir.name.startswith("job-"):
            fallback_path = job_dir / "json" / path.name
            if fallback_path.exists():
                return read_json(fallback_path)
    return None


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
    return f"python -m video_agent.cli {rendered}"


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
        parsed["qa"] = {"verdict": "PENDING_GEMINI_QA"}
    else:
        # Scenes QA must be produced by the dedicated QA reviewer,
        # never prefilled by the writing model.
        qa_obj = dict(qa)
        qa_obj["verdict"] = "PENDING_GEMINI_QA"
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


def _canonicalize_channel_name_whitespace(text: str, channel_config: dict[str, Any] | None) -> str:
    channel = (channel_config or {}).get("channel") or {}
    channel_name = str(channel.get("name") or "").strip()
    # Configs may use an official 'Name: Tagline' form while descriptions refer
    # to the channel by the display prefix alone ('Vida Plena 45+'). Repair
    # wraps of both forms; full name first so a wrapped full name is not
    # half-fixed by the shorter prefix pattern.
    candidates = [channel_name]
    display_prefix = channel_name.split(":", 1)[0].strip()
    if display_prefix and display_prefix != channel_name:
        candidates.append(display_prefix)
    for name in candidates:
        parts = name.split()
        if len(parts) < 2:
            continue
        pattern = r"\s+".join(re.escape(part) for part in parts)
        text = re.sub(pattern, name.replace("\\", r"\\"), text, flags=re.IGNORECASE)
    return text


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

# Spanish function words that carry no topical signal for section↔scene
# matching (already accent-stripped, ≥4 chars — shorter words are dropped by
# the tokenizer anyway).
_SECTION_MATCH_STOPWORDS = frozenset({
    "para", "pero", "como", "esta", "este", "esto", "estos", "estas",
    "todo", "toda", "todos", "todas", "cada", "unos", "unas",
    "otro", "otra", "otros", "otras", "ellos", "ellas", "usted",
    "sobre", "entre", "cuando", "donde", "tambien", "porque",
    "desde", "hasta", "tiene", "tienen", "hacer", "hace",
    "puede", "pueden", "algo", "alguien", "antes", "despues",
    "luego", "ahora", "siempre", "nunca", "durante", "mientras",
    "aunque", "sino", "segun", "misma", "mismo", "muy",
})

# Minimum share of sections (beyond the first) that must find a positive
# narration match before anchored boundaries replace even spacing.
_SECTION_ANCHOR_MIN_MATCH_RATIO = 0.6

# YouTube ignores chapter blocks containing chapters shorter than 10 seconds.
_MIN_CHAPTER_GAP_SEC = 10.0


def _format_mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _section_match_tokens(text: str) -> set[str]:
    """Accent-stripped content-word tokens, crudely stemmed to 6 chars."""
    normalized = unicodedata.normalize("NFKD", text.lower())
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    words = re.findall(r"[a-z]+", normalized)
    return {w[:6] for w in words if len(w) >= 4 and w not in _SECTION_MATCH_STOPWORDS}


def _scene_texts(scenes: list[dict[str, Any]]) -> list[str]:
    return [
        " ".join(str(s.get(k) or "") for k in ("narration", "on_screen_text", "caption"))
        for s in scenes
    ]


def _scene_offsets(scenes: list[dict[str, Any]]) -> list[float]:
    """Per-scene start offsets. Prefer whisper-aligned ``audio_offset_sec``
    (ground truth for what the viewer hears) over summing planned
    ``duration_sec`` (drifts from real audio; bug-529)."""
    have_audio = sum(
        1 for s in scenes if isinstance(s.get("audio_offset_sec"), (int, float))
    )
    if have_audio >= max(2, int(len(scenes) * 0.8)):
        offsets: list[float] = []
        last = 0.0
        for s in scenes:
            raw = s.get("audio_offset_sec")
            value = float(raw) if isinstance(raw, (int, float)) else last
            value = max(value, last)  # monotonic guard against bad rows
            offsets.append(value)
            last = value
        return offsets
    offsets = []
    cursor = 0.0
    for s in scenes:
        offsets.append(cursor)
        cursor += float(s.get("duration_sec") or 0)
    return offsets


def _norm_section_title(title: str) -> str:
    return " ".join(sorted(_section_match_tokens(str(title))))


def _anchors_from_scene_metadata(
    sections: list[dict[str, Any]], scenes: list[dict[str, Any]]
) -> list[int] | None:
    """Exact anchors when scenes carry explicit section attribution.

    The long-form scene generator now tags every scene with the script section
    it narrates (``scene["section"]``); this is the PRIMARY, deterministic
    path — lexical matching below is only the legacy fallback (bug-529)."""
    titles = [_norm_section_title(str(sec.get("title") or "")) for sec in sections]
    first_scene: dict[str, int] = {}
    tagged = 0
    for idx, scene in enumerate(scenes):
        raw = str(scene.get("section") or "").strip()
        if not raw:
            continue
        tagged += 1
        key = _norm_section_title(raw)
        first_scene.setdefault(key, idx)
    if tagged < max(2, int(len(scenes) * 0.5)):
        return None
    anchors: list[int] = []
    last = 0
    for key in titles[1:]:
        idx = first_scene.get(key)
        if idx is None or idx <= last:
            return None  # incomplete/unordered attribution — fall back
        anchors.append(idx)
        last = idx
    return anchors


def _anchor_sections_via_plan(
    sections: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    scenes_plan: dict[str, Any] | None,
    offsets: list[float],
) -> list[int] | None:
    """Constrain each section's anchor to its planned batch scene-range, then
    pick the best boundary inside that range by windowed IDF-weighted vocabulary
    affinity (legacy jobs whose scenes lack section attribution)."""
    if not isinstance(scenes_plan, dict):
        return None
    batches = (scenes_plan.get("data") or scenes_plan).get("batches") if isinstance(
        (scenes_plan.get("data") or scenes_plan), dict
    ) else None
    if not isinstance(batches, list) or not batches:
        return None
    sid_to_idx = {str(s.get("id")): i for i, s in enumerate(scenes)}

    texts = _scene_texts(scenes)
    scene_toks = [_section_match_tokens(t) for t in texts]
    doc_freq: dict[str, int] = {}
    for ts in scene_toks:
        for t in ts:
            doc_freq[t] = doc_freq.get(t, 0) + 1
    common = {t for t, c in doc_freq.items() if c > max(2, len(scenes) * 0.15)}

    def weights(k: int) -> dict[str, float]:
        w: dict[str, float] = {}
        for t in _section_match_tokens(str(sections[k].get("focus") or "")):
            if t not in common:
                w[t] = 1.0 / (1 + doc_freq.get(t, 0))
        for t in _section_match_tokens(str(sections[k].get("title") or "")):
            if t not in common:
                w[t] = 2.0 / (1 + doc_freq.get(t, 0))
        return w

    def batch_range(k: int) -> tuple[int, int] | None:
        key = _norm_section_title(str(sections[k].get("title") or ""))
        for b in batches:
            names = [
                _norm_section_title(str(x)) for x in (b.get("script_sections") or [])
            ]
            if key in names:
                lo = sid_to_idx.get(str(b.get("scene_start")))
                hi = sid_to_idx.get(str(b.get("scene_end")))
                if lo is None or hi is None:
                    return None
                return lo, min(hi + 2, len(scenes) - 1)
        return None

    anchors: list[int] = []
    prev_anchor = 0
    for k in range(1, len(sections)):
        rng = batch_range(k)
        if rng is None:
            return None
        lo, hi = max(rng[0], prev_anchor + 1), rng[1]
        if lo > hi:
            lo = hi = min(prev_anchor + 1, len(scenes) - 1)
        w_cur = weights(k)
        w_prev = weights(k - 1)
        # transition score: this section's vocabulary from i onward, the
        # previous section's vocabulary strictly before i (window of 3).
        best_score, best_i = float("-inf"), lo
        for i in range(lo, hi + 1):
            cur_gain = sum(
                sum(v for t, v in w_cur.items() if t in scene_toks[j]) * (0.75 ** (j - i))
                for j in range(i, min(i + 3, len(scenes)))
            )
            prev_gain = sum(
                sum(v for t, v in w_prev.items() if t in scene_toks[j])
                for j in range(max(lo - 2, 0), i)
            )
            score = cur_gain + prev_gain
            if score > best_score:
                best_score, best_i = score, i
        anchors.append(best_i)
        prev_anchor = best_i
    return anchors


def _anchor_sections_to_scenes(
    sections: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
) -> list[int] | None:
    """Global fallback: map each section (after the first) to the scene where it
    starts by keyword overlap (title tokens weigh double), maximum-score strictly
    increasing assignment via dynamic programming. Returns anchor scene indices
    for sections[1:], or None when too few sections match to trust the
    alignment (callers then fall back to evenly spaced boundaries)."""
    if len(sections) < 2 or len(scenes) < 2:
        return None

    section_tokens: list[dict[str, float]] = []
    for section in sections[1:]:
        title_tokens = _section_match_tokens(str(section.get("title") or ""))
        focus_tokens = _section_match_tokens(str(section.get("focus") or ""))
        weights = {t: 1.0 for t in focus_tokens}
        for t in title_tokens:
            weights[t] = 2.0
        section_tokens.append(weights)

    scene_tokens = [_section_match_tokens(t) for t in _scene_texts(scenes)]

    n, m = len(section_tokens), len(scenes)
    scores = [
        [sum(w for t, w in tokens.items() if t in scene_tokens[i]) for i in range(m)]
        for tokens in section_tokens
    ]

    neg = float("-inf")
    best = [[neg] * m for _ in range(n)]
    prev = [[-1] * m for _ in range(n)]
    for i in range(1, m - (n - 1)):
        best[0][i] = scores[0][i]
    for k in range(1, n):
        running_best, running_idx = neg, -1
        for i in range(k + 1, m - (n - 1 - k)):
            if best[k - 1][i - 1] > running_best:
                running_best, running_idx = best[k - 1][i - 1], i - 1
            if running_idx >= 0:
                best[k][i] = running_best + scores[k][i]
                prev[k][i] = running_idx

    end = max(range(n, m), key=lambda i: best[n - 1][i], default=-1)
    if end < 0 or best[n - 1][end] == neg:
        return None
    anchors = [0] * n
    k, i = n - 1, end
    while k >= 0:
        anchors[k] = i
        i = prev[k][i]
        k -= 1
    matched = sum(1 for k in range(n) if scores[k][anchors[k]] > 0)
    if matched * 2 < n:
        return None
    return anchors


def _compute_chapter_timestamps(
    scene_doc: dict[str, Any] | None,
    script: dict[str, Any] | None,
    scenes_plan: dict[str, Any] | None = None,
    chapter_overrides: dict[str, Any] | None = None,
) -> list[tuple[str, str]]:
    """Compute YouTube chapter (timestamp, title) pairs from real scenes.

    Resolution order (bug-529):
    1. ``chapter_overrides`` — an audited per-job correction artifact
       (``json/chapter_overrides.json``); validated, never invented.
    2. Scene-level section attribution (``scene["section"]``) — exact and
       deterministic; the scene generator persists it going forward.
    3. Planned batch ranges (``scenes_plan``) + windowed IDF vocabulary
       affinity — legacy jobs.
    4. Global keyword DP, then even spacing — sparse metadata.

    Invariants: first chapter 00:00; strictly increasing; >=10s apart; never
    beyond the real duration; ALL valid sections are kept (no arbitrary cap —
    YouTube has no 10-chapter limit)."""
    if not isinstance(scene_doc, dict):
        return []
    scenes = scene_doc.get("scenes") or []
    if not isinstance(scenes, list) or not scenes:
        return []

    offsets = _scene_offsets(scenes)
    total_sec = float(scene_doc.get("total_duration_sec") or 0)
    tail = offsets[-1] + float(scenes[-1].get("duration_sec") or 0)
    total_sec = max(total_sec, tail)
    if total_sec <= 0:
        return []

    def _valid_chapter_list(raw: Any) -> list[tuple[str, str]] | None:
        if not isinstance(raw, list) or len(raw) < 2:
            return None
        out: list[tuple[str, str]] = []
        last = -1.0
        for row in raw:
            if isinstance(row, dict):
                ts, title = str(row.get("timestamp") or ""), str(row.get("title") or "")
            elif isinstance(row, (list, tuple)) and len(row) == 2:
                ts, title = str(row[0]), str(row[1])
            else:
                return None
            if not re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", ts) or not title.strip():
                return None
            parts = [int(x) for x in ts.split(":")]
            sec = parts[-1] + parts[-2] * 60 + (parts[-3] * 3600 if len(parts) == 3 else 0)
            if sec <= last or (out and sec - last < _MIN_CHAPTER_GAP_SEC) or sec >= total_sec:
                return None
            if not out and sec != 0:
                return None
            out.append((ts, title.strip()))
            last = sec
        return out

    if isinstance(chapter_overrides, dict):
        validated = _valid_chapter_list(chapter_overrides.get("chapters"))
        if validated:
            return validated

    sections: list[dict[str, Any]] = []
    if isinstance(script, dict):
        for section in script.get("sections") or []:
            if isinstance(section, dict) and str(section.get("title") or "").strip():
                sections.append(section)
    section_titles = [str(s.get("title")).strip() for s in sections]

    anchors: list[int] | None = None
    if len(sections) >= 2:
        anchors = _anchors_from_scene_metadata(sections, scenes)
        if anchors is None:
            anchors = _anchor_sections_via_plan(sections, scenes, scenes_plan, offsets)
        if anchors is None:
            anchors = _anchor_sections_to_scenes(sections, scenes)

    if anchors is not None:
        anchored: list[tuple[str, str]] = [("00:00", section_titles[0])]
        last_offset = 0.0
        for title, scene_idx in zip(section_titles[1:], anchors, strict=True):
            offset = offsets[scene_idx]
            # Keep EVERY section whose anchor is valid; drop only chapters that
            # violate YouTube's hard rules (>=10s apart, inside the video).
            if offset - last_offset < _MIN_CHAPTER_GAP_SEC or offset >= total_sec:
                continue
            anchored.append((_format_mmss(offset), title))
            last_offset = offset
        if len(anchored) >= 2:
            return anchored

    # Even-spacing fallback (sparse metadata). Target one chapter per ~80s.
    target_count = max(5, min(10, int(total_sec // 80)))
    if section_titles:
        target_count = max(5, min(target_count, len(section_titles) + 1))

    boundary_indices: list[int] = []
    if target_count == 1:
        boundary_indices = [0]
    else:
        step = (len(scenes) - 1) / (target_count - 1) if len(scenes) > 1 else 0
        for i in range(target_count):
            idx = min(int(round(i * step)), len(scenes) - 1)
            if idx not in boundary_indices:
                boundary_indices.append(idx)
    if 0 not in boundary_indices:
        boundary_indices.insert(0, 0)
    boundary_indices.sort()

    chapters: list[tuple[str, str]] = []
    used_titles: set[str] = set()
    last_offset = -_MIN_CHAPTER_GAP_SEC
    for chapter_pos, scene_idx in enumerate(boundary_indices):
        offset = offsets[scene_idx]
        if chapter_pos > 0 and (offset - last_offset < _MIN_CHAPTER_GAP_SEC or offset >= total_sec):
            continue
        ts = "00:00" if chapter_pos == 0 else _format_mmss(offset)
        title = ""
        if chapter_pos < len(section_titles):
            title = section_titles[chapter_pos]
        scene = scenes[scene_idx]
        if not title and isinstance(scene, dict):
            title = str(scene.get("on_screen_text") or "").strip()
            if not title:
                narration = str(scene.get("narration") or "").strip()
                title = " ".join(narration.split()[:6]) if narration else ""
        if not title:
            title = f"Capítulo {chapter_pos + 1}"
        title = title.strip().rstrip(".:,;").strip()
        if title and title.lower() in used_titles:
            title = f"{title} ({chapter_pos + 1})"
        used_titles.add(title.lower())
        chapters.append((ts, title))
        last_offset = offset

    return chapters


def resync_seo_chapters(
    job_dir: Path,
    scene_doc: dict[str, Any] | None = None,
    script: dict[str, Any] | None = None,
) -> list[tuple[str, str]] | None:
    """Recompute the YouTube chapter block in seo.json from the CURRENT scene
    timeline and persist it via a race-safe single-field update.

    bug-531: the whisper-stage resync ran while scenes.json still carried the
    original PLANNED durations (sum ~24.6min) — the audio-fit pass inside the
    render stage rewrites the timeline (real ~19.8min) afterwards, so the early
    resync shipped chapters past the video end. This helper is called again
    from the render path with the FINAL merged scene_doc; description is
    written through update_seo_fields so concurrent writers (thumbnail stage)
    are never clobbered. Returns the chapters written, or None when inputs are
    missing/unchanged."""
    job_dir = Path(job_dir)
    seo_path = job_dir / "json" / "seo.json"
    if not seo_path.exists():
        return None
    if scene_doc is None:
        scenes_path = job_dir / "json" / "scenes.json"
        if not scenes_path.exists():
            return None
        scene_doc = json.loads(scenes_path.read_text(encoding="utf-8"))
    if script is None:
        script_path = job_dir / "json" / "script.json"
        script = (
            json.loads(script_path.read_text(encoding="utf-8"))
            if script_path.exists()
            else None
        )
    scenes_plan = _read_optional_json(
        job_dir / "operator" / "chatgpt" / "scenes_plan.json"
    )
    overrides = _read_optional_json(job_dir / "json" / "chapter_overrides.json")
    chapters = _compute_chapter_timestamps(
        scene_doc, script, scenes_plan=scenes_plan, chapter_overrides=overrides
    )
    if not chapters:
        return None
    seo_payload = json.loads(seo_path.read_text(encoding="utf-8"))
    new_description = _rewrite_description_chapters(
        str(seo_payload.get("description") or ""), chapters
    )
    if new_description == seo_payload.get("description"):
        return chapters
    update_seo_fields(seo_path, {"description": new_description})
    return chapters


def update_seo_fields(seo_path: Path, updates: dict[str, Any]) -> dict[str, Any]:
    """Serialized, single-field-safe update of ``seo.json``.

    Concurrent writers (thumbnail stage attaching ``thumbnail_path`` while a
    chapter migration rewrites ``description``) each held a full stale snapshot
    and clobbered the other's field on write (bug-528/bug-529). This helper
    takes an exclusive ``flock`` on a sidecar lock file, re-reads the CURRENT
    artifact inside the lock, applies ONLY the given fields, and writes
    atomically — a writer can never resurrect stale values of fields it does
    not own."""
    seo_path = Path(seo_path)
    lock_path = seo_path.with_suffix(seo_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        try:
            current: dict[str, Any] = {}
            if seo_path.exists():
                loaded = json.loads(seo_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    current = loaded
            current.update(updates)
            atomic_write_json(seo_path, current)
            return current
        finally:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)


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
    brand_palette: dict[str, Any] | None = None,
    scenes_plan: dict[str, Any] | None = None,
    chapter_overrides: dict[str, Any] | None = None,
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
        parsed["description"] = _canonicalize_channel_name_whitespace(
            parsed["description"], channel_config
        )
        parsed["description"] = _normalize_youtube_description(parsed["description"])
        parsed["description"] = _canonicalize_channel_name_whitespace(
            parsed["description"], channel_config
        )
        if scene_doc:
            chapters = _compute_chapter_timestamps(
                scene_doc, script, scenes_plan=scenes_plan, chapter_overrides=chapter_overrides
            )
            if chapters:
                parsed["description"] = _rewrite_description_chapters(
                    parsed["description"], chapters
                )
                parsed["description"] = _canonicalize_channel_name_whitespace(
                    parsed["description"], channel_config
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

    # ChatGPT is asked to pick a per-video topic_accent_color harmonized with the
    # brand palette (see _chatgpt_seo_prompt); if it's missing/malformed, fall back
    # to the channel's brand-default accent rather than silently rendering with no
    # accent or an invalid hex.
    if not is_valid_hex(parsed.get("topic_accent_color")):
        brand_accent = ((brand_palette or {}).get("palette") or {}).get("accent")
        if is_valid_hex(brand_accent):
            parsed["topic_accent_color"] = brand_accent

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
    if isinstance(parsed.get("suggested_pinned_comments"), str):
        parsed["suggested_pinned_comments"] = _canonicalize_channel_name_whitespace(
            parsed["suggested_pinned_comments"], channel_config
        )
    return parsed


from video_agent.operator_prompts import (
    _locale_guidance,
    _locale_block_lines,
    _chatgpt_script_prompt,
    get_scenes_qa_feedback,
    _SCENE_RHYTHM_RULES,
    _chatgpt_scenes_prompt,
    _chatgpt_scenes_plan_prompt,
    _chatgpt_scenes_batch_prompt,
    _gemini_scenes_qa_batch_prompt,
    _chatgpt_seo_prompt,
    _gemini_qa_prompt,
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
    gemini_dir = prompt_dir / "gemini"
    chatgpt_dir.mkdir(parents=True, exist_ok=True)
    gemini_dir.mkdir(parents=True, exist_ok=True)

    stages = ["script", "scenes", "seo"] if stage == "all" else [stage]
    written: list[Path] = []

    script = _read_optional_json(_resolve_operator_path(job_dir, "script.json"))
    scenes = _read_optional_json(_resolve_operator_path(job_dir, "scenes.json"))

    for current_stage in stages:
        if current_stage == "script":
            paths_and_text = [
                (chatgpt_dir / "script_prompt.md", _chatgpt_script_prompt(channel_config, idea)),
                (gemini_dir / "script_qa_prompt.md", _gemini_qa_prompt("script", script, channel_config)),
            ]
        elif current_stage == "scenes":
            if script is None:
                raise FileNotFoundError(f"{_resolve_operator_path(job_dir, 'script.json')} is required before writing scenes prompts.")
            qa_feedback = get_scenes_qa_feedback(job_dir)
            paths_and_text = [
                (chatgpt_dir / "scenes_prompt.md", _chatgpt_scenes_prompt(channel_config, script, qa_feedback=qa_feedback)),
                (gemini_dir / "scenes_qa_prompt.md", _gemini_qa_prompt("scenes", scenes, channel_config)),
            ]
        elif current_stage == "seo":
            if script is None:
                raise FileNotFoundError(f"{_resolve_operator_path(job_dir, 'script.json')} is required before writing SEO prompts.")
            if scenes is None:
                raise FileNotFoundError(f"{_resolve_operator_path(job_dir, 'scenes.json')} is required before writing SEO prompts.")
            seo = _read_optional_json(_resolve_operator_path(job_dir, "seo.json"))
            brand_palette = load_style_dna(channel_path)
            paths_and_text = [
                (chatgpt_dir / "seo_prompt.md", _chatgpt_seo_prompt(channel_config, script, scenes, brand_palette)),
                (gemini_dir / "seo_qa_prompt.md", _gemini_qa_prompt("seo", seo, channel_config)),
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
                script=_read_optional_json(_resolve_operator_path(job_dir, "script.json")),
            )
        elif artifact == "seo":
            candidate = _normalize_seo_candidate(
                candidate,
                channel_config=load_operator_channel_config(channel_path, candidate),
                scene_doc=_read_optional_json(_resolve_operator_path(job_dir, "scenes.json")),
                script=_read_optional_json(_resolve_operator_path(job_dir, "script.json")),
                brand_palette=load_style_dna(channel_path),
                scenes_plan=_read_optional_json(
                    Path(job_dir) / "operator" / "chatgpt" / "scenes_plan.json"
                ),
                chapter_overrides=_read_optional_json(
                    Path(job_dir) / "json" / "chapter_overrides.json"
                ),
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
    if (job_dir / "json").exists():
        output_path = job_dir / "json" / f"{artifact}.json"
    else:
        output_path = job_dir / f"{artifact}.json"
    write_json(output_path, parsed)
    return PromoteResult(artifact=artifact, raw_path=raw_path, output_path=output_path)


def _normalize_operator_qa(artifact: str, parsed: dict[str, Any]) -> dict[str, Any]:
    # Gemini sometimes echoes the FULL artifact (which natively carries a "qa"
    # field) instead of a bare {verdict, issues, ...} object, nesting the verdict
    # under "qa". Unwrap that so the verdict is found either way (otherwise the
    # top-level lookup misses it -> MISSING -> endless QA rework -> pipeline stall).
    if "verdict" not in parsed and isinstance(parsed.get("qa"), dict):
        parsed = parsed["qa"]
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
        artifact_path = _resolve_operator_path(job_dir, f"{artifact}.json")
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
        next_step = "Generate and promote script.json, then run Gemini QA for script."
    elif artifacts["script"]["qa"] != "PASS":
        next_step = "Promote a PASS Gemini QA response for script."
    elif artifacts["scenes"]["artifact"] == "missing":
        next_step = "Generate and promote scenes.json, then run Gemini QA for scenes."
    elif artifacts["scenes"]["qa"] != "PASS":
        next_step = "Promote a PASS Gemini QA response for scenes."
    elif artifacts["seo"]["artifact"] == "missing":
        next_step = "Generate and promote seo.json, then run Gemini QA for seo."
    elif artifacts["seo"]["qa"] != "PASS":
        next_step = "Promote a PASS Gemini QA response for seo."
    elif not _resolve_operator_path(job_dir, "render_props.json").exists():
        next_step = "Run operator-render to prepare assets and render props."
    elif not _resolve_operator_path(job_dir, "operator_review.html").exists():
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
        raw_qa_path = job_dir / "operator" / "gemini" / f"{artifact}_qa.raw.txt"

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
                    message=f"Raw Gemini QA exists for {artifact}; promote it into {artifact}_qa.json.",
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
            prompt_path = job_dir / "operator" / "gemini" / f"{artifact}_qa_prompt.md"
            return OperatorNextResult(
                step=f"gemini-{artifact}-qa",
                message=f"Copy the {artifact} QA prompt into Gemini, then save the response as {raw_qa_path}.",
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
            message="All operator artifacts and Gemini QA are ready; render the video.",
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


def _resolve_operator_path(job_dir: Path, filename: str) -> Path:
    """Resolve a file path with fallback to root or json/outputs based on layout."""
    if filename.endswith(".json") or filename.endswith(".jsonl"):
        new_path = job_dir / "json" / filename
    elif filename == "operator_review.html" or filename == "report.md" or filename.endswith(".mp4") or filename.endswith(".jpg"):
        new_path = job_dir / "outputs" / filename
    else:
        new_path = job_dir / filename
    
    if new_path.exists():
        return new_path
    
    legacy_path = job_dir / filename
    if legacy_path.exists():
        return legacy_path
        
    return new_path


def write_operator_review(job_dir: Path, output_path: Path | None = None) -> Path:
    if output_path is None:
        if (job_dir / "outputs").exists():
            output_path = job_dir / "outputs" / "operator_review.html"
        else:
            output_path = job_dir / "operator_review.html"

    script = _read_optional_json(_resolve_operator_path(job_dir, "script.json")) or {}
    scenes = _read_optional_json(_resolve_operator_path(job_dir, "scenes.json")) or {}
    seo = _read_optional_json(_resolve_operator_path(job_dir, "seo.json")) or {}
    visual_review = _read_optional_json(_resolve_operator_path(job_dir, "visual_review.json")) or {}

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
        path = _resolve_operator_path(job_dir, filename)
        artifact_rows.append(
            "<tr>"
            f"<td>{escape(filename)}</td>"
            f"<td>{_status_badge('PASS' if path.exists() else 'MISSING')}</td>"
            f'<td>{f"""<a href="{_relative_href(path, job_dir)}">{escape(filename)}</a>""" if path.exists() else ""}</td>'
            "</tr>"
        )

    video_path = _resolve_operator_path(job_dir, "video.mp4")
    thumbnail_path = _resolve_operator_path(job_dir, "thumbnail.jpg")
    contact_sheet = visual_review.get("contact_sheet", "visual_contact_sheet.jpg")
    contact_sheet_path = _resolve_operator_path(job_dir, contact_sheet)

    video_href = _relative_href(video_path, job_dir)
    thumbnail_href = _relative_href(thumbnail_path, job_dir)
    contact_href = _relative_href(contact_sheet_path, job_dir)
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
      <h2>Gemini QA</h2>
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
