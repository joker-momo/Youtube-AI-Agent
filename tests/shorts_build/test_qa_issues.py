from video_agent.shorts import qa

def test_suppress_stale_hook_fidelity():
    script = {
        "planner_warnings": ["stale_hook_text_repaired"]
    }
    
    issue = {
        "type": "idea_fidelity",
        "severity": "minor",
        "detail": "unrelated bread hook"
    }
    
    normalized = qa.normalize_qa_issue(
        issue,
        idea={},
        script=script,
        scenes={}
    )
    
    assert normalized.issue_class == qa.IssueClass.STALE_OR_SUPPRESSED
    assert normalized.reason == "wrong_context_suppressed"


def test_repairable_point_grouping():
    issue = {
        "type": "idea_fidelity",
        "severity": "major",
        "detail": "Se agrupan 'siéntate cómodo' y 'suelta la mandíbula' en un solo bloque temporal."
    }
    
    normalized = qa.normalize_qa_issue(
        issue,
        idea={},
        script={},
        scenes={}
    )
    
    assert normalized.issue_class == qa.IssueClass.REPAIRABLE_BLOCKER
    assert normalized.reason == "repairable_point_grouping"


def test_repairable_audio_fit():
    issue = {
        "type": "style",
        "severity": "major",
        "detail": "The script contains 74 words in Spanish... audio-fit impossible."
    }
    
    normalized = qa.normalize_qa_issue(
        issue,
        idea={},
        script={},
        scenes={}
    )
    
    assert normalized.issue_class == qa.IssueClass.REPAIRABLE_BLOCKER
    assert normalized.reason == "repairable_audio_fit"


def test_repairable_audio_fit_structure():
    issue = {
        "type": "structure",
        "severity": "major",
        "detail": "The script contains 74 words in Spanish... audio-fit impossible."
    }
    
    normalized = qa.normalize_qa_issue(
        issue,
        idea={},
        script={},
        scenes={}
    )
    
    assert normalized.issue_class == qa.IssueClass.REPAIRABLE_BLOCKER
    assert normalized.reason == "repairable_audio_fit"


def test_audio_fit_within_deterministic_budget_is_soft():
    # Regression: LLM judge claims an audio-fit problem but the narration is
    # within the deterministic budget (35s -> 72 words). The deterministic gate
    # is authoritative, so this must downgrade to a soft warning and NOT trigger
    # a regeneration loop. Previously this caused a non-converging 5-attempt
    # failure reported as "not_generated".
    issue = {
        "type": "style",
        "severity": "major",
        "detail": "The script contains 77 words... audio-fit rushed, needs 42-45s.",
    }
    script = {
        "target_duration_sec": 35,
        "beats": [{"narration": " ".join(["palabra"] * 65)}],
    }
    normalized = qa.normalize_qa_issue(
        issue, idea={"target_duration_sec": 35}, script=script, scenes={}
    )
    assert normalized.issue_class == qa.IssueClass.SOFT_WARNING
    assert normalized.reason == "audio_fit_within_deterministic_budget"
    assert normalized.trigger_regeneration is False


def test_audio_fit_over_deterministic_budget_still_repairable():
    # Counterpart guard: when narration genuinely exceeds the budget, the
    # audio-fit issue stays a repairable blocker that triggers regeneration.
    issue = {
        "type": "style",
        "severity": "major",
        "detail": "The script word count is too high for the speaking time.",
    }
    script = {
        "target_duration_sec": 35,
        "beats": [{"narration": " ".join(["palabra"] * 85)}],
    }
    normalized = qa.normalize_qa_issue(
        issue, idea={"target_duration_sec": 35}, script=script, scenes={}
    )
    assert normalized.issue_class == qa.IssueClass.REPAIRABLE_BLOCKER
    assert normalized.reason == "repairable_audio_fit"
    assert normalized.trigger_regeneration is True


def test_graphic_duration_at_hard_max_is_soft():
    # Regression: a graphic_routine_split scene sitting exactly at the 5.0s hard
    # max is ALLOWED by the deterministic rule (dur > hard_max). The LLM judge
    # hard-blocked the boundary value, causing a non-converging scene-QA loop.
    # Must downgrade to a soft warning when within the deterministic hard max.
    scenes = {"scenes": [{"id": "s04", "layout": "graphic_routine_split", "duration_sec": 5.0}]}
    issue = {
        "type": "duration",
        "severity": "major",
        "detail": "La escena s04 utiliza un diseño gráfico (graphic_routine_split) con una duración de 5.0 segundos. Ninguna escena gráfica puede durar más de 5.0 segundos.",
    }
    normalized = qa.normalize_qa_issue(issue, idea={}, script={}, scenes=scenes, source="scene_qa")
    assert normalized.issue_class == qa.IssueClass.SOFT_WARNING
    assert normalized.trigger_regeneration is False


def test_graphic_duration_over_hard_max_still_blocks():
    # Counterpart guard: a graphic scene genuinely over its hard max stays a
    # blocker (the deterministic rule would raise), so the judge's complaint holds.
    scenes = {"scenes": [{"id": "s04", "layout": "graphic_routine_split", "duration_sec": 6.5}]}
    issue = {
        "type": "duration",
        "severity": "major",
        "detail": "La escena s04 (graphic_routine_split) dura 6.5 segundos, excede el máximo.",
    }
    normalized = qa.normalize_qa_issue(issue, idea={}, script={}, scenes=scenes, source="scene_qa")
    assert normalized.issue_class != qa.IssueClass.SOFT_WARNING
    assert normalized.trigger_regeneration is True


def test_non_graphic_duration_issue_unaffected():
    # A duration complaint on a non-graphic layout must not be downgraded by the
    # graphic-duration guard.
    scenes = {"scenes": [{"id": "s06", "layout": "short_quote", "duration_sec": 5.5}]}
    issue = {
        "type": "duration",
        "severity": "major",
        "detail": "La escena s06 dura 5.5 segundos, ralentiza el ritmo final.",
    }
    normalized = qa.normalize_qa_issue(issue, idea={}, script={}, scenes=scenes, source="scene_qa")
    assert normalized.issue_class != qa.IssueClass.SOFT_WARNING


def test_has_hard_fail_minor():
    from video_agent.shorts.builder.qa_gate import has_hard_fail
    
    result = {
        "verdict": "FAIL",
        "issues": [
            {
                "type": "idea_fidelity",
                "severity": "minor",
                "detail": "The script announces five limits but lists them differently."
            }
        ]
    }
    
    # Even though "idea_fidelity" has "idea" (which is a hard marker),
    # since severity is "minor", has_hard_fail should return False.
    assert has_hard_fail(result) is False


def test_has_hard_fail_major():
    from video_agent.shorts.builder.qa_gate import has_hard_fail
    
    result = {
        "verdict": "FAIL",
        "issues": [
            {
                "type": "idea_fidelity",
                "severity": "major",
                "detail": "The script announces five limits but lists them differently."
            }
        ]
    }
    
    assert has_hard_fail(result) is True


def test_scene_qa_duration_compression_source_fidelity_is_soft():
    from video_agent.shorts.builder.qa_gate import _scene_qa_has_hard_fail

    result = {
        "verdict": "FAIL",
        "issues": [
            {
                "type": "source_fidelity",
                "severity": "major",
                "detail": (
                    "The original SCRIPT has a target duration of 45 seconds and "
                    "the narration in the generated scenes has been heavily truncated."
                ),
            }
        ],
    }

    assert _scene_qa_has_hard_fail(result) is False


def test_scene_qa_real_source_fidelity_remains_hard():
    from video_agent.shorts.builder.qa_gate import _scene_qa_has_hard_fail

    result = {
        "verdict": "FAIL",
        "issues": [
            {
                "type": "source_fidelity",
                "severity": "major",
                "detail": "Scene adds unsupported diagnosis and changes the health claim.",
            }
        ],
    }

    assert _scene_qa_has_hard_fail(result) is True


def test_scene_qa_product_quality_major_is_soft():
    from video_agent.shorts.builder.qa_gate import _scene_qa_has_hard_fail

    result = {
        "verdict": "FAIL",
        "issues": [
            {
                "type": "product_quality_score_low",
                "severity": "major",
                "detail": "hook_strength is 8.0 and should be improved.",
            }
        ],
    }

    assert _scene_qa_has_hard_fail(result) is False


def test_scene_qa_duration_under_target_is_soft_after_deterministic_pass():
    from video_agent.shorts.qa import IssueClass, normalize_qa_issue

    issue = {
        "type": "duration",
        "severity": "major",
        "detail": (
            "The total sequence duration (29.2 seconds) underperforms the script's "
            "target_duration_sec (45 seconds) by a significant margin."
        ),
    }

    normalized = normalize_qa_issue(
        issue,
        idea={},
        script={"target_duration_sec": 45},
        scenes={},
        source="gemini_scene_qa",
    )

    assert normalized.issue_class == IssueClass.SOFT_WARNING
    assert normalized.trigger_regeneration is False


def test_scene_qa_truncated_layout_fidelity_is_soft_after_deterministic_pass():
    from video_agent.shorts.qa import IssueClass, normalize_qa_issue

    issues = [
        {
            "type": "source_fidelity",
            "scene_id": "s01",
            "severity": "major",
            "detail": (
                "The script specifies the hook text and identity line as 'BAJA LA CARGA'. "
                "Scene s01 drops the core identity concept entirely and truncates the narration seed."
            ),
        },
        {
            "type": "source_fidelity",
            "scene_id": "s07",
            "severity": "major",
            "detail": (
                "Scene s07 uses layout 'short_quote' but the script provides an explicit "
                "structural list of 5 key points. Using a stock quote layout here compresses "
                "the core practical tips."
            ),
        },
    ]

    for issue in issues:
        normalized = normalize_qa_issue(
            issue,
            idea={},
            script={"target_duration_sec": 45},
            scenes={},
            source="gemini_scene_qa",
        )
        assert normalized.issue_class == IssueClass.SOFT_WARNING
        assert normalized.trigger_regeneration is False


def test_scene_qa_product_quality_score_six_is_soft_terminal_warning():
    from video_agent.shorts.qa import IssueClass, normalize_qa_issue

    issue = {
        "type": "product_quality_score_low",
        "severity": "major",
        "detail": (
            "Some product quality scores are below their required thresholds: "
            "{'retention_pacing': 6.0}. Required: {'retention_pacing': 9.0}. "
            "Hint: Improve the weak product-quality dimensions while preserving safety."
        ),
    }

    normalized = normalize_qa_issue(
        issue,
        idea={},
        script={},
        scenes={},
        source="gemini_scene_qa",
    )

    assert normalized.issue_class == IssueClass.SOFT_WARNING
    assert normalized.trigger_regeneration is False
