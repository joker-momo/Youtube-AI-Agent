from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CHANNEL_VIDEO = ROOT / "remotion" / "src" / "ChannelVideo.tsx"
SHORT_LAYOUT_CONSTANTS = ROOT / "remotion" / "src" / "shorts" / "ShortLayoutConstants.ts"


def _source() -> str:
    return CHANNEL_VIDEO.read_text(encoding="utf-8")


def _short_constants_source() -> str:
    return SHORT_LAYOUT_CONSTANTS.read_text(encoding="utf-8")


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
    # SceneLayout now includes short_* values; long-form ChannelVideo casts
    # to keep the registry tolerant of new short layout names.
    assert (
        "layoutTemplates as any)[layout]" in source
        or "const Template = layoutTemplates[layout]" in source
    )


def test_retention_templates_do_not_invent_script_text():
    source = _source()

    assert "Comienza hoy" not in source
    assert "ATENCION" not in source


def test_short_layout_constants_use_10_percent_horizontal_safe_zone():
    source = _short_constants_source()

    assert "safeX: 108" in source
    assert "hookZone: {yMin: 360, yMax: 850, width: 864}" in source
    assert "bodyZone: {yMin: 860, yMax: 1250, width: 820}" in source
    assert "captionZone: {yMin: 1100, yMax: 1380, width: 800}" in source
    assert "ctaZone: {yMin: 1180, yMax: 1420, width: 800}" in source


def test_short_layout_constants_use_montserrat_for_all_text_roles():
    source = _short_constants_source()

    assert "HOOK_FONT_FAMILY = 'Montserrat" in source
    assert "BODY_FONT_FAMILY = 'Montserrat" in source
    assert "CAPTION_FONT_FAMILY = 'Montserrat" in source
