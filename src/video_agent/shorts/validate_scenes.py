"""Pre-render validation for Shorts graphic scenes (spec v7 §18).

Runs after ``build_short_scenes`` + ``run_short_scenes_qa`` and before render
props are written / Remotion is invoked. Catches unsupported graphic layouts and
malformed payloads early, with clear errors, so bad scenes never reach the
renderer. Mirrors the Zod checks in ``remotion/src/graphics/graphic-payloads.ts``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re
import wave
from pathlib import Path
from typing import Any

DEFAULT_SPANISH_WPS = 2.25
AUDIO_TAIL_MARGIN_SEC = 0.6
MIN_SHORT_DURATION_SEC = 20.0
MAX_SHORT_DURATION_SEC = 45.0
IDEAL_MIN_SHORT_DURATION_SEC = 28.0
IDEAL_MAX_SHORT_DURATION_SEC = 38.0
GLOBAL_SCENE_MAX_SEC = 5.5

SUPPORTED_GRAPHIC_LAYOUTS = {
    "graphic_plate_ratio",
    "graphic_checklist",
    "graphic_step_list",
    "graphic_label_callout",
    "graphic_comparison",
    "graphic_routine_split",
}

SUPPORTED_SHORT_LAYOUTS = {
    "short_hook",
    "short_pain",
    "short_tip",
    "short_checklist",
    "short_myth",
    "short_quote",
    "short_cta",
}

SUPPORTED_SCENE_LAYOUTS = SUPPORTED_SHORT_LAYOUTS | SUPPORTED_GRAPHIC_LAYOUTS

LAYOUT_DURATION_TARGETS = {
    "short_hook": (1.8, 2.8, 3.0),
    "short_myth": (2.0, 3.0, 3.2),
    "short_tip": (2.2, 4.2, 5.0),
    "short_checklist": (3.0, 4.5, 5.0),
    "short_pain": (2.0, 3.8, 4.5),
    "short_cta": (1.8, 2.6, 2.8),
    "graphic_checklist": (3.0, 4.0, 4.5),
    "graphic_step_list": (3.0, 4.0, 4.5),
    "graphic_label_callout": (3.5, 5.0, 5.0),
    "graphic_comparison": (3.5, 5.0, 5.0),
    "graphic_plate_ratio": (3.0, 4.5, 5.0),
    "graphic_routine_split": (3.5, 5.0, 5.0),
}


@dataclass
class SceneValidationIssue:
    type: str
    scene_id: str | None
    severity: str  # "blocking_error" | "repairable_error" | "warning"
    detail: str
    repair_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def issues_to_dicts(issues: list[SceneValidationIssue]) -> list[dict[str, Any]]:
    return [issue.to_dict() for issue in issues]


def has_blocking_or_repairable(issues: list[SceneValidationIssue]) -> bool:
    return any(issue.severity in {"blocking_error", "repairable_error"} for issue in issues)


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+(?:['’][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+)?", str(text or ""))


def count_spoken_words(text: str) -> int:
    return len(_words(text))


def count_sentences(text: str) -> int:
    parts = [p for p in re.split(r"[.!?¡¿]+|\n+", str(text or "")) if p.strip()]
    return len(parts)


def estimate_spanish_narration_sec(text: str, wps: float = DEFAULT_SPANISH_WPS) -> float:
    words = count_spoken_words(text)
    if words == 0:
        return 0.0
    sentence_pause = 0.18 * count_sentences(text)
    return (words / float(wps or DEFAULT_SPANISH_WPS)) + sentence_pause


def max_spoken_words_for_duration(target_video_sec: float, wps: float = DEFAULT_SPANISH_WPS) -> int:
    return int(math.floor(float(target_video_sec or 35.0) * float(wps or DEFAULT_SPANISH_WPS) * 0.88))


def validate_script_word_budget(script: dict[str, Any], *, wps: float = DEFAULT_SPANISH_WPS) -> SceneValidationIssue | None:
    narration = str((script or {}).get("narration") or "")
    target = float((script or {}).get("target_duration_sec") or 35.0)
    words = count_spoken_words(narration)
    estimated = estimate_spanish_narration_sec(narration, wps=wps)
    max_words = max_spoken_words_for_duration(target, wps=wps)
    if estimated > target * 1.05 or estimated > 38.0 or words > max_words:
        return SceneValidationIssue(
            type="script_word_budget",
            scene_id=None,
            severity="repairable_error",
            detail=(
                f"Script narration has {words} spoken words; estimated_spoken_duration "
                f"is {estimated:.1f}s at {wps:.2f} wps for target {target:.1f}s "
                f"(recommended max about {max_words} words)."
            ),
            repair_hint=(
                "Condense narration before scene generation. For 35s Shorts use about "
                "60-70 spoken Spanish words; speak at most 3-4 checklist points."
            ),
        )
    return None


def estimate_spoken_checklist_points(script: dict[str, Any]) -> int:
    text = str((script or {}).get("narration") or "")
    lower = text.lower()
    numbered_words = re.findall(r"\b(uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)\s*:", lower)
    numeric_markers = re.findall(r"(?:^|[\s\n])(?:\d+)[\).:]", text)
    if numbered_words or numeric_markers:
        return len(numbered_words) + len(numeric_markers)
    if "cinco cosas" in lower or "cinco puntos" in lower or "cinco pasos" in lower:
        return 5
    if "cuatro cosas" in lower or "cuatro puntos" in lower or "cuatro pasos" in lower:
        return 4
    return 0


def validate_script_checklist_point_cap(script: dict[str, Any]) -> SceneValidationIssue | None:
    text = " ".join(
        str((script or {}).get(key) or "")
        for key in ("short_format", "format", "narration", "hook")
    ).lower()
    if not any(term in text for term in ("checklist", "lista", "revisa", "paso", "punto")):
        return None
    points = estimate_spoken_checklist_points(script)
    if points > 4:
        return SceneValidationIssue(
            type="script_checklist_point_cap",
            scene_id=None,
            severity="repairable_error",
            detail=f"Checklist/explainer narration appears to speak {points} points; normal 30-38s Shorts should speak at most 3-4.",
            repair_hint="Speak the top 3-4 points and move remaining details to on-screen text or a graphic payload.",
        )
    return None


def validate_audio_fit(
    render_duration_sec: float,
    narration_audio_sec: float,
    *,
    margin_sec: float = AUDIO_TAIL_MARGIN_SEC,
) -> SceneValidationIssue | None:
    if float(narration_audio_sec or 0) + margin_sec > float(render_duration_sec or 0):
        return SceneValidationIssue(
            type="audio_fit",
            scene_id=None,
            severity="blocking_error",
            detail=(
                f"Narration audio ({float(narration_audio_sec):.1f}s) exceeds video duration "
                f"({float(render_duration_sec):.1f}s) with margin {margin_sec:.1f}s."
            ),
            repair_hint="Condense narration or increase valid scene durations without exceeding scene caps.",
        )
    return None


def probe_audio_duration_sec(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            if rate <= 0:
                return None
            return handle.getnframes() / float(rate)
    except Exception:
        return None


def _scene_id(scene: dict[str, Any], index: int) -> str:
    return str(scene.get("id") or scene.get("scene_id") or f"s{index + 1:02d}")


def _duration(scene: dict[str, Any]) -> float:
    try:
        return float(scene.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _looks_like_checklist_or_explainer(script: dict[str, Any] | None, scenes: list[dict[str, Any]]) -> bool:
    text = " ".join(
        [
            str((script or {}).get("short_format") or ""),
            str((script or {}).get("format") or ""),
            str((script or {}).get("narration") or ""),
            " ".join(_joined_scene_text(scene) for scene in scenes),
        ]
    ).lower()
    return any(term in text for term in ("checklist", "lista", "paso", "revisa", "etiqueta", "por 100 g", "ingrediente"))


def _missing_graphic_candidate(scene: dict[str, Any]) -> bool:
    if str(scene.get("layout") or "").startswith("graphic_"):
        return False
    text = _joined_scene_text(scene).lower()
    label_terms = ("etiqueta", "fibra", "azúcar", "azucar", "azúcares", "azucares", "sal", "proteína", "proteina", "por 100 g")
    comparison_terms = ("mejor", "cuidado", "opción a", "opcion a", "opción b", "opcion b")
    structured_terms = ("1/2", "1/4", "50%", "25%", "paso 1", "paso 2")
    return (
        sum(1 for term in label_terms if term in text) >= 2
        or any(term in text for term in comparison_terms) and (" vs " in text or "opción" in text or "opcion" in text)
        or any(term in text for term in structured_terms)
    )


def validate_scene_structure(
    scenes: list[dict[str, Any]],
    *,
    scenes_doc: dict[str, Any] | None = None,
    script: dict[str, Any] | None = None,
    audio_duration_sec: float | None = None,
) -> list[SceneValidationIssue]:
    """Deterministic pre-QA validation for Shorts scene structure.

    This is the numeric/layout authority for spec v1.3. LLM QA can comment on
    product quality, but duration caps and arithmetic are decided here.
    """
    issues: list[SceneValidationIssue] = []
    scenes_doc = scenes_doc or {}
    scenes = list(scenes or [])
    scene_count = len(scenes)
    is_checklist = _looks_like_checklist_or_explainer(script, scenes)

    min_count = 6 if is_checklist else 4
    max_count = 9 if is_checklist else 8
    if scene_count < min_count or scene_count > max_count:
        issues.append(SceneValidationIssue(
            type="scene_count",
            scene_id=None,
            severity="repairable_error",
            detail=f"Scene count {scene_count} is outside recommended range {min_count}-{max_count}.",
            repair_hint="Use 5-8 scenes by default, 6-9 for checklist/explainer, 4-6 for simple hook-tip-CTA.",
        ))

    if scenes:
        first_layout = str(scenes[0].get("layout") or "")
        if first_layout != "short_hook":
            issues.append(SceneValidationIssue(
                type="first_scene_layout",
                scene_id=_scene_id(scenes[0], 0),
                severity="blocking_error",
                detail=f"First scene layout is {first_layout!r}; expected short_hook.",
                repair_hint="Regenerate with the first scene as short_hook.",
            ))
        cta_text = str((script or {}).get("cta") or "").strip()
        has_cta = bool(cta_text) or any(str(scene.get("layout") or "") == "short_cta" for scene in scenes)
        if has_cta and str(scenes[-1].get("layout") or "") != "short_cta":
            issues.append(SceneValidationIssue(
                type="last_scene_cta",
                scene_id=_scene_id(scenes[-1], scene_count - 1),
                severity="blocking_error",
                detail="CTA exists but the last scene is not short_cta.",
                repair_hint="Append or regenerate a final short_cta scene.",
            ))

    scene_sum = round(sum(_duration(scene) for scene in scenes), 3)
    computed_total = round(sum(_duration(scene) for scene in scenes), 1)
    original_declared = scenes_doc.get("total_duration_sec") if scenes_doc is not None else None
    if original_declared is not None:
        try:
            declared_float = float(original_declared)
            if abs(declared_float - computed_total) > 0.11:
                issues.append(SceneValidationIssue(
                    type="total_duration_normalized",
                    scene_id=None,
                    severity="warning",
                    detail=f"total_duration_sec normalized from {original_declared} to {computed_total}.",
                    repair_hint=None,
                ))
        except (TypeError, ValueError):
            issues.append(SceneValidationIssue(
                type="total_duration_normalized",
                scene_id=None,
                severity="warning",
                detail=f"total_duration_sec normalized from {original_declared!r} to {computed_total}.",
                repair_hint=None,
            ))
    if scenes_doc is not None:
        scenes_doc["total_duration_sec"] = computed_total
    declared = computed_total

    total_for_range = float(declared or scene_sum or 0.0)
    if total_for_range and not (MIN_SHORT_DURATION_SEC <= total_for_range <= MAX_SHORT_DURATION_SEC):
        issues.append(SceneValidationIssue(
            type="duration_range",
            scene_id=None,
            severity="repairable_error",
            detail=f"Total duration {total_for_range:.1f}s is outside hard range 20-45s.",
            repair_hint="Keep final duration within 20-45s; do not stretch individual scenes.",
        ))
    elif total_for_range and not (IDEAL_MIN_SHORT_DURATION_SEC <= total_for_range <= IDEAL_MAX_SHORT_DURATION_SEC):
        issues.append(SceneValidationIssue(
            type="duration_ideal",
            scene_id=None,
            severity="warning",
            detail=f"Total duration {total_for_range:.1f}s is outside ideal 28-38s but within hard range.",
            repair_hint="Render is allowed if pacing and audio-fit are strong.",
        ))

    graphic_count = 0
    missing_graphic_candidates = 0
    for index, scene in enumerate(scenes):
        sid = _scene_id(scene, index)
        layout = str(scene.get("layout") or "")
        dur = _duration(scene)

        if layout not in SUPPORTED_SCENE_LAYOUTS:
            issues.append(SceneValidationIssue(
                type="layout",
                scene_id=sid,
                severity="blocking_error",
                detail=f"Unsupported scene layout {layout!r}.",
                repair_hint="Use only supported short_* or graphic_* layouts.",
            ))
            continue

        if layout.startswith("graphic_"):
            graphic_count += 1

        if dur > GLOBAL_SCENE_MAX_SEC:
            issues.append(SceneValidationIssue(
                type="duration_cap",
                scene_id=sid,
                severity="repairable_error",
                detail=f"Scene {sid} duration {dur:.1f}s exceeds global hard max {GLOBAL_SCENE_MAX_SEC:.1f}s.",
                repair_hint=f"No scene may exceed 5.0 sec in a normal Short. Split or regenerate {sid}.",
            ))
        target = LAYOUT_DURATION_TARGETS.get(layout)
        if target:
            target_min, target_max, hard_max = target
            if dur > hard_max:
                issues.append(SceneValidationIssue(
                    type="duration_cap",
                    scene_id=sid,
                    severity="repairable_error",
                    detail=f"Scene {sid} ({layout}) duration {dur:.1f}s exceeds hard max {hard_max:.1f}s.",
                    repair_hint=f"No scene may exceed {hard_max:.1f} sec for layout {layout}. Split or regenerate {sid}.",
                ))
            elif dur and not (target_min <= dur <= target_max):
                issues.append(SceneValidationIssue(
                    type="duration_pacing",
                    scene_id=sid,
                    severity="warning",
                    detail=f"Scene {sid} ({layout}) duration {dur:.1f}s is outside target {target_min:.1f}-{target_max:.1f}s.",
                    repair_hint="Allowed if pacing remains strong and hard caps are respected.",
                ))

        narration = str(scene.get("narration") or "")
        estimated_scene_audio = estimate_spanish_narration_sec(narration)
        if narration.strip() and estimated_scene_audio > dur + 0.3:
            issues.append(SceneValidationIssue(
                type="scene_narration_fit",
                scene_id=sid,
                severity="repairable_error",
                detail=f"Scene {sid} narration estimates {estimated_scene_audio:.1f}s for {dur:.1f}s scene (exceeds 0.3s tolerance).",
                repair_hint="Condense narration or increase scene duration within layout cap. Do not exceed hard cap.",
            ))
        elif narration.strip() and estimated_scene_audio > dur:
            issues.append(SceneValidationIssue(
                type="scene_narration_fit",
                scene_id=sid,
                severity="warning",
                detail=f"Scene {sid} narration estimates {estimated_scene_audio:.1f}s for {dur:.1f}s scene.",
                repair_hint="Consider condensing narration slightly or adjusting duration.",
            ))

        on_screen_text = str(scene.get("on_screen_text") or "").strip().upper()
        if on_screen_text in PASSIVE_CTA_TEXTS:
            issues.append(SceneValidationIssue(
                type="passive_cta",
                scene_id=sid,
                severity="repairable_error",
                detail=f"Scene {sid} CTA text '{on_screen_text}' is passive/status-like.",
                repair_hint="Use GUARDA ESTA LISTA, GUÁRDALO PARA LA COMPRA, MÍRALO ANTES DE COMPRAR PAN, or ÚSALO EN EL SÚPER.",
            ))

        if _missing_graphic_candidate(scene):
            missing_graphic_candidates += 1

    if graphic_count == 3:
        issues.append(SceneValidationIssue(
            type="graphic_count",
            scene_id=None,
            severity="warning",
            detail=f"Short has 3 graphic scenes; normal Shorts should use 1-2 unless intentionally graphic-led.",
            repair_hint="Verify if 3 graphics are intentionally needed, otherwise reduce to 1-2.",
        ))
    elif graphic_count >= 4:
        issues.append(SceneValidationIssue(
            type="graphic_count",
            scene_id=None,
            severity="repairable_error",
            detail=f"Short has {graphic_count} graphic scenes; normal Shorts should use 1-2.",
            repair_hint="Keep the two highest-value graphics and convert extras to stock short_tip/short_checklist scenes.",
        ))

    if missing_graphic_candidates and graphic_count >= MAX_GRAPHIC_SCENES_PER_SHORT:
        issues.append(SceneValidationIssue(
            type="missing_graphic_warning",
            scene_id=None,
            severity="warning",
            detail="A stock scene contains visualizable label/checklist structure, but the Short already has 2 graphics.",
            repair_hint="Do not add a third graphic; improve the stock visual_prompt instead.",
        ))

    if audio_duration_sec is not None:
        issue = validate_audio_fit(total_for_range or scene_sum, audio_duration_sec)
        if issue:
            issues.append(issue)

    return issues


def build_scene_repair_plan(
    scenes: list[dict[str, Any]],
    issues: list[SceneValidationIssue],
    script: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repair_modes: list[str] = []
    instructions: list[str] = [
        "REPAIR PLAN:",
        "- You must fix the listed scene IDs and not reintroduce the same violation.",
        "- target_duration_sec is a soft planning target; do not stretch scenes to reach 35 sec.",
        "- Final total may be 28-34 sec, or any 20-45 sec duration, if pacing and audio-fit are strong.",
    ]
    suggested_scene_plan: list[dict[str, Any]] = []

    for issue in issues:
        if issue.severity == "warning":
            continue
        if issue.type in {"duration_cap", "scene_narration_fit"} and issue.scene_id:
            repair_modes.append("split_long_scene")
            instructions.append(f"- Fix {issue.scene_id}: {issue.detail}")
            instructions.append("- No scene may exceed 5.0 sec in a normal Short; split, shorten, or regenerate the scene.")
            original = next((scene for scene in scenes if _scene_id(scene, -1) == issue.scene_id), {})
            suggested_scene_plan.append({
                "id": f"{issue.scene_id}a",
                "duration_sec": 3.4,
                "layout": "short_tip",
                "on_screen_text": str(original.get("on_screen_text") or "COMPARA CON OTRO")[:32],
            })
            suggested_scene_plan.append({
                "id": f"{issue.scene_id}b",
                "duration_sec": 3.2,
                "layout": "short_tip",
                "on_screen_text": "ETIQUETA CLARA",
            })
        elif issue.type == "graphic_count":
            repair_modes.append("reduce_graphics")
            instructions.append("- Keep exactly 2 graphic scenes, chosen for the highest-value knowledge moments.")
            instructions.append("- Convert extra graphic/checklist-heavy scenes to stock short_tip or short_checklist scenes.")
        elif issue.type == "passive_cta":
            repair_modes.append("cta_rewrite")
            instructions.append("- Rewrite passive CTA text to an action CTA such as GUARDA ESTA LISTA or GUÁRDALO PARA LA COMPRA.")
        elif issue.type == "audio_fit":
            repair_modes.append("audio_fit")
            instructions.extend([
                "AUDIO-FIT REPAIR PLAN:",
                "- Actual narration audio exceeds video duration.",
                "- Condense narration; do not stretch scenes above caps.",
                "- Keep 3 spoken checklist points max.",
                "- Move supporting detail to on_screen_text or graphic payload.",
                "- Regenerate scenes after script compression."
            ])
        elif issue.type == "script_word_budget":
            repair_modes.append("script_condense")
            instructions.append("- Compress narration to about 60-70 spoken Spanish words for a 35s Short.")
            instructions.append("- For checklist/explainer Shorts, speak only the top 3-4 checklist points.")
        else:
            instructions.append(f"- Fix {issue.type}: {issue.detail}")
            if issue.repair_hint:
                instructions.append(f"- {issue.repair_hint}")

    mode = " | ".join(sorted(set(repair_modes))) if repair_modes else "warnings_only"
    return {
        "repair_mode": mode,
        "instructions": instructions,
        "suggested_scene_plan": suggested_scene_plan,
    }

ALLOWED_GRAPHIC_VARIANTS = {
    "brand_default",
    "warm_olive",
    "soft_clay",
    "cream_focus",
    "evening_calm",
}

ALLOWED_GRAPHIC_VISUAL_TONES = {
    "calm",
    "focus",
    "warning_soft",
    "positive",
    "evening",
}

ALLOWED_GRAPHIC_BACKGROUND_MODES = {
    "clean",
    "radial",
    "paper",
    "video_blur",
}

ALLOWED_GRAPHIC_SURFACE_STYLES = {
    "none",
    "soft_card",
    "editorial",
    "plate_focus",
}

PLATE_RATIO_TOTAL = 100.0
PLATE_RATIO_EPSILON = 0.01
MAX_GRAPHIC_SCENES_PER_SHORT = 2
GRAPHIC_MIN_DURATION_SEC = 2.5
GRAPHIC_MAX_DURATION_SEC = 5.0
GRAPHIC_LAYOUT_DURATION_TARGETS = {
    "graphic_checklist": (3.0, 4.0, 4.5),
    "graphic_step_list": (3.0, 4.0, 4.5),
    "graphic_plate_ratio": (3.0, 4.5, 5.0),
    "graphic_label_callout": (3.5, 5.0, 5.0),
    "graphic_comparison": (3.5, 4.5, 5.0),
    "graphic_routine_split": (3.5, 5.0, 5.0),
}
PASSIVE_CTA_TEXTS = {
    "CHECKLIST GUARDADA",
    "LISTA COMPLETA",
    "FIN",
    "CONSEJO FINAL",
}
BREAD_LABEL_TOPIC_TERMS = (
    "pan",
    "marrón",
    "marron",
    "integral",
    "etiqueta",
    "ingrediente",
    "fibra",
)
BREAD_LABEL_HOOK_VISUAL_TERMS = (
    "bread",
    "pan",
    "package",
    "packaging",
    "label",
    "ingredient",
    "supermarket",
    "shelf",
    "basket",
)

# Text-density limits (keep in sync with the TypeScript Zod schemas).
_PLATE_LABEL_MAX = 48
_CHECKLIST_ITEM_MAX = 48
_STEP_TEXT_MAX = 56
_FOOTER_MAX = 72
_TITLE_MAX_PHASE15 = 60
_LABEL_CALLOUT_PRODUCT_MAX = 36
_LABEL_CALLOUT_LABEL_MAX = 22
_LABEL_CALLOUT_VALUE_MAX = 26
_LABEL_CALLOUT_NOTE_MAX = 48
_COMPARISON_HEADING_MAX = 24
_COMPARISON_TEXT_MAX = 68
_COMPARISON_BADGE_MAX = 28
_ROUTINE_TOTAL_MAX = 16
_ROUTINE_TIME_MAX = 16
_ROUTINE_TEXT_MAX = 52

FORBIDDEN_HEALTH_MARKETING_WORDS = (
    "veneno",
    "prohibido",
    "nunca",
    "milagro",
    "cura",
    "doctores no quieren",
)


def validate_short_graphic_scenes(scenes: list[dict[str, Any]]) -> list[str]:
    """Validate graphic scenes in place. Raises ``ValueError`` on hard errors.

    Returns a list of non-fatal warnings (e.g. duration / count advisories).
    Also inserts safe compatibility stubs for the rich ``Scene`` fields graphic
    scenes do not use directly, so render props stay schema-compatible.
    """
    warnings: list[str] = []
    graphic_count = 0
    is_bread_label_topic = _looks_like_bread_label_topic(scenes)

    for index, scene in enumerate(scenes):
        sid = scene.get("id", index)
        layout = scene.get("layout")

        _validate_non_graphic_scene_tuning(scene, sid, layout, index, is_bread_label_topic, warnings)

        if "scene_id" in scene and "id" not in scene:
            raise ValueError(
                f"Scene at index {index} uses scene_id but is missing id. "
                "Normalize scene_id -> id before render props."
            )

        if not isinstance(layout, str) or not layout.startswith("graphic_"):
            continue

        graphic_count += 1

        if layout not in SUPPORTED_GRAPHIC_LAYOUTS:
            raise ValueError(
                f"Scene {sid} uses unsupported graphic layout {layout}. "
                f"Supported graphic layouts: {', '.join(sorted(SUPPORTED_GRAPHIC_LAYOUTS))}."
            )

        # Compatibility stubs for the existing rich Scene type.
        scene.setdefault("visual_type", "graphic")
        if not str(scene.get("on_screen_text") or "").strip():
            scene["on_screen_text"] = _title_from_payload(scene.get("layout_payload", {}))
        scene.setdefault("caption", "")
        scene.setdefault("motion", "none")
        scene.setdefault("asset_refs", {})
        if isinstance(scene.get("asset_refs"), dict):
            scene["asset_refs"].setdefault("background", "")

        payload = scene.get("layout_payload")
        if not isinstance(payload, dict):
            raise ValueError(f"Graphic scene {sid} ({layout}) is missing layout_payload.")

        _validate_visual_style_fields(payload, sid, layout)
        _validate_title(payload, sid, layout)
        _validate_footer(payload, sid, layout, warnings)

        if layout == "graphic_plate_ratio":
            _validate_plate_ratio(payload, sid, warnings)
        elif layout == "graphic_checklist":
            _validate_checklist(payload, sid, warnings)
        elif layout == "graphic_step_list":
            _validate_step_list(payload, sid, warnings)
        elif layout == "graphic_label_callout":
            _validate_label_callout(payload, sid, warnings)
        elif layout == "graphic_comparison":
            _validate_comparison(payload, sid)
        elif layout == "graphic_routine_split":
            _validate_routine_split(payload, sid, warnings)

        _validate_graphic_duration(scene, sid, layout, warnings)

    if graphic_count > MAX_GRAPHIC_SCENES_PER_SHORT:
        warnings.append(
            f"Short has {graphic_count} graphic scenes; "
            f"max recommended is {MAX_GRAPHIC_SCENES_PER_SHORT} for MVP."
        )

    return warnings


def _joined_scene_text(scene: dict[str, Any]) -> str:
    payload = scene.get("layout_payload")
    payload_text = ""
    if isinstance(payload, dict):
        payload_text = " ".join(str(v) for v in payload.values() if isinstance(v, (str, int, float)))
    return " ".join(
        str(scene.get(key) or "")
        for key in ("narration", "on_screen_text", "caption", "visual_prompt")
    ) + " " + payload_text


def _looks_like_bread_label_topic(scenes: list[dict[str, Any]]) -> bool:
    text = " ".join(_joined_scene_text(scene).lower() for scene in scenes)
    return any(term in text for term in BREAD_LABEL_TOPIC_TERMS)


def _validate_non_graphic_scene_tuning(
    scene: dict[str, Any],
    sid: Any,
    layout: Any,
    index: int,
    is_bread_label_topic: bool,
    warnings: list[str],
) -> None:
    on_screen_text = str(scene.get("on_screen_text") or "").strip().upper()
    if on_screen_text in PASSIVE_CTA_TEXTS:
        warnings.append(
            f"Scene {sid} CTA text '{on_screen_text}' is passive/status-like; prefer "
            "GUARDA ESTA LISTA or GUÁRDALO PARA LA COMPRA."
        )

    if layout == "short_myth" and float(scene.get("duration_sec") or 0) > 3.0:
        warnings.append(f"Scene {sid} myth/setup duration exceeds 3.0s; keep myth beats short.")

    if on_screen_text == "MITO RÁPIDO" and float(scene.get("duration_sec") or 0) > 3.0:
        warnings.append(f"Scene {sid} keeps generic MITO RÁPIDO too long; use a specific myth statement.")

    if index == 0 and layout == "short_hook" and is_bread_label_topic:
        visual_prompt = str(scene.get("visual_prompt") or "").lower()
        if not any(term in visual_prompt for term in BREAD_LABEL_HOOK_VISUAL_TERMS):
            warnings.append(
                f"Scene {sid} bread/label hook visual is too generic; include bread, package, label, "
                "supermarket shelf, or shopping basket imagery."
            )


def _validate_graphic_duration(scene: dict[str, Any], sid: Any, layout: str, warnings: list[str]) -> None:
    dur = float(scene.get("duration_sec") or 0)
    target_min, target_max, hard_max = GRAPHIC_LAYOUT_DURATION_TARGETS.get(
        layout,
        (GRAPHIC_MIN_DURATION_SEC, GRAPHIC_MAX_DURATION_SEC, GRAPHIC_MAX_DURATION_SEC),
    )

    if dur > hard_max:
        raise ValueError(
            f"Graphic scene {sid} ({layout}) duration {dur}s exceeds hard max {hard_max}s; "
            "graphics must be fast explanatory bursts, not slides."
        )

    if not (target_min <= dur <= target_max):
        warnings.append(
            f"Scene {sid} ({layout}) graphic duration {dur}s is outside the target "
            f"{target_min}-{target_max}s range."
        )


def _validate_optional_choice(
    payload: dict,
    field: str,
    allowed: set[str],
    sid: Any,
    layout: str,
) -> None:
    value = payload.get(field)
    if value is None:
        return
    if not isinstance(value, str) or value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(
            f"Graphic scene {sid} ({layout}) has invalid {field}: {value!r}. "
            f"Allowed values: {allowed_values}."
        )


def _validate_visual_style_fields(payload: dict, sid: Any, layout: str) -> None:
    _validate_optional_choice(payload, "variant", ALLOWED_GRAPHIC_VARIANTS, sid, layout)
    _validate_optional_choice(payload, "visual_tone", ALLOWED_GRAPHIC_VISUAL_TONES, sid, layout)
    _validate_optional_choice(payload, "background_mode", ALLOWED_GRAPHIC_BACKGROUND_MODES, sid, layout)
    _validate_optional_choice(payload, "surface_style", ALLOWED_GRAPHIC_SURFACE_STYLES, sid, layout)


def _validate_title(payload: dict, sid: Any, layout: str) -> None:
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"Graphic scene {sid} ({layout}) requires a non-empty title.")
    max_len = _TITLE_MAX_PHASE15 if layout in {
        "graphic_label_callout",
        "graphic_comparison",
        "graphic_routine_split",
    } else 48
    if len(title) > max_len:
        raise ValueError(f"Graphic scene {sid} title exceeds {max_len} chars: {len(title)}.")


def _validate_footer(payload: dict, sid: Any, layout: str, warnings: list[str]) -> None:
    footer = payload.get("footer")
    if footer is not None and isinstance(footer, str) and len(footer) > _FOOTER_MAX:
        warnings.append(f"Scene {sid} ({layout}) footer exceeds {_FOOTER_MAX} chars: {len(footer)}.")


def _validate_plate_ratio(payload: dict, sid: Any, warnings: list[str]) -> None:
    segments = payload.get("segments")
    if not isinstance(segments, list) or not (2 <= len(segments) <= 4):
        raise ValueError(f"graphic_plate_ratio scene {sid} requires 2-4 segments.")
    total = sum(float(s.get("value", 0)) for s in segments if isinstance(s, dict))
    if abs(total - PLATE_RATIO_TOTAL) > PLATE_RATIO_EPSILON:
        raise ValueError(
            f"graphic_plate_ratio scene {sid} segments must sum to {int(PLATE_RATIO_TOTAL)} "
            f"+/- {PLATE_RATIO_EPSILON}; got {total}."
        )
    for s in segments:
        label = s.get("label") if isinstance(s, dict) else None
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"graphic_plate_ratio scene {sid} has a segment with an empty label.")
        if len(label) > _PLATE_LABEL_MAX:
            warnings.append(f"Scene {sid} plate label exceeds {_PLATE_LABEL_MAX} chars: '{label}'.")


def _validate_checklist(payload: dict, sid: Any, warnings: list[str]) -> None:
    items = payload.get("items")
    if not isinstance(items, list) or not (2 <= len(items) <= 5):
        raise ValueError(f"graphic_checklist scene {sid} requires 2-5 items.")
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"graphic_checklist scene {sid} has an empty item.")
        if len(item) > _CHECKLIST_ITEM_MAX:
            warnings.append(f"Scene {sid} checklist item exceeds {_CHECKLIST_ITEM_MAX} chars: '{item}'.")


def _validate_step_list(payload: dict, sid: Any, warnings: list[str]) -> None:
    steps = payload.get("steps")
    if not isinstance(steps, list) or not (2 <= len(steps) <= 4):
        raise ValueError(f"graphic_step_list scene {sid} requires 2-4 steps.")
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError(f"graphic_step_list scene {sid} has a non-object step.")
        text = step.get("text")
        label = step.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"graphic_step_list scene {sid} has a step with an empty label.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"graphic_step_list scene {sid} has a step with empty text.")
        if len(text) > _STEP_TEXT_MAX:
            warnings.append(f"Scene {sid} step text exceeds {_STEP_TEXT_MAX} chars: '{text}'.")


def _title_from_payload(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("title") or payload.get("productLabel") or "").strip()


def _warn_if_long(value: Any, max_len: int, label: str, sid: Any, warnings: list[str]) -> None:
    if isinstance(value, str) and len(value) > max_len:
        warnings.append(f"Scene {sid} {label} exceeds {max_len} chars: '{value}'.")


def _require_short_string(value: Any, max_len: int, label: str, sid: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Graphic scene {sid} requires non-empty {label}.")
    if len(value) > max_len:
        raise ValueError(f"Graphic scene {sid} {label} exceeds {max_len} chars: {len(value)}.")
    return value


def _validate_label_callout(payload: dict, sid: Any, warnings: list[str]) -> None:
    product_label = payload.get("productLabel")
    _warn_if_long(product_label, _LABEL_CALLOUT_PRODUCT_MAX, "productLabel", sid, warnings)
    callouts = payload.get("callouts")
    if not isinstance(callouts, list) or not (2 <= len(callouts) <= 4):
        got = len(callouts) if isinstance(callouts, list) else "missing"
        raise ValueError(f"graphic_label_callout scene {sid} callouts must contain 2-4 items, got {got}.")
    for callout in callouts:
        if not isinstance(callout, dict):
            raise ValueError(f"graphic_label_callout scene {sid} has a non-object callout.")
        _require_short_string(callout.get("label"), _LABEL_CALLOUT_LABEL_MAX, "callout.label", sid)
        _require_short_string(callout.get("value"), _LABEL_CALLOUT_VALUE_MAX, "callout.value", sid)
        note = callout.get("note")
        _warn_if_long(note, _LABEL_CALLOUT_NOTE_MAX, "callout.note", sid, warnings)


def _check_forbidden_language(value: Any, sid: Any, field: str) -> None:
    if not isinstance(value, str):
        return
    lower = value.lower()
    for word in FORBIDDEN_HEALTH_MARKETING_WORDS:
        if word in lower:
            raise ValueError(
                f"graphic_comparison scene {sid} contains forbidden health-marketing word "
                f"'{word}' in {field}."
            )


def _validate_comparison(payload: dict, sid: Any) -> None:
    _check_forbidden_language(payload.get("title"), sid, "title")
    _check_forbidden_language(payload.get("footer"), sid, "footer")
    for side_name in ("left", "right"):
        side = payload.get(side_name)
        if not isinstance(side, dict):
            raise ValueError(f"graphic_comparison scene {sid} requires object '{side_name}'.")
        _require_short_string(side.get("heading"), _COMPARISON_HEADING_MAX, f"{side_name}.heading", sid)
        _require_short_string(side.get("text"), _COMPARISON_TEXT_MAX, f"{side_name}.text", sid)
        badge = side.get("badge")
        if badge is not None:
            _require_short_string(badge, _COMPARISON_BADGE_MAX, f"{side_name}.badge", sid)
        for field in ("heading", "text", "badge"):
            _check_forbidden_language(side.get(field), sid, f"{side_name}.{field}")


def _validate_routine_split(payload: dict, sid: Any, warnings: list[str]) -> None:
    total_label = payload.get("totalLabel")
    _warn_if_long(total_label, _ROUTINE_TOTAL_MAX, "totalLabel", sid, warnings)
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not (2 <= len(blocks) <= 4):
        got = len(blocks) if isinstance(blocks, list) else "missing"
        raise ValueError(f"graphic_routine_split scene {sid} blocks must contain 2-4 items, got {got}.")
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError(f"graphic_routine_split scene {sid} has a non-object block.")
        _require_short_string(block.get("time"), _ROUTINE_TIME_MAX, "block.time", sid)
        _require_short_string(block.get("text"), _ROUTINE_TEXT_MAX, "block.text", sid)


def repair_scene_duration_if_possible(scene: dict[str, Any]) -> str:
    layout = scene.get("layout") or ""
    narration = scene.get("narration") or ""
    est = estimate_spanish_narration_sec(narration, 2.25)
    required = round(est + 0.3, 1)

    cap = GLOBAL_SCENE_MAX_SEC
    target = LAYOUT_DURATION_TARGETS.get(layout)
    if target:
        cap = target[2]

    try:
        dur = float(scene.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0

    if required <= cap and dur < required:
        scene["duration_sec"] = required
        return "auto_extended"

    if required > cap:
        return "must_split_or_compress"

    return "ok"
