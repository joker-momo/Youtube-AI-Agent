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
    assert (result.job_dir / "visual_review.json").exists()
    assert (result.job_dir / "render_props.json").exists()
    assert (result.job_dir / "seo.json").exists()
    assert (result.job_dir / "thumbnail.jpg").exists()
    assert (result.job_dir / "report.md").exists()
    render_props = read_json(result.job_dir / "render_props.json")
    assert render_props["channel"]["id"] == "vida-plena-45"
    assert len(render_props["scenes"]) == 5
    visual_review = read_json(result.job_dir / "visual_review.json")
    assert visual_review["job_id"] == result.job_id
    assert len(visual_review["scenes"]) == 5
    assert visual_review["summary"]["total_scenes"] == 5
    assert visual_review["qa"]["status"] == "WARN"
    scene_issue_count = sum(len(scene["qa"]["issues"]) for scene in visual_review["scenes"])
    assert visual_review["qa"]["issue_count"] == scene_issue_count
    assert any(
        issue["type"] == "PLACEHOLDER_USED"
        for scene in visual_review["scenes"]
        for issue in scene["qa"]["issues"]
    )
    report = (result.job_dir / "report.md").read_text(encoding="utf-8")
    assert "Visual Review" in report
    assert "Visual QA: WARN" in report
