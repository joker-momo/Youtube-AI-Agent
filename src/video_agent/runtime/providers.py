from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from video_agent.contracts import repo_root

AUDIO_SUBPROCESS_ENV = "VIDEO_AGENT_AUDIO_SUBPROCESS"


class AudioTaskProvider(Protocol):
    def run_whisper_timestamps(self, job_dir: Path) -> Path:
        ...

    def prepare_assets(self, job_dir: Path, channel_path: Path) -> None:
        ...


def audio_subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base or os.environ)
    env[AUDIO_SUBPROCESS_ENV] = "1"
    src_path = str(repo_root() / "src")
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{src_path}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else src_path
    )
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("MKL_NUM_THREADS", "1")
    return env


@dataclass(frozen=True)
class SubprocessAudioTaskProvider:
    python_executable: str = sys.executable

    def _run(self, args: list[str], *, error_prefix: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [self.python_executable, "-m", "video_agent.audio_tasks", *args],
            check=False,
            capture_output=True,
            text=True,
            env=audio_subprocess_env(),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"{error_prefix}: {detail[-2000:]}")
        return result

    def run_whisper_timestamps(self, job_dir: Path) -> Path:
        self._run(
            ["whisper-timestamps", "--job-dir", str(job_dir)],
            error_prefix="Audio subprocess failed for whisper-timestamps",
        )
        output = job_dir / "json/whisper_timestamps.json"
        if not output.exists():
            output = job_dir / "whisper_timestamps.json"
        if not output.exists():
            raise RuntimeError(f"Audio subprocess completed without writing {output}")
        return output

    def prepare_assets(self, job_dir: Path, channel_path: Path) -> None:
        self._run(
            [
                "prepare-assets",
                "--job-dir",
                str(job_dir),
                "--channel-path",
                str(channel_path),
            ],
            error_prefix="Audio asset subprocess failed",
        )

