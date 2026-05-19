from __future__ import annotations

import json
from html import escape
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


@dataclass
class OperatorNextResult:
    step: str
    message: str
    prompt_paths: list[Path]
    commands: list[str]


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


def build_operator_status(job_dir: Path) -> dict[str, Any]:
    artifacts: dict[str, dict[str, str]] = {}
    for artifact in OPERATOR_ARTIFACTS:
        artifact_path = job_dir / f"{artifact}.json"
        qa_path = job_dir / "operator" / "gemini" / f"{artifact}_qa.json"
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
        qa_path = job_dir / "operator" / "gemini" / f"{artifact}_qa.json"
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
            f"<td>{f'<a href={_relative_href(path, job_dir)!r}>{escape(filename)}</a>' if path.exists() else ''}</td>"
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
    output_path.write_text(html, encoding="utf-8")
    return output_path
