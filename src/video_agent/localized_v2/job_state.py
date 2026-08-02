from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from video_agent.localized_v2.paths import RuntimePaths


@dataclass(frozen=True, slots=True)
class JobInput:
    job_id: str
    channel_id: str
    locale: str
    topic: str
    channel_snapshot: dict
    locale_snapshot: dict
    description: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PromotedArtifact:
    name: str
    path: Path
    sha256: str


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def create_job_snapshot(paths: RuntimePaths, job_input: JobInput) -> Path:
    paths.initialize()
    destination = paths.jobs / job_input.job_id
    if destination.exists():
        raise FileExistsError(f"localized V2 job already exists: {job_input.job_id}")
    staging = paths.jobs / f".{job_input.job_id}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        _atomic_json(staging / "input.json", job_input.to_dict())
        os.replace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def remove_job_snapshot(paths: RuntimePaths, job_id: str) -> None:
    shutil.rmtree(paths.jobs / job_id, ignore_errors=True)


def promote_artifacts(
    paths: RuntimePaths,
    job_id: str,
    stage: str,
    artifacts: dict[str, Path],
) -> tuple[PromotedArtifact, ...]:
    promoted: list[PromotedArtifact] = []
    destination_root = paths.jobs / job_id / "artifacts" / stage
    destination_root.mkdir(parents=True, exist_ok=True)
    for name, source in artifacts.items():
        if Path(name).name != name:
            raise ValueError(f"artifact name must be a basename: {name}")
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = destination_root / name
        temporary = destination.with_name(f".{destination.name}.tmp")
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
        promoted.append(
            PromotedArtifact(
                name=name,
                path=destination,
                sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
            )
        )
    return tuple(promoted)
