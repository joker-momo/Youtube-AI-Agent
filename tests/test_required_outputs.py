from pathlib import Path

from video_agent.pipeline import PipelineOptions, run_pipeline

ROOT = Path(__file__).resolve().parents[1]


def test_required_outputs_exist_without_render(tmp_path):
    result = run_pipeline(
        PipelineOptions(
            channel_path=ROOT / "configs/vida-plena-45/channel.yaml",
            idea_path=ROOT / "inputs/manual_idea.json",
            jobs_dir=tmp_path,
            render=False,
        )
    )
    required = ["thumbnail.jpg", "seo.json", "report.md", "render_props.json", "script.json", "scenes.json"]
    for filename in required:
        path = result.job_dir / filename
        assert path.exists(), filename
        assert path.stat().st_size > 0, filename
