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


def test_count_promise_soft_when_count_not_locked():
    # Judge complains the title promises '5 ajustes' but the script collapses to
    # 4 items. With an UNLOCKED count (must_preserve_count false) this is an
    # allowed adaptation, so it must be a soft warning, not a regen-looping
    # blocker (bug-360).
    issue = {
        "type": "idea_fidelity",
        "severity": "major",
        "detail": (
            "El título promete '5 ajustes' pero el guion agrupa, colapsando el "
            "punto 3 y 4, rompiendo la promesa numérica implícita del gancho."
        ),
    }
    normalized = qa.normalize_qa_issue(
        issue,
        idea={},
        script={"idea_contract": {"must_preserve_count": False}},
        scenes={},
    )
    assert normalized.issue_class == qa.IssueClass.SOFT_WARNING
    assert normalized.reason == "count_not_locked_grouping_soft"


def test_count_promise_repairable_when_count_locked():
    # Same complaint but with a LOCKED count must stay a repairable blocker so the
    # promised count is actually restored.
    issue = {
        "type": "idea_fidelity",
        "severity": "major",
        "detail": (
            "El guion agrupa y colapsa puntos, rompiendo la promesa numérica de "
            "'5 ajustes'."
        ),
    }
    normalized = qa.normalize_qa_issue(
        issue,
        idea={},
        script={"idea_contract": {"must_preserve_count": True}},
        scenes={},
    )
    assert normalized.issue_class == qa.IssueClass.REPAIRABLE_BLOCKER


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


def test_spanish_audio_fit_style_complaint_within_budget_is_soft():
    issue = {
        "type": "style",
        "severity": "major",
        "detail": (
            "El guion contiene 76 palabras para una duración de 35 segundos; "
            "las pausas naturales harán que la locución resulte acelerada."
        ),
    }
    script = {
        "target_duration_sec": 35,
        "narration": " ".join(["palabra"] * 70),
    }

    normalized = qa.normalize_qa_issue(
        issue, idea={"target_duration_sec": 35}, script=script, scenes={}
    )

    assert normalized.issue_class == qa.IssueClass.SOFT_WARNING
    assert normalized.reason == "audio_fit_within_deterministic_budget"
    assert normalized.trigger_regeneration is False


def test_selected_hook_title_drop_complaint_is_soft_when_contract_used():
    issue = {
        "type": "hook",
        "severity": "major",
        "detail": (
            "The script does not open with pain, curiosity, a number, or a common mistake "
            "within the first 2 seconds. Starting with 'TU MANO AYUDA' functions as a "
            "title drop rather than an immediate hook."
        ),
    }
    script = {
        "hook": "TU MANO AYUDA",
        "narration": "TU MANO AYUDA. ¿Repites pan por costumbre? Decide antes.",
    }

    normalized = qa.normalize_qa_issue(
        issue,
        idea={"hook_text": "TU MANO AYUDA"},
        script=script,
        scenes={},
        source="script_qa",
    )

    assert normalized.issue_class == qa.IssueClass.SOFT_WARNING
    assert normalized.reason == "selected_hook_contract_used"
    assert normalized.trigger_regeneration is False


def test_selected_hook_complaint_soft_for_hyphenated_paraphrase():
    """Real judge wording — hyphenated 'first-2-seconds' and 'doesn't open' — must
    still downgrade to soft when the contracted hook_text was used, instead of
    hard-blocking the build for 5 attempts (regression for the dead-end loop)."""
    issue = {
        "type": "hook",
        "severity": "major",
        "detail": (
            "The hook 'TU MANO AYUDA' fails the first-2-seconds rule. It doesn't open "
            "with a clear pain point, curiosity gap, a number, or a common mistake. It "
            "is too abstract for an audience aged 45+ looking for practical guidance."
        ),
    }
    script = {
        "hook": "TU MANO AYUDA",
        "narration": "TU MANO AYUDA. ¿Repites pan por costumbre? Decide antes.",
    }

    normalized = qa.normalize_qa_issue(
        issue,
        idea={"hook_text": "TU MANO AYUDA"},
        script=script,
        scenes={},
        source="script_qa",
    )

    assert normalized.issue_class == qa.IssueClass.SOFT_WARNING
    assert normalized.reason == "selected_hook_contract_used"
    assert normalized.trigger_regeneration is False


def test_audio_fit_required_change_is_soft_when_budget_passes():
    issue = (
        "Micro-compress the narration wording across the 5 checklist items to allow "
        "breathing room within the 35-second target without losing the exact 5-point "
        "contract structure."
    )
    script = {
        "target_duration_sec": 35,
        "narration": " ".join(["palabra"] * 68),
    }

    normalized = qa.normalize_qa_issue(
        issue, idea={"target_duration_sec": 35}, script=script, scenes={}, source="script_qa"
    )

    assert normalized.issue_class == qa.IssueClass.SOFT_WARNING
    assert normalized.reason == "audio_fit_within_deterministic_budget"
    assert normalized.trigger_regeneration is False


def test_gemini_strict_duration_inside_deterministic_contract_is_soft():
    issue = {
        "type": "duration",
        "severity": "major",
        "detail": "Total duration is 30.6 seconds, which exceeds the strict 26.0–30.0s window.",
    }
    scenes = {
        "total_duration_sec": 30.6,
        "scenes": [
            {"id": "s01", "duration_sec": 3.0},
            {"id": "s02", "duration_sec": 27.6},
        ],
    }

    normalized = qa.normalize_qa_issue(
        issue, idea={}, script={}, scenes=scenes, source="gemini_scene_qa"
    )

    assert normalized.issue_class == qa.IssueClass.SOFT_WARNING
    assert normalized.reason == "duration_within_deterministic_contract"
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


def test_gemini_fabricated_step_word_limit_is_soft_when_within_char_caps():
    # Gemini invents an undocumented "under 3 words per step" rule and hard-blocks
    # valid 3-word steps (the prompt's own example uses 3-word steps). When every
    # step is within the deterministic char cap, the judge opinion is advisory and
    # must not loop scene-QA regeneration. (short-08/idea-07 s05 production loop.)
    scenes = {
        "scenes": [
            {
                "id": "s05",
                "layout": "graphic_step_list",
                "duration_sec": 3.8,
                "layout_payload": {
                    "title": "EN 3 PASOS",
                    "steps": [
                        {"label": "1", "text": "Decide la porción"},
                        {"label": "2", "text": "Córtala"},
                        {"label": "3", "text": "Guarda el resto"},
                    ],
                },
            }
        ]
    }
    issue = {
        "type": "graphic",
        "severity": "major",
        "scene_id": "s05",
        "detail": (
            "The layout_payload.steps array contains 3 elements, but each step's "
            "text attribute is too long for an on-screen graphic graphic_step_list "
            "step block. Keep the short text under 3 words per step."
        ),
    }

    normalized = qa.normalize_qa_issue(
        issue, idea={}, script={}, scenes=scenes, source="gemini_scene_qa"
    )

    assert normalized.issue_class == qa.IssueClass.SOFT_WARNING
    assert normalized.reason == "graphic_payload_text_within_deterministic_limits"
    assert normalized.trigger_regeneration is False


def test_genuinely_long_step_text_still_blocks():
    # Counterpart guard: a step that actually exceeds the deterministic char cap
    # (_STEP_TEXT_MAX=56) is NOT downgraded — the length complaint is legitimate.
    long_text = "Decide la porción correcta antes de comer y guarda siempre el resto"
    assert len(long_text) > 56
    scenes = {
        "scenes": [
            {
                "id": "s05",
                "layout": "graphic_step_list",
                "duration_sec": 3.8,
                "layout_payload": {
                    "title": "EN 3 PASOS",
                    "steps": [
                        {"label": "1", "text": long_text},
                        {"label": "2", "text": "Córtala"},
                    ],
                },
            }
        ]
    }
    issue = {
        "type": "graphic",
        "severity": "major",
        "scene_id": "s05",
        "detail": "Each step's text is too long for an on-screen graphic_step_list block.",
    }

    normalized = qa.normalize_qa_issue(
        issue, idea={}, script={}, scenes=scenes, source="gemini_scene_qa"
    )

    assert normalized.reason != "graphic_payload_text_within_deterministic_limits"
    assert normalized.issue_class != qa.IssueClass.SOFT_WARNING


def test_five_error_label_rule_suppressed_on_non_five_errors_bread_short():
    # Gemini over-applies the dedicated "5-error bread" label rule (DE PIE /
    # SUMAR SIN DECIDIR / ...) to any bread Short because the prompt trigger
    # includes "pan". On a portion-control checklist Short (not a 5-errores
    # Short) demanding label "SUMAR SIN DECIDIR" is wrong-context and must be
    # suppressed, not hard-block + loop. (short-08/idea-07 s02 production loop.)
    idea = {"title": "La porción de pan sin contar gramos", "format": "checklist"}
    detail = (
        "The on_screen_text is too long (3 words, but checks against the 5-error "
        "bread rule specific labels). According to the special bread short rules, "
        "generic or non-specific labels must be replaced with uppercase specific "
        "labels. For the second scene, it should use 'SUMAR SIN DECIDIR'."
    )
    issue = {"type": "graphic", "severity": "major", "scene_id": "s02", "detail": detail}
    scenes = {"scenes": [{"id": "s02", "layout": "short_tip"}]}

    normalized = qa.normalize_qa_issue(
        issue, idea=idea, script={}, scenes=scenes, source="gemini_scene_qa"
    )

    assert normalized.issue_class == qa.IssueClass.STALE_OR_SUPPRESSED
    assert normalized.reason == "wrong_context_five_errors_rule"
    assert normalized.trigger_regeneration is False


def test_five_error_label_rule_still_enforced_on_real_five_errors_short():
    # Counterpart guard: on the genuine "5 errores al comer pan" Short the label
    # rule must still hard-block — suppression is context-gated, not blanket.
    idea = {"title": "5 errores al comer pan", "format": "mistakes", "original_count": 5}
    detail = (
        "Generic labels must be replaced with uppercase specific labels. The second "
        "scene should use 'SUMAR SIN DECIDIR'."
    )
    issue = {"type": "graphic", "severity": "major", "scene_id": "s02", "detail": detail}
    scenes = {"scenes": [{"id": "s02", "layout": "short_tip"}]}

    normalized = qa.normalize_qa_issue(
        issue, idea=idea, script={}, scenes=scenes, source="gemini_scene_qa"
    )

    assert normalized.reason != "wrong_context_five_errors_rule"
    assert normalized.issue_class != qa.IssueClass.STALE_OR_SUPPRESSED
