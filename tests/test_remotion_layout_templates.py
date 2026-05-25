from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CHANNEL_VIDEO = ROOT / "remotion" / "src" / "ChannelVideo.tsx"


def _source() -> str:
    return CHANNEL_VIDEO.read_text(encoding="utf-8")


def test_channel_video_has_template_component_for_each_retention_layout():
    source = _source()

    for component in (
        "HookOverlay",
        "SubtitleOverlay",
        "ChecklistOverlay",
        "WarningOverlay",
        "QuoteOverlay",
        "CtaOverlay",
    ):
        assert re.search(rf"const {component}: React\.FC<", source), component


def test_retention_overlay_dispatches_through_layout_template_registry():
    source = _source()

    assert "const layoutTemplates: Record<RetentionLayout, React.FC<RetentionTemplateProps>>" in source
    assert "const Template = layoutTemplates[layout]" in source


def test_retention_templates_do_not_invent_script_text():
    source = _source()

    assert "Comienza hoy" not in source
    assert "ATENCION" not in source
