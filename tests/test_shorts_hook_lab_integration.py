from __future__ import annotations

import json


def test_build_short_script_injects_hook_lab_selection(tmp_path):
    from video_agent.shorts.short_script_builder import build_short_script

    prompts_seen: list[str] = []

    def fake_llm(prompt: str) -> str:
        prompts_seen.append(prompt)
        return json.dumps(
            {
                "short_id": "short-01",
                "source_long_job_id": None,
                "short_format": "myth_or_contradiction",
                "target_duration_sec": 35,
                "hook": "No mires el color. Mira el primer ingrediente.",
                "narration": "No mires el color. Mira el primer ingrediente.",
                "beats": [],
                "cta": "Guarda esto.",
                "qa": {"verdict": "PENDING_SHORTS_QA"},
            }
        )

    short_plan = {
        "short_id": "short-01",
        "title": "Pan integral falso",
        "format": "myth_or_contradiction",
        "hook_angle": "El pan marron no siempre es integral",
        "viewer_pain": "comprar pan creyendo que es mas sano",
        "curiosity_gap": "el detalle esta en el primer ingrediente",
        "topic_family": "nutrition",
        "key_points": [{"point": "El primer ingrediente importa."}],
    }

    script = build_short_script(tmp_path, short_plan, {"shorts": {}}, fake_llm)

    assert short_plan["hook_text"]
    assert short_plan["hook_lab"]["selected_hook"] == short_plan["hook_text"]
    assert short_plan["hook_text"] in prompts_seen[0]
    assert "Use the selected hook_text as the script hook" in prompts_seen[0]
    assert script["hook"] == "No mires el color. Mira el primer ingrediente."
