from pathlib import Path

from video_agent.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_cli_run_without_render(tmp_path, capsys):
    exit_code = main(
        [
            "run",
            "--channel",
            str(ROOT / "configs/vida-plena-45/channel.yaml"),
            "--idea",
            str(ROOT / "inputs/manual_idea.json"),
            "--jobs-dir",
            str(tmp_path),
            "--no-render",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Job completed:" in captured.out
    assert "video.mp4: skipped" in captured.out


def test_cli_batch_without_render_writes_audit(tmp_path, capsys):
    audit_path = tmp_path / "batch_audit.md"
    exit_code = main(
        [
            "batch",
            "--channel",
            str(ROOT / "configs/vida-plena-45/channel.yaml"),
            "--idea",
            str(ROOT / "inputs/manual_idea.json"),
            "--idea",
            str(ROOT / "inputs/demo_idea_light_dinner.json"),
            "--jobs-dir",
            str(tmp_path / "jobs"),
            "--audit-path",
            str(audit_path),
            "--no-render",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Batch completed: 2 jobs" in captured.out
    assert "Visual Batch Audit" in captured.out
    assert audit_path.exists()
    audit = audit_path.read_text(encoding="utf-8")
    assert "| Job | Topic | Video | Visual QA | Source mix |" in audit
    assert audit.count("| `") == 2


def test_cli_audit_existing_jobs(tmp_path, capsys):
    jobs_dir = tmp_path / "jobs"
    batch_exit = main(
        [
            "batch",
            "--channel",
            str(ROOT / "configs/vida-plena-45/channel.yaml"),
            "--idea",
            str(ROOT / "inputs/manual_idea.json"),
            "--jobs-dir",
            str(jobs_dir),
            "--no-render",
        ]
    )
    assert batch_exit == 0
    job_dir = next(path for path in jobs_dir.iterdir() if path.is_dir())

    exit_code = main(["audit", "--job", str(job_dir)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Visual Batch Audit" in captured.out
    assert job_dir.name in captured.out
