from __future__ import annotations


def _nutrition_plan() -> dict:
    return {
        "short_id": "short-01",
        "title": "Pan integral falso",
        "format": "myth_or_contradiction",
        "hook_angle": "El pan marron no siempre es integral",
        "viewer_pain": "comprar pan creyendo que es mas sano",
        "curiosity_gap": "el detalle esta en el primer ingrediente",
        "identity_angle": "personas que compran con prisa en el supermercado",
        "topic_family": "nutrition",
        "key_points": [
            {"point": "El primer ingrediente delata si el pan es integral."},
        ],
    }


def test_build_hook_lab_generates_scores_and_selects_safe_hook():
    from video_agent.shorts.hook_lab import build_hook_lab

    result = build_hook_lab(_nutrition_plan(), {}, {}, {})

    assert result["selected_hook"]
    assert result["selected_hook"] in {item["hook"] for item in result["candidates"]}
    assert result["selected_hook_type"]
    assert len(result["candidates"]) >= 8
    assert all("score" in item for item in result["candidates"])
    assert not any(
        phrase in result["selected_hook"].lower()
        for phrase in (
            "esto te esta matando",
            "esto te está matando",
            "la industria no quiere",
            "milagro",
            "secreto que nadie",
        )
    )


def test_build_hook_lab_is_deterministic_without_llm_access():
    from video_agent.shorts.hook_lab import build_hook_lab

    first = build_hook_lab(_nutrition_plan(), {}, {}, {})
    second = build_hook_lab(_nutrition_plan(), {}, {}, {})

    assert first["selected_hook"] == second["selected_hook"]
    assert [item["hook"] for item in first["candidates"]] == [
        item["hook"] for item in second["candidates"]
    ]
