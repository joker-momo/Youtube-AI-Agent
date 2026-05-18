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
