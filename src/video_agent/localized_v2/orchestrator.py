from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_agent.localized_v2.config import validate_artifact
from video_agent.localized_v2.content_safety import validate_localized_content
from video_agent.localized_v2.contracts import ArtifactKind
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.prompts import PromptEnvelope
from video_agent.localized_v2.prompts.idea import build_idea_prompt
from video_agent.localized_v2.prompts.qa import build_qa_prompt
from video_agent.localized_v2.prompts.scenes import build_scenes_prompt
from video_agent.localized_v2.prompts.script import build_script_prompt
from video_agent.localized_v2.prompts.seo import build_seo_prompt
from video_agent.localized_v2.providers import (
    ProviderBoundaryError,
    StructuredProvider,
    validate_structured_response,
)

PROMPT_STAGES = ("idea", "script", "scenes", "seo", "qa")
ARTIFACT_NAMES = {stage: f"{stage}.json" for stage in PROMPT_STAGES}
ARTIFACT_KINDS = {
    "idea": ArtifactKind.IDEA,
    "script": ArtifactKind.SCRIPT,
    "scenes": ArtifactKind.SCENES,
    "seo": ArtifactKind.SEO,
    "qa": ArtifactKind.QA,
}


class LocalizedQARejected(ValueError):
    def __init__(
        self,
        locale: str,
        provider: str,
        failures: list[dict[str, Any]],
    ):
        super().__init__("localized content QA rejected the candidate artifacts")
        self.locale = locale
        self.provider = provider
        self.failures = failures

    def to_failure(self) -> dict[str, Any]:
        return {
            "code": "LOCALIZED_QA_REJECTED",
            "locale": self.locale,
            "stage": "qa",
            "provider": self.provider,
            "artifact": "qa",
            "message": str(self),
            "failures": self.failures,
            "retryable": True,
        }


class LocalizedPromptRunner:
    def __init__(
        self,
        paths: RuntimePaths,
        schema_root: Path,
        provider: StructuredProvider,
    ):
        self.paths = paths
        self.schema_root = schema_root.resolve()
        self.provider = provider

    def stages(self, _job: dict[str, Any]) -> tuple[str, ...]:
        return PROMPT_STAGES

    def _artifact_path(self, job_id: str, stage: str) -> Path:
        root = (self.paths.jobs / job_id / "artifacts").resolve()
        path = (root / stage / ARTIFACT_NAMES[stage]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("localized V2 artifact escaped its job root")
        return path

    def _load(
        self,
        job: dict[str, Any],
        stage: str,
        locale_pack: dict[str, Any],
    ) -> dict[str, Any]:
        path = self._artifact_path(job["jobId"], stage)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"localized V2 {stage} artifact must contain an object")
        validate_artifact(payload, ARTIFACT_KINDS[stage], self.schema_root)
        validate_localized_content(ARTIFACT_KINDS[stage], payload, locale_pack)
        return payload

    @staticmethod
    def _snapshots(job: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        job_input = job["input"]
        channel = job_input["channel_snapshot"]
        locale_pack = job_input["locale_snapshot"]
        if channel["locale"] != locale_pack["locale"] or channel["locale"] != job["locale"]:
            raise ValueError("localized V2 immutable locale snapshots do not match")
        return channel, locale_pack

    def _prompt(self, job: dict[str, Any], stage: str) -> PromptEnvelope:
        channel, locale_pack = self._snapshots(job)
        if stage == "idea":
            return build_idea_prompt(channel, locale_pack, job["topic"])
        if stage == "script":
            return build_script_prompt(
                channel,
                locale_pack,
                self._load(job, "idea", locale_pack),
            )
        if stage == "scenes":
            return build_scenes_prompt(
                channel,
                locale_pack,
                self._load(job, "script", locale_pack),
            )
        if stage == "seo":
            return build_seo_prompt(
                channel,
                locale_pack,
                self._load(job, "script", locale_pack),
            )
        if stage == "qa":
            return build_qa_prompt(
                locale_pack,
                {
                    name: self._load(job, name, locale_pack)
                    for name in ("idea", "script", "scenes", "seo")
                },
            )
        raise ValueError(f"unknown localized V2 prompt stage: {stage}")

    def run_stage(
        self,
        job: dict[str, Any],
        stage: str,
        work_dir: Path,
    ) -> dict[str, Path]:
        prompt = self._prompt(job, stage)
        _channel, locale_pack = self._snapshots(job)
        provider_name = str(getattr(self.provider, "name", type(self.provider).__name__))
        try:
            raw = self.provider.generate(prompt)
        except Exception as exc:
            raise ProviderBoundaryError(
                "PROVIDER_ERROR",
                locale=job["locale"],
                stage=stage,
                provider=provider_name,
                artifact=prompt.artifact_kind.value,
                message=f"structured provider failed with {type(exc).__name__}",
            ) from exc
        payload = validate_structured_response(
            raw,
            prompt=prompt,
            locale_pack=locale_pack,
            schema_root=self.schema_root,
            provider=provider_name,
        )
        if stage == "qa" and payload["verdict"] != "PASS":
            raise LocalizedQARejected(
                job["locale"],
                provider_name,
                payload["failures"],
            )
        work_dir.mkdir(parents=True, exist_ok=True)
        output = work_dir / ARTIFACT_NAMES[stage]
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {output.name: output}
