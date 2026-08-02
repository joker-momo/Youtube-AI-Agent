from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from video_agent.localized_v2.assets import LocalizedAssetPipeline
from video_agent.localized_v2.audio.capabilities import VoiceSpec
from video_agent.localized_v2.audio.timing import (
    compile_audio_timing,
    concatenate_wav,
)
from video_agent.localized_v2.audio.tts import LocalizedTTS
from video_agent.localized_v2.brand_assets import BrandClip
from video_agent.localized_v2.config import ARTIFACT_SCHEMA_FILES, validate_artifact
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
from video_agent.localized_v2.queue import LocalizedQueue
from video_agent.localized_v2.render import VideoRenderer
from video_agent.localized_v2.render_props import compile_render_props

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
        source_context = {
            "topic": job["topic"],
            "description": job["input"].get("description"),
        }
        if stage == "idea":
            prompt = build_idea_prompt(
                channel,
                locale_pack,
                job["topic"],
                job["input"].get("description"),
            )
        elif stage == "script":
            prompt = build_script_prompt(
                channel,
                locale_pack,
                self._load(job, "idea", locale_pack),
                source_context,
            )
        elif stage == "scenes":
            prompt = build_scenes_prompt(
                channel,
                locale_pack,
                self._load(job, "script", locale_pack),
                source_context,
            )
        elif stage == "seo":
            prompt = build_seo_prompt(
                channel,
                locale_pack,
                self._load(job, "script", locale_pack),
                source_context,
            )
        elif stage == "qa":
            prompt = build_qa_prompt(
                locale_pack,
                {
                    name: self._load(job, name, locale_pack)
                    for name in ("idea", "script", "scenes", "seo")
                },
                source_context,
            )
        else:
            raise ValueError(f"unknown localized V2 prompt stage: {stage}")
        return self._attach_response_schema(prompt)

    def _attach_response_schema(self, prompt: PromptEnvelope) -> PromptEnvelope:
        schema_path = self.schema_root / ARTIFACT_SCHEMA_FILES[prompt.artifact_kind]
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if not isinstance(schema, dict):
            raise ValueError("localized V2 response schema must contain an object")
        payload = dict(prompt.payload)
        contract = dict(payload.get("responseContract") or {})
        contract.pop("artifactKind", None)
        contract["jsonSchema"] = schema
        payload["responseContract"] = contract
        system = "\n".join(
            [
                prompt.system,
                "responseContract.jsonSchema is authoritative for the output object.",
                "Include every required field and no field absent from jsonSchema.properties.",
                "Do not copy request metadata such as artifactKind into the output.",
            ]
        )
        return PromptEnvelope(
            stage=prompt.stage,
            system=system,
            payload=payload,
            artifact_kind=prompt.artifact_kind,
        )

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


class LocalizedOrchestrator:
    def __init__(
        self,
        prompt_runner: LocalizedPromptRunner,
        tts: LocalizedTTS,
    ):
        self.prompt_runner = prompt_runner
        self.tts = tts

    @property
    def paths(self) -> RuntimePaths:
        return self.prompt_runner.paths

    def stages(self, _job: dict[str, Any]) -> tuple[str, ...]:
        return (*PROMPT_STAGES, "audio", "timing")

    def _audio_paths(
        self,
        job_id: str,
        scene_ids: list[str],
    ) -> dict[str, Path]:
        root = (self.paths.jobs / job_id / "artifacts" / "audio").resolve()
        paths = {scene_id: (root / f"{scene_id}.wav").resolve() for scene_id in scene_ids}
        if any(not path.is_relative_to(root) for path in paths.values()):
            raise ValueError("localized V2 narration escaped its audio artifact root")
        return paths

    def run_stage(
        self,
        job: dict[str, Any],
        stage: str,
        work_dir: Path,
    ) -> dict[str, Path]:
        if stage in PROMPT_STAGES:
            return self.prompt_runner.run_stage(job, stage, work_dir)
        _channel, locale_pack = self.prompt_runner._snapshots(job)
        scenes_artifact = self.prompt_runner._load(job, "scenes", locale_pack)
        scenes = scenes_artifact["scenes"]
        if stage == "audio":
            voice = VoiceSpec.from_channel(job["input"]["channel_snapshot"])
            outputs = self.tts.synthesize_scenes(
                locale=job["locale"],
                voice=voice,
                scenes=scenes,
                output_dir=work_dir,
            )
            narration = work_dir / "narration.wav"
            concatenate_wav(
                [outputs[str(scene["id"])] for scene in scenes],
                narration,
            )
            return {
                **{path.name: path for path in outputs.values()},
                narration.name: narration,
            }
        if stage == "timing":
            scene_ids = [str(scene["id"]) for scene in scenes]
            timing = compile_audio_timing(
                job["locale"],
                scenes,
                self._audio_paths(job["jobId"], scene_ids),
            )
            validate_artifact(
                timing,
                ArtifactKind.AUDIO_TIMING,
                self.prompt_runner.schema_root,
            )
            work_dir.mkdir(parents=True, exist_ok=True)
            output = work_dir / "audio-timing.json"
            output.write_text(
                json.dumps(timing, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return {output.name: output}
        raise ValueError(f"unknown localized V2 stage: {stage}")


class LocalizedMediaOrchestrator:
    MEDIA_STAGES = ("assets", "branding", "render_props")

    def __init__(
        self,
        content: LocalizedOrchestrator,
        assets: LocalizedAssetPipeline,
        brand_clips: dict[str, BrandClip],
        queue: LocalizedQueue,
        renderer: VideoRenderer | None = None,
    ):
        if set(brand_clips) != {"intro", "disclaimer", "outro"}:
            raise ValueError("localized V2 requires intro, disclaimer, and outro clips")
        self.content = content
        self.assets = assets
        self.brand_clips = brand_clips
        self.queue = queue
        self.renderer = renderer

    @property
    def paths(self) -> RuntimePaths:
        return self.content.paths

    def stages(self, job: dict[str, Any]) -> tuple[str, ...]:
        stages = (*self.content.stages(job), *self.MEDIA_STAGES)
        return (*stages, "render") if self.renderer else stages

    def _artifact_json(
        self,
        job_id: str,
        stage: str,
        name: str,
    ) -> dict[str, Any]:
        root = (self.paths.jobs / job_id / "artifacts").resolve()
        path = (root / stage / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("localized V2 artifact path escaped its job root")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"localized V2 {name} must contain an object")
        return payload

    def _promoted_artifacts(self, job_id: str) -> dict[Path, str]:
        return {
            Path(item["path"]).resolve(): str(item["sha256"])
            for item in self.queue.list_artifacts(job_id)
        }

    def run_stage(
        self,
        job: dict[str, Any],
        stage: str,
        work_dir: Path,
    ) -> dict[str, Path]:
        if stage not in (*self.MEDIA_STAGES, "render"):
            return self.content.run_stage(job, stage, work_dir)
        job_id = str(job["jobId"])
        channel, locale_pack = self.content.prompt_runner._snapshots(job)
        if stage == "assets":
            scenes = self.content.prompt_runner._load(job, "scenes", locale_pack)
            seo = self.content.prompt_runner._load(job, "seo", locale_pack)
            outputs = self.assets.build(
                locale_pack=locale_pack,
                topic=str(job["topic"]),
                scenes=scenes,
                seo=seo,
                output_dir=work_dir,
                promoted_root=self.paths.jobs / job_id / "artifacts" / "assets",
                channel_id=str(job["channelId"]),
                job_id=job_id,
            )
            manifest = json.loads(
                outputs["asset-manifest.json"].read_text(encoding="utf-8")
            )
            validate_artifact(
                manifest,
                ArtifactKind.ASSET_MANIFEST,
                self.content.prompt_runner.schema_root,
            )
            return outputs
        if stage == "branding":
            work_dir.mkdir(parents=True, exist_ok=True)
            outputs: dict[str, Path] = {}
            for name, clip in self.brand_clips.items():
                suffix = clip.path.suffix.lower() or ".mp4"
                destination = work_dir / f"{name}{suffix}"
                shutil.copyfile(clip.path, destination)
                outputs[destination.name] = destination
            return outputs
        if stage == "render_props":
            scenes = self.content.prompt_runner._load(job, "scenes", locale_pack)
            seo = self.content.prompt_runner._load(job, "seo", locale_pack)
            timing = self._artifact_json(job_id, "timing", "audio-timing.json")
            manifest = self._artifact_json(job_id, "assets", "asset-manifest.json")
            promoted = self._promoted_artifacts(job_id)
            artifacts_root = self.paths.jobs / job_id / "artifacts"
            promoted_brand = {
                name: BrandClip(
                    path=next(
                        path
                        for path in promoted
                        if path.parent == (artifacts_root / "branding").resolve()
                        and path.stem == name
                    ),
                    duration_sec=clip.duration_sec,
                )
                for name, clip in self.brand_clips.items()
            }
            props = compile_render_props(
                job_root=self.paths.jobs / job_id,
                promoted_artifacts=promoted,
                schema_root=self.content.prompt_runner.schema_root,
                channel=channel,
                locale_pack=locale_pack,
                scenes=scenes,
                timing=timing,
                seo=seo,
                asset_manifest=manifest,
                narration_path=artifacts_root / "audio" / "narration.wav",
                brand_clips=promoted_brand,
            )
            work_dir.mkdir(parents=True, exist_ok=True)
            output = work_dir / "render-props.json"
            output.write_text(
                json.dumps(props, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return {output.name: output}
        if stage == "render" and self.renderer is not None:
            work_dir.mkdir(parents=True, exist_ok=True)
            output = work_dir / "final.mp4"
            artifacts_root = self.paths.jobs / job_id / "artifacts"
            result = self.renderer.render(
                artifacts_root=artifacts_root,
                props_path=artifacts_root / "render_props" / "render-props.json",
                output_path=output,
            )
            return {result.name: result}
        raise ValueError(f"unknown localized V2 media stage: {stage}")
