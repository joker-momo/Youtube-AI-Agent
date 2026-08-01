from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from video_agent.localized_v2.config import validate_artifact
from video_agent.localized_v2.contracts import ArtifactKind


class LocalizedRenderError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def to_failure(self) -> dict[str, object]:
        return {
            "code": self.code,
            "stage": "render",
            "provider": "remotion",
            "artifact": "final.mp4",
            "message": str(self),
            "retryable": self.code == "REMOTION_RENDER_FAILED",
        }


class VideoRenderer(Protocol):
    def render(
        self,
        *,
        artifacts_root: Path,
        props_path: Path,
        output_path: Path,
    ) -> Path: ...


class RemotionRenderer:
    def __init__(
        self,
        remotion_root: Path,
        schema_root: Path,
        *,
        runner=subprocess.run,
    ):
        self.remotion_root = remotion_root
        self.schema_root = schema_root
        self.runner = runner

    def render(
        self,
        *,
        artifacts_root: Path,
        props_path: Path,
        output_path: Path,
    ) -> Path:
        return render_localized_video(
            remotion_root=self.remotion_root,
            artifacts_root=artifacts_root,
            schema_root=self.schema_root,
            props_path=props_path,
            output_path=output_path,
            runner=self.runner,
        )


def build_render_command(
    *,
    remotion_root: Path,
    artifacts_root: Path,
    props_path: Path,
    output_path: Path,
    public_root: Path | None = None,
) -> tuple[str, ...]:
    root = remotion_root.resolve(strict=True)
    artifacts = artifacts_root.resolve(strict=True)
    public = (public_root or artifacts_root).resolve(strict=True)
    props = props_path.resolve(strict=True)
    output = output_path.resolve()
    if not (root / "src" / "localized-v2" / "index.ts").is_file():
        raise ValueError("localized V2 Remotion entrypoint is missing")
    if not props.is_file() or props.name != "render-props.json":
        raise ValueError("localized V2 render requires promoted render-props.json")
    if not props.is_relative_to(artifacts):
        raise ValueError("localized V2 render props must stay inside the artifact root")
    if output.suffix.lower() != ".mp4":
        raise ValueError("localized V2 render output must be MP4")
    return (
        "npx",
        "remotion",
        "render",
        "src/localized-v2/index.ts",
        "LocalizedV2ChannelVideo",
        str(output),
        "--props",
        str(props),
        "--public-dir",
        str(public),
    )


def _prepare_public_dir(
    *,
    remotion_root: Path,
    artifacts_root: Path,
    destination: Path,
) -> Path:
    artifacts = artifacts_root.resolve(strict=True)
    source_fonts = remotion_root.resolve(strict=True) / "public" / "localized-v2" / "fonts"
    font_names = ("Manrope-latin.woff2", "Manrope-latin-ext.woff2")
    for name in font_names:
        body = (source_fonts / name).read_bytes()
        if not body.startswith(b"wOF2"):
            raise LocalizedRenderError(
                "REMOTION_RENDER_FAILED",
                "localized V2 bundled font is missing or invalid",
            )
    public = destination.resolve()
    shutil.rmtree(public, ignore_errors=True)
    sources = [path for path in artifacts.rglob("*") if path.is_file()]
    public.mkdir(parents=True)
    for source in sources:
        relative = source.relative_to(artifacts)
        target = public / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target)
        except OSError:
            shutil.copyfile(source, target)
    target_fonts = public / "localized-v2" / "fonts"
    target_fonts.mkdir(parents=True, exist_ok=True)
    for name in font_names:
        shutil.copyfile(source_fonts / name, target_fonts / name)
    return public


def render_localized_video(
    *,
    remotion_root: Path,
    artifacts_root: Path,
    schema_root: Path,
    props_path: Path,
    output_path: Path,
    runner=subprocess.run,
) -> Path:
    try:
        payload = json.loads(props_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalizedRenderError(
            "INVALID_RENDER_PROPS",
            "localized V2 render props cannot be parsed",
        ) from exc
    if not isinstance(payload, dict):
        raise LocalizedRenderError(
            "INVALID_RENDER_PROPS",
            "localized V2 render props must contain an object",
        )
    try:
        validate_artifact(payload, ArtifactKind.RENDER_PROPS, schema_root)
    except ValueError as exc:
        raise LocalizedRenderError(
            "INVALID_RENDER_PROPS",
            "localized V2 render props failed contract validation",
        ) from exc
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    public_root = _prepare_public_dir(
        remotion_root=remotion_root,
        artifacts_root=artifacts_root,
        destination=output_path.parent / ".localized-v2-public",
    )
    command: Sequence[str] = build_render_command(
        remotion_root=remotion_root,
        artifacts_root=artifacts_root,
        props_path=props_path,
        output_path=output_path,
        public_root=public_root,
    )
    try:
        result = runner(
            command,
            cwd=remotion_root,
            capture_output=True,
            check=False,
            text=True,
            timeout=7200,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalizedRenderError(
            "REMOTION_RENDER_FAILED",
            "localized V2 Remotion process failed to complete",
        ) from exc
    if result.returncode != 0 or not output_path.is_file():
        output_path.unlink(missing_ok=True)
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        suffix = f": {detail[-1][:600]}" if detail else ""
        raise LocalizedRenderError(
            "REMOTION_RENDER_FAILED",
            f"localized V2 Remotion render did not produce final media{suffix}",
        )
    return output_path
