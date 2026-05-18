from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.contracts import ARTIFACT_SCENES
from video_agent.qa.scene_qa import check_scenes
from video_agent.utils.json_io import write_json


def run_scene_stage(
    provider: Any,
    channel_config: dict[str, Any],
    idea: dict[str, Any],
    script: dict[str, Any],
    job_id: str,
    job_dir: Path,
) -> dict[str, Any]:
    scene_doc = provider.generate_scenes(channel_config, idea, script, job_id)
    iterations = []
    for iteration in range(1, 4):
        qa = check_scenes(scene_doc, channel_config)
        iterations.append({"iteration": iteration, **qa})
        if qa["verdict"] == "PASS":
            scene_doc["qa"] = {"verdict": "PASS", "iterations": iterations}
            write_json(job_dir / ARTIFACT_SCENES, scene_doc)
            return scene_doc
    scene_doc["qa"] = {"verdict": "FAIL", "iterations": iterations}
    write_json(job_dir / ARTIFACT_SCENES, scene_doc)
    raise RuntimeError("Scene QA failed after 3 iterations.")
