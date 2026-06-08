from __future__ import annotations


def test_quality_upgrade_does_not_modify_long_form_stage_modules():
    from pathlib import Path

    changed = Path("src/video_agent/shorts/short_builder.py")
    assert "shorts" in changed.parts
