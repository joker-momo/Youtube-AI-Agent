from pathlib import Path

from video_agent.pipeline import PipelineOptions, run_pipeline
from video_agent.utils.json_io import read_json

ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_writes_structured_artifacts_without_render(tmp_path):
    result = run_pipeline(
        PipelineOptions(
            channel_path=ROOT / "configs/vida-plena-45/channel.yaml",
            idea_path=ROOT / "inputs/manual_idea.json",
            jobs_dir=tmp_path,
            render=False,
        )
    )
    assert result.video_path is None
    assert (result.job_dir / "script.json").exists()
    assert (result.job_dir / "scenes.json").exists()
    assert (result.job_dir / "assets_manifest.json").exists()
    assert (result.job_dir / "render_props.json").exists()
    assert (result.job_dir / "seo.json").exists()
    assert (result.job_dir / "thumbnail.jpg").exists()
    assert (result.job_dir / "report.md").exists()
    render_props = read_json(result.job_dir / "render_props.json")
    assert render_props["channel"]["id"] == "vida-plena-45"
    assert len(render_props["scenes"]) == 5
