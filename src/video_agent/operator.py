from __future__ import annotations

import json
from html import escape
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from video_agent.contracts import repo_root
from video_agent.operator_validators import load_operator_channel_config, validate_operator_artifact
from video_agent.utils.json_io import read_json, read_yaml, write_json
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


def _normalize_scenes_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Normalize scenes outputs from alternate model formats."""
    parsed = dict(candidate)
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
            or current.get("visual")
            or ""
        )
        caption = str(current.get("caption") or "")
        on_screen_text = str(current.get("on_screen_text") or caption)
        narration = str(current.get("narration") or "")
        motion = str(current.get("motion") or "slow push-in")
        asset_refs = current.get("asset_refs")
        if not isinstance(asset_refs, dict):
            asset_refs = {}

        normalized_scenes.append(
            {
                "id": scene_id,
                "duration_sec": duration,
                "narration": narration,
                "on_screen_text": on_screen_text,
                "caption": caption,
                "visual_prompt": visual_prompt,
                "motion": motion,
                "asset_refs": asset_refs,
            }
        )

    parsed["scenes"] = normalized_scenes
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


def _normalize_seo_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Backfill SEO fields for compatibility with older model/test payloads."""
    parsed = dict(candidate)
    parsed = _score_and_sort_seo_variants(parsed)

    title = str(parsed.get("title") or "").strip()
    thumbnail_text = str(parsed.get("thumbnail_text") or "").strip()
    if not thumbnail_text:
        words = [w for w in title.split() if w]
        fallback = " ".join(words[:5]).upper()
        parsed["thumbnail_text"] = fallback or "DUERME MEJOR HOY"
    return parsed


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
            "STYLE ANTI-REPETITION RULES (MANDATORY):",
            "• Do NOT reuse a repetitive tail sentence pattern across sections.",
            "• Do NOT repeat phrases like 'hazlo simple y con calma' or close variants more than once.",
            "• Each section narration_text must end differently (different verb + image + rhythm).",
            "• Keep tone warm and natural, but avoid formulaic copy-paste cadence.",
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
        "- each scene object: id, duration_sec, narration, on_screen_text, caption, visual_prompt, motion, asset_refs",
        f"- create {scenes_min}-{scenes_max} scenes; each scene duration_sec should be {scene_dur_target-3}–{scene_dur_target+3} seconds",
        f"- total_duration_sec must be approximately {target_sec} (sum of all scene durations)",
        "- scene ids: sequential scene-01, scene-02, ...",
        "- HOOK RULE: scene-01 narration must match the script hook word-for-word.",
        "  scene-01 on_screen_text: bold 3-6 word question or statement (e.g. '¿Por qué no puedes dormir?').",
        "- asset_refs: must be an object {}, never an array",
        "- on_screen_text MUST be 2-4 words (keyword hook), and MUST NOT duplicate caption text.",
        "- caption should be natural spoken sentence(s); never copy on_screen_text verbatim.",
        "- visual_prompt: English, stock-search friendly, specific (person + setting + action + lighting + camera framing).",
        "- visual_prompt must match sleep-wellness context for adults 45+: bedroom night routine, evening herbal tea, low-impact stretching, doctor consultation, calm morning sunlight.",
        "- avoid off-topic visuals (cars, highways, random city traffic, tech gadgets unless explicitly in narration).",
        "- motion: 'slow_zoom' / 'pan_right' / 'pan_left'; never repeat same motion 3x in a row",
        "- qa.verdict: must be PENDING_CLAUDE_QA — never mark your own scenes as PASS",
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


def _chatgpt_seo_prompt(channel_config: dict[str, Any], script: dict[str, Any], scenes: dict[str, Any]) -> str:
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
            "- job_id, title, description, tags, language, ai_disclosure, thumbnail_path, thumbnail_text",
            "- title_variants: array of EXACTLY 3 objects, each: {title, thumbnail_text}",
            "  • title: clear Spanish, searchable, 6-10 words, may include numbers or questions",
            "  • thumbnail_text: 3-5 words ALL-CAPS Spanish emotional hook (e.g. 'DUERME MEJOR HOY')",
            "  • Make 3 variants MEANINGFULLY DIFFERENT — vary angle, emotion, or specificity",
            "  • Do NOT repeat the same hook with minor word swaps",
            "- title: copy from the best title_variants entry",
            "- thumbnail_text: copy from the best title_variants entry",
            "- description: YouTube video description in Spanish, 150-200 words",
            "- language: must be es-419",
            "- tags: 5-8 concise Spanish/LatAm wellness search terms",
            "- ai_disclosure: must be true",
            "- thumbnail_path: leave as empty string ''",
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


def _claude_qa_prompt(artifact_name: str, artifact: dict[str, Any] | None) -> str:
    artifact_text = _json_block(artifact) if artifact is not None else "<paste ChatGPT JSON artifact here>"
    return "\n".join(
        [
            f"You are QA reviewer for the {artifact_name.upper()} artifact of a Spanish-language YouTube health channel.",
            "",
            "⚠️ OUTPUT RULES:",
            "• Return exactly ONE raw JSON object. No markdown. No commentary.",
            "• Start with { and end with }.",
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
            "• Channel fit: content matches es-419 Spanish health channel for adults 45+",
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
                (claude_dir / "script_qa_prompt.md", _claude_qa_prompt("script", script)),
            ]
        elif current_stage == "scenes":
            if script is None:
                raise FileNotFoundError(f"{job_dir / 'script.json'} is required before writing scenes prompts.")
            qa_feedback = get_scenes_qa_feedback(job_dir)
            paths_and_text = [
                (chatgpt_dir / "scenes_prompt.md", _chatgpt_scenes_prompt(channel_config, script, qa_feedback=qa_feedback)),
                (claude_dir / "scenes_qa_prompt.md", _claude_qa_prompt("scenes", scenes)),
            ]
        elif current_stage == "seo":
            if script is None:
                raise FileNotFoundError(f"{job_dir / 'script.json'} is required before writing SEO prompts.")
            if scenes is None:
                raise FileNotFoundError(f"{job_dir / 'scenes.json'} is required before writing SEO prompts.")
            seo = _read_optional_json(job_dir / "seo.json")
            paths_and_text = [
                (chatgpt_dir / "seo_prompt.md", _chatgpt_seo_prompt(channel_config, script, scenes)),
                (claude_dir / "seo_qa_prompt.md", _claude_qa_prompt("seo", seo)),
            ]
        else:
            raise ValueError(f"Unsupported operator prompt stage: {current_stage}")

        for path, text in paths_and_text:
            path.write_text(text + "\n", encoding="utf-8")
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
            candidate = _normalize_scenes_candidate(candidate)
        elif artifact == "seo":
            candidate = _normalize_seo_candidate(candidate)
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
      <p>{escape(str(seo.get("description", "")))}</p>
      <p class="meta">Providers: {escape(provider_text)}</p>
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
    output_path.write_text(html, encoding="utf-8")
    return output_path


# Backward-compatible alias for callers not yet migrated.
_gemini_qa_prompt = _claude_qa_prompt
