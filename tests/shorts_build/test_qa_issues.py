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
