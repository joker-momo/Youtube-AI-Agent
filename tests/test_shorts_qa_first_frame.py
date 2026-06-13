from __future__ import annotations

import json
from pathlib import Path


def test_scenes_qa_warns_when_first_scene_missing_first_frame_plan_and_crop(tmp_path: Path):
    from video_agent.shorts import paths
    from video_agent.shorts.qa import run_short_scenes_qa

    short_id = "short-01"
    jd = paths.short_json_dir(tmp_path, short_id)
    jd.mkdir(parents=True)
    (jd / paths.SHORT_SCRIPT_FILE).write_text(
        json.dumps({"hook": "No mires el color.", "narration": "No mires el color. Mira la etiqueta."}),
        encoding="utf-8",
    )
    (jd / paths.SHORT_SCENES_FILE).write_text(
        json.dumps(
            {
                "total_duration_sec": 24,
                "scenes": [
                    {
                        "id": "s01",
                        "layout": "short_hook",
                        "duration_sec": 3,
                        "motion": "push_in",
                        "narration": "No mires el color.",
                        "on_screen_text": "NO MIRES COLOR",
                        "visual_prompt": "wide smiling person holding bread in supermarket",
                    },
                    {
                        "id": "s02",
                        "layout": "short_tip",
                        "duration_sec": 21,
                        "motion": "crop_shift",
                        "narration": "Mira el primer ingrediente.",
                        "on_screen_text": "MIRA ETIQUETA",
                        "visual_prompt": "close up bread label",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    qa = run_short_scenes_qa(tmp_path, short_id, {"shorts": {"duration": {"min_sec": 1, "target_max_sec": 60}}})

    assert "first_scene_missing_first_frame_plan" in qa["warnings"]
    assert "first_scene_missing_crop_plan" in qa["warnings"]
    assert "first_scene_generic_stock_risk" in qa["warnings"]
