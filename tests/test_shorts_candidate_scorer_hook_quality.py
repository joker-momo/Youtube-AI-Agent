from __future__ import annotations


def test_score_hook_candidate_formula_and_rejections():
    from video_agent.shorts.candidate_scorer import score_hook_candidate

    candidate = {
        "hook": "No mires el color. Mira el primer ingrediente.",
        "hook_type": "proof_first",
        "clarity_2s": 9,
        "curiosity_gap": 8,
        "emotional_tension": 7,
        "trust_fit_45plus": 9,
        "clickbait_risk": 2,
        "source_fidelity": 9,
    }

    scored = score_hook_candidate(candidate)

    assert scored["score"] == 7.9
    assert scored["reject"] is False
    assert scored["reject_reason"] == ""

    clickbait = score_hook_candidate({**candidate, "clickbait_risk": 8})
    assert clickbait["reject"] is True
    assert clickbait["reject_reason"] == "clickbait_risk"

    unsupported = score_hook_candidate({**candidate, "source_fidelity": 6})
    assert unsupported["reject"] is True
    assert unsupported["reject_reason"] == "low_source_fidelity"

    unclear = score_hook_candidate({**candidate, "clarity_2s": 5})
    assert unclear["reject"] is True
    assert unclear["reject_reason"] == "low_clarity_2s"


def test_score_candidate_still_scores_whole_short_ideas():
    from video_agent.shorts.candidate_scorer import score_candidate

    out = score_candidate(
        {
            "narration": "¿Te cuesta elegir pan integral? Mira la etiqueta y evita este error.",
            "visual_prompt": "close up bread package label vertical",
        },
        {},
    )

    assert "final_score" in out
    assert "tier" in out
    assert "score" not in out
