from pathlib import Path
from types import SimpleNamespace

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


def test_cli_run_accepts_tts_overrides(tmp_path, monkeypatch):
    calls = []

    def fake_run_pipeline(options):
        calls.append(options)
        return SimpleNamespace(
            job_dir=tmp_path / "job",
            thumbnail_path=tmp_path / "job/thumbnail.jpg",
            seo_path=tmp_path / "job/seo.json",
            report_path=tmp_path / "job/report.md",
            video_path=None,
        )

    monkeypatch.setattr("video_agent.cli.run_pipeline", fake_run_pipeline)

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
            "--tts-provider",
            "kokoro",
            "--tts-voice-id",
            "ef_dora",
            "--tts-lang-code",
            "e",
            "--tts-speed",
            "0.92",
        ]
    )

    assert exit_code == 0
    assert calls[0].tts_override == {
        "provider": "kokoro",
        "voice_id": "ef_dora",
        "lang_code": "e",
        "speed": 0.92,
    }


def test_cli_operator_render_accepts_existing_job_artifacts(tmp_path, monkeypatch):
    calls = []

    def fake_render_operator_job(options):
        calls.append(options)
        return SimpleNamespace(
            job_dir=tmp_path / "job",
            thumbnail_path=tmp_path / "job/thumbnail.jpg",
            seo_path=tmp_path / "job/seo.json",
            report_path=tmp_path / "job/report.md",
            video_path=None,
        )

    monkeypatch.setattr("video_agent.cli.render_operator_job", fake_render_operator_job)

    exit_code = main(
        [
            "operator-render",
            "--channel",
            str(ROOT / "configs/vida-plena-45/channel.yaml"),
            "--job-dir",
            str(tmp_path / "operator-job"),
            "--no-render",
            "--tts-provider",
            "kokoro",
            "--tts-voice-id",
            "ef_dora",
        ]
    )

    assert exit_code == 0
    assert calls[0].job_dir == tmp_path / "operator-job"
    assert calls[0].render is False
    assert calls[0].require_operator_qa is True
    assert calls[0].tts_override == {"provider": "kokoro", "voice_id": "ef_dora"}


def test_cli_operator_render_can_skip_operator_qa_gate(tmp_path, monkeypatch):
    calls = []

    def fake_render_operator_job(options):
        calls.append(options)
        return SimpleNamespace(
            job_dir=tmp_path / "job",
            thumbnail_path=tmp_path / "job/thumbnail.jpg",
            seo_path=tmp_path / "job/seo.json",
            report_path=tmp_path / "job/report.md",
            video_path=None,
        )

    monkeypatch.setattr("video_agent.cli.render_operator_job", fake_render_operator_job)

    exit_code = main(
        [
            "operator-render",
            "--channel",
            str(ROOT / "configs/vida-plena-45/channel.yaml"),
            "--job-dir",
            str(tmp_path / "operator-job"),
            "--no-render",
            "--skip-operator-qa",
        ]
    )

    assert exit_code == 0
    assert calls[0].require_operator_qa is False


def test_cli_operator_promote_qa_writes_promoted_qa(tmp_path, capsys):
    raw_path = tmp_path / "script_qa.raw.txt"
    raw_path.write_text('{"verdict": "PASS", "issues": [], "required_changes": [], "scores": {"safety": 10}}', encoding="utf-8")

    exit_code = main(
        [
            "operator-promote-qa",
            "--job-dir",
            str(tmp_path / "operator-job"),
            "--artifact",
            "script",
            "--raw-file",
            str(raw_path),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Promoted script QA:" in captured.out
    assert (tmp_path / "operator-job/operator/gemini/script_qa.json").exists()


def test_cli_operator_review_writes_html(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_write_operator_review(job_dir, output_path=None):
        calls.append((job_dir, output_path))
        path = output_path or job_dir / "operator_review.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html>review</html>", encoding="utf-8")
        return path

    monkeypatch.setattr("video_agent.cli.write_operator_review", fake_write_operator_review)

    output_path = tmp_path / "review.html"
    exit_code = main(
        [
            "operator-review",
            "--job-dir",
            str(tmp_path / "operator-job"),
            "--output",
            str(output_path),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls == [(tmp_path / "operator-job", output_path)]
    assert "operator_review.html:" in captured.out


def test_cli_operator_status_prints_next_step(tmp_path, monkeypatch, capsys):
    def fake_build_operator_status(job_dir):
        return {
            "job_dir": str(job_dir),
            "overall": "IN_PROGRESS",
            "next_step": "Generate scenes.",
            "artifacts": {
                "script": {"artifact": "present", "qa": "PASS"},
                "scenes": {"artifact": "missing", "qa": "missing"},
                "seo": {"artifact": "missing", "qa": "missing"},
            },
        }

    monkeypatch.setattr("video_agent.cli.build_operator_status", fake_build_operator_status)

    exit_code = main(["operator-status", "--job-dir", str(tmp_path / "operator-job")])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Overall: IN_PROGRESS" in captured.out
    assert "script: artifact=present qa=PASS" in captured.out
    assert "Next: Generate scenes." in captured.out


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
    assert "| Job | Topic | Video | Visual QA | Score range | Contact sheet | Source mix | Provider mix | Searched providers |" in audit
    assert "pexels" in audit or "generated_placeholder" in audit
    assert "visual_contact_sheet.jpg" in audit
    job_lines = [line for line in audit.splitlines() if line.startswith("| `")]
    assert len(job_lines) == 2


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


def test_cli_audit_reports_score_range_and_searched_providers(tmp_path, capsys):
    job_dir = tmp_path / "jobs" / "job-with-stock"
    job_dir.mkdir(parents=True)
    (job_dir / "render_props.json").write_text(
        '{"seo": {"title": "Stock test"}}',
        encoding="utf-8",
    )
    (job_dir / "visual_review.json").write_text(
        """
{
  "job_id": "job-with-stock",
  "contact_sheet": "visual_contact_sheet.jpg",
  "summary": {
    "total_scenes": 2,
    "by_source": {"asset_library": 2},
    "by_provider": {"pexels": 1, "pixabay": 1},
    "selection_scores": {"min": 60, "avg": 71.0, "max": 82},
    "searched_providers": {"pexels": 2, "pixabay": 2}
  },
  "qa": {"status": "PASS", "issue_count": 0},
  "scenes": []
}
""".strip(),
        encoding="utf-8",
    )

    exit_code = main(["audit", "--job", str(job_dir)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "| `job-with-stock` | Stock test | skipped | PASS | 60-82 (avg 71.0) |" in captured.out
    assert "| 2 asset_library | 1 pexels, 1 pixabay | 2 pexels, 2 pixabay |" in captured.out
