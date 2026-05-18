from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.contracts import ARTIFACT_SCRIPT
from video_agent.qa.script_qa import check_script
from video_agent.utils.json_io import write_json


def run_script_stage(provider: Any, channel_config: dict[str, Any], idea: dict[str, Any], job_id: str, job_dir: Path) -> dict[str, Any]:
    script = provider.generate_script(channel_config, idea, job_id)
    iterations = []
    for iteration in range(1, 4):
        qa = check_script(script, channel_config)
        iterations.append({"iteration": iteration, **qa})
        if qa["verdict"] == "PASS":
            script["qa"] = {"verdict": "PASS", "iterations": iterations}
            write_json(job_dir / ARTIFACT_SCRIPT, script)
            return script
        if qa["retry_action"] == "add_disclaimer":
            script["narration"] += " Este contenido es educativo y no reemplaza el consejo de un profesional de salud."
    script["qa"] = {"verdict": "FAIL", "iterations": iterations}
    write_json(job_dir / ARTIFACT_SCRIPT, script)
    raise RuntimeError("Script QA failed after 3 iterations.")
