from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "legacy_vida_plena"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
BASELINE_SCRIPT = REPO_ROOT / "scripts" / "capture_legacy_localization_baseline.py"
ALLOWED_NEW_PREFIXES = (
    "assets/localized-v2/",
    "configs/localized-v2/",
    "docs/implementation/localized_v2",
    "remotion/public/localized-v2/",
    "remotion/src/localized-v2/",
    "schemas/localized-v2/",
    "src/video_agent/localized_v2/",
    "tests/localized_v2/",
)
ALLOWED_NEW_FILES = {
    "docs/runbooks/localized_v2_canary.md",
    "schemas/locale-pack-v2.schema.json",
    "schemas/localized-channel-v2.schema.json",
    "scripts/capture_legacy_localization_baseline.py",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_frozen_artifacts_match_manifest() -> None:
    manifest = _manifest()

    for relative_path, metadata in manifest["artifacts"].items():
        fixture = FIXTURE_ROOT / "artifacts" / relative_path
        assert fixture.is_file(), relative_path
        assert _sha256(fixture) == metadata["sha256"], relative_path
        assert fixture.stat().st_size == metadata["size_bytes"], relative_path

    job_snapshot = json.loads(
        (FIXTURE_ROOT / "artifacts" / "job.json").read_text(encoding="utf-8")
    )
    assert [stage["name"] for stage in job_snapshot["stages"]] == manifest["stage_sequence"]
    assert all(stage["status"] == "completed" for stage in job_snapshot["stages"])


def test_preexisting_tracked_files_keep_baseline_hashes() -> None:
    manifest = _manifest()

    for relative_path, expected_hash in manifest["tracked_files"].items():
        current_path = REPO_ROOT / relative_path
        assert current_path.is_file(), relative_path
        assert _sha256(current_path) == expected_hash, relative_path


def test_legacy_import_does_not_load_localized_v2() -> None:
    command = [
        sys.executable,
        "-c",
        (
            "import json,sys;"
            "import video_agent.orchestrator.worker;"
            "print(json.dumps(sorted(k for k in sys.modules "
            "if k.startswith('video_agent.localized_v2'))))"
        ),
    ]
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == []


def test_legacy_render_concurrency_remains_auto() -> None:
    config = yaml.safe_load(
        (REPO_ROOT / "configs" / "vida-plena-45" / "channel.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert config["render"]["concurrency"] == "auto"


def test_capture_refuses_non_terminal_job(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-running"
    job_dir.mkdir()
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "job_id": "job-running",
                "channel_id": "vida-plena-45",
                "current_stage": "render",
                "stages": [{"name": "render", "status": "running"}],
            }
        ),
        encoding="utf-8",
    )
    queue_path = tmp_path / "queue.db"
    with sqlite3.connect(queue_path) as connection:
        connection.execute(
            """
            CREATE TABLE job_queue (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT,
                started_at TEXT,
                completed_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO job_queue (job_id, status) VALUES (?, ?)",
            ("job-running", "running"),
        )

    result = subprocess.run(
        [
            sys.executable,
            str(BASELINE_SCRIPT),
            "--repo-root",
            str(REPO_ROOT),
            "--job-dir",
            str(job_dir),
            "--queue-db",
            str(queue_path),
            "--output-dir",
            str(tmp_path / "baseline"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "not terminal" in result.stderr.lower()
    assert not (tmp_path / "baseline").exists()


def test_repository_changes_are_additive_and_allowlisted() -> None:
    manifest = _manifest()
    baseline_ref = manifest["baseline_ref"]
    tracked_at_baseline = set(manifest["tracked_files"])
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    for line in result.stdout.splitlines():
        status = line[:2]
        relative_path = line[3:]
        if " -> " in relative_path:
            relative_path = relative_path.split(" -> ", 1)[1]
        assert relative_path not in tracked_at_baseline, (
            f"pre-existing tracked file changed after {baseline_ref}: "
            f"{status} {relative_path}"
        )
        assert relative_path in ALLOWED_NEW_FILES or relative_path.startswith(
            ALLOWED_NEW_PREFIXES
        ), f"new path is outside KTD1 allowlist: {relative_path}"
