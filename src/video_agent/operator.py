from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from video_agent.contracts import repo_root
from video_agent.utils.json_io import read_json, read_yaml, write_json
from video_agent.utils.validation import validate_json

ARTIFACT_SCHEMAS = {
    "script": "schemas/script.schema.json",
    "scenes": "schemas/scenes.schema.json",
    "seo": "schemas/seo.schema.json",
}
OPERATOR_ARTIFACTS = tuple(ARTIFACT_SCHEMAS.keys())


@dataclass
class PromptWriteResult:
    paths: list[Path]


@dataclass
class PromoteResult:
    artifact: str
    raw_path: Path
    output_path: Path


def extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model output.")

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
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
            depth -= 1
            if depth == 0:
                return json.loads(text[start : index + 1])

    raise ValueError("JSON object was started but not closed.")


def _json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    return read_json(path) if path.exists() else None


def _chatgpt_script_prompt(channel_config: dict[str, Any], idea: dict[str, Any]) -> str:
    return "\n".join(
        [
            "You are creating the SCRIPT artifact for this YouTube channel.",
            "Return exactly one valid JSON object. No markdown. No commentary.",
            "",
            "Required schema summary:",
            "- channel_id, job_id, hook, sections, narration, cta, qa",
            "- sections must be an array of short structured section objects",
            "- narration must be natural Spanish for a 45-60 second video",
            "- qa.verdict should be PASS when you believe the artifact is ready for Gemini QA",
            "",
            "Channel config:",
            _json_block(channel_config),
            "",
            "Video idea:",
            _json_block(idea),
        ]
    )


def _chatgpt_scenes_prompt(channel_config: dict[str, Any], script: dict[str, Any]) -> str:
    return "\n".join(
        [
            "You are creating the SCENES artifact from an approved script.",
            "Return exactly one valid JSON object. No markdown. No commentary.",
            "",
            "Required schema summary:",
            "- channel_id, job_id, scenes, total_duration_sec, qa",
            "- scenes must include id, duration_sec, narration, on_screen_text, caption, visual_prompt, motion, asset_refs",
            "- use 4-6 scenes and make total_duration_sec match the sum of scene durations",
            "- visual_prompt should be stock-search friendly and specific",
            "",
            "Channel config:",
            _json_block(channel_config),
            "",
            "Approved script:",
            _json_block(script),
        ]
    )


def _chatgpt_seo_prompt(channel_config: dict[str, Any], script: dict[str, Any], scenes: dict[str, Any]) -> str:
    return "\n".join(
        [
            "You are creating the SEO artifact for the approved video.",
            "Return exactly one valid JSON object. No markdown. No commentary.",
            "",
            "Required schema summary:",
            "- title, description, tags, language, ai_disclosure, thumbnail_path",
            "- title should be clear Spanish, searchable, and not clickbait",
            "- tags should be concise Spanish/LatAm wellness search terms",
            "- ai_disclosure must be true",
            "",
            "Channel config:",
            _json_block(channel_config),
            "",
            "Approved script:",
            _json_block(script),
            "",
            "Approved scenes:",
            _json_block(scenes),
        ]
    )


def _gemini_qa_prompt(artifact_name: str, artifact: dict[str, Any] | None) -> str:
    artifact_text = _json_block(artifact) if artifact is not None else "<paste ChatGPT JSON artifact here>"
    return "\n".join(
        [
            f"You are QA for the {artifact_name.upper()} artifact.",
            "Check schema fit, channel fit, safety, clarity, and whether this is ready for rendering.",
            "Return exactly one JSON object with: verdict, scores, issues, required_changes.",
            "Use verdict PASS only when no required changes remain.",
            "",
            "Artifact to review:",
            artifact_text,
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
    gemini_dir = prompt_dir / "gemini"
    chatgpt_dir.mkdir(parents=True, exist_ok=True)
    gemini_dir.mkdir(parents=True, exist_ok=True)

    stages = ["script", "scenes", "seo"] if stage == "all" else [stage]
    written: list[Path] = []

    script = _read_optional_json(job_dir / "script.json")
    scenes = _read_optional_json(job_dir / "scenes.json")

    for current_stage in stages:
        if current_stage == "script":
            paths_and_text = [
                (chatgpt_dir / "script_prompt.md", _chatgpt_script_prompt(channel_config, idea)),
                (gemini_dir / "script_qa_prompt.md", _gemini_qa_prompt("script", script)),
            ]
        elif current_stage == "scenes":
            if script is None:
                raise FileNotFoundError(f"{job_dir / 'script.json'} is required before writing scenes prompts.")
            paths_and_text = [
                (chatgpt_dir / "scenes_prompt.md", _chatgpt_scenes_prompt(channel_config, script)),
                (gemini_dir / "scenes_qa_prompt.md", _gemini_qa_prompt("scenes", scenes)),
            ]
        elif current_stage == "seo":
            if script is None:
                raise FileNotFoundError(f"{job_dir / 'script.json'} is required before writing SEO prompts.")
            if scenes is None:
                raise FileNotFoundError(f"{job_dir / 'scenes.json'} is required before writing SEO prompts.")
            seo = _read_optional_json(job_dir / "seo.json")
            paths_and_text = [
                (chatgpt_dir / "seo_prompt.md", _chatgpt_seo_prompt(channel_config, script, scenes)),
                (gemini_dir / "seo_qa_prompt.md", _gemini_qa_prompt("seo", seo)),
            ]
        else:
            raise ValueError(f"Unsupported operator prompt stage: {current_stage}")

        for path, text in paths_and_text:
            path.write_text(text + "\n", encoding="utf-8")
            written.append(path)

    return PromptWriteResult(paths=written)


def promote_operator_artifact(job_dir: Path, artifact: str, raw_path: Path) -> PromoteResult:
    if artifact not in ARTIFACT_SCHEMAS:
        raise ValueError(f"Unsupported operator artifact: {artifact}")

    parsed = extract_json_object(raw_path.read_text(encoding="utf-8"))
    root = repo_root()
    validate_json(parsed, root / ARTIFACT_SCHEMAS[artifact])
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

    parsed = extract_json_object(raw_path.read_text(encoding="utf-8"))
    qa = _normalize_operator_qa(artifact, parsed)
    output_path = job_dir / "operator" / "gemini" / f"{artifact}_qa.json"
    write_json(output_path, qa)
    return PromoteResult(artifact=artifact, raw_path=raw_path, output_path=output_path)


def assert_operator_qa_passed(job_dir: Path, artifacts: list[str] | tuple[str, ...] = OPERATOR_ARTIFACTS) -> None:
    for artifact in artifacts:
        if artifact not in ARTIFACT_SCHEMAS:
            raise ValueError(f"Unsupported operator artifact QA: {artifact}")
        qa_path = job_dir / "operator" / "gemini" / f"{artifact}_qa.json"
        if not qa_path.exists():
            raise FileNotFoundError(f"{qa_path} is required before operator render.")
        qa = read_json(qa_path)
        verdict = str(qa.get("verdict", "")).upper()
        if verdict != "PASS":
            raise ValueError(f"{qa_path} must have verdict PASS before operator render. Got: {verdict or '<missing>'}")
