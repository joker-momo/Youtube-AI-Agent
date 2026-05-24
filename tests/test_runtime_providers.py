from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from video_agent.runtime import providers
from video_agent.runtime.providers import (
    AUDIO_SUBPROCESS_ENV,
    SubprocessAudioTaskProvider,
    audio_subprocess_env,
)


def test_audio_subprocess_env_marks_isolated_process(monkeypatch):
    monkeypatch.setattr(providers, "repo_root", lambda: Path("/repo"))

    env = audio_subprocess_env({"PYTHONPATH": "/existing"})

    assert env[AUDIO_SUBPROCESS_ENV] == "1"
    assert env["PYTHONPATH"].startswith("/repo/src")
    assert "/existing" in env["PYTHONPATH"]
    assert env["OMP_NUM_THREADS"] == "1"
    assert env["MKL_NUM_THREADS"] == "1"


def test_subprocess_audio_provider_builds_prepare_assets_command(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(providers.subprocess, "run", fake_run)

    provider = SubprocessAudioTaskProvider(python_executable="/python")
    provider.prepare_assets(tmp_path / "job", tmp_path / "channel.yaml")

    cmd, kwargs = calls[0]
    assert cmd == [
        "/python",
        "-m",
        "video_agent.audio_tasks",
        "prepare-assets",
        "--job-dir",
        str(tmp_path / "job"),
        "--channel-path",
        str(tmp_path / "channel.yaml"),
    ]
    assert kwargs["env"][AUDIO_SUBPROCESS_ENV] == "1"


def test_subprocess_audio_provider_requires_whisper_output(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(providers.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="without writing"):
        SubprocessAudioTaskProvider(python_executable="/python").run_whisper_timestamps(tmp_path)

