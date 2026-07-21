"""Spec v6 §2.4 — ChatGPT generates Short scenes AND picks layouts."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from video_agent.contracts import resolve_topic_family

# The 7 structured-evidence lists the scene schema / post-asset QA expect.
_EVIDENCE_KEYS = (
    "required_actions", "required_objects", "subject_pose", "visibility",
    "forbidden_pose", "forbidden_context", "forbidden_mood",
)

# Safe defaults for MOVEMENT scenes when the LLM omits required_visual_evidence.
# Encodes the editorial line: active, capable, standing/chair-supported 45+ —
# never frail, bedbound, or medical mobility-aid imagery.
_MOVEMENT_EVIDENCE_DEFAULT = {
    "required_actions": ["standing", "gentle movement or stretching"],
    "required_objects": ["chair", "trainers"],
    "subject_pose": ["upright", "active"],
    "visibility": ["full body or legs visible"],
    "forbidden_pose": ["lying down", "seated silhouette only", "bedbound"],
    "forbidden_context": ["rollator", "walker", "wheelchair", "hospital", "medical mobility aid"],
    "forbidden_mood": ["frail", "sad", "helpless"],
}
from video_agent.shorts import paths, prompts
from video_agent.shorts.first_frame_planner import apply_first_frame_plan
from video_agent.shorts.idea_preservation import normalize_covers_items
from video_agent.shorts.llm import LLMCallLog, log_llm_call
from video_agent.storage.atomic import atomic_write_json

PROVIDER = "chatgpt"


class ChatGPTProviderError(Exception):
    """Raised when ChatGPT returns provider/browser-error text instead of scene
    JSON (e.g. "Something went wrong…"). This is NOT a creative scene-QA failure:
    the caller must clean the browser/session and retry the same prompt, and must
    NOT pass the response to scene validation or emit scene_count repair feedback."""

    failure_kind = "chatgpt_provider_error"

    def __init__(self, message: str, *, snippet: str = ""):
        super().__init__(message)
        self.snippet = snippet


class ChatGPTSizeRefusalError(ChatGPTProviderError):
    """ChatGPT refused to emit the scenes JSON claiming the output would exceed
    its per-message response size — either as plain refusal text or as a JSON
    object with an ``error`` field and an empty scenes array. A fully populated
    5-8 scene payload is small (~3-6 KB), so this is a model misjudgment, not a
    real limit. The builder retries once with a compact-output correction before
    raising; like its parent, this must never reach scene validation or consume
    the creative retry budget."""

    failure_kind = "chatgpt_size_refusal"


class ChatGPTQuotaError(ChatGPTProviderError):
    """ChatGPT declined because of a usage/quota/rate limit, not response size.
    A compact-output retry cannot help — the account must wait or upgrade."""

    failure_kind = "chatgpt_quota"


class ChatGPTAuthError(ChatGPTProviderError):
    """ChatGPT returned an authentication/session error (logged out, session
    expired). Needs a re-login, not a scene retry."""

    failure_kind = "chatgpt_auth"


class ChatGPTPolicyRefusalError(ChatGPTProviderError):
    """ChatGPT refused on content-policy grounds. Retrying the same prompt will
    refuse again; the idea/prompt must change."""

    failure_kind = "chatgpt_policy"


class ChatGPTErrorObjectError(ChatGPTProviderError):
    """ChatGPT returned a JSON error object with no usable scenes that does not
    match a known category (size / quota / auth / policy). Still a provider
    failure — never empty scenes, never creative QA — but distinctly labelled so
    the operator sees the real reason instead of a blanket 'size refusal'."""

    failure_kind = "chatgpt_error_object"


# Provider/browser error phrases that ChatGPT renders as plain page text. If any
# appears, the response is a provider failure, not a scenes answer.
PROVIDER_ERROR_PATTERNS = (
    "something went wrong",
    "help.openai.com",
    "please contact us",
    "this issue persists",
    "an error occurred",
    "try again later",
    "network error",
    "unable to load",
)


def is_provider_error_text(text: str | None) -> bool:
    value = (text or "").strip().lower()
    if not value:
        return False
    return any(pattern in value for pattern in PROVIDER_ERROR_PATTERNS)


def is_valid_scene_payload(payload: object) -> bool:
    """A valid scenes payload is a JSON object with a non-empty ``scenes`` list."""
    if not isinstance(payload, dict):
        return False
    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        return False
    return len(scenes) > 0


# Phrases that pin a no-scenes reply to a SPECIFIC failure cause. Consulted only
# when the payload has no usable scenes, so Spanish scene content can never
# false-positive on these English phrases. bug 20260705 reopen: a JSON error
# object must NOT be blanket-labelled a size refusal — only size wording is size.
SIZE_REFUSAL_PATTERNS = (
    "maximum response size",
    "exceeds the maximum response",
    "exceed the response limit",
    "exceeds the response limit",
    "response limits",
    "too large to fit",
    "too long to",
    "split the task",
    "split it into",
    "break it into",
)
_QUOTA_PATTERNS = (
    "usage limit", "usage cap", "rate limit", "too many requests",
    "you've reached", "you have reached", "reached your", "reached the limit",
    "upgrade to continue", "limit for gpt", "come back later",
)
_AUTH_PATTERNS = (
    "log in", "logged out", "sign in to continue", "session expired",
    "session has expired", "please authenticate", "not authenticated",
    "unauthorized", "you're not logged in", "you are not logged in",
)
_POLICY_PATTERNS = (
    "content policy", "usage policy", "against our", "can't help with that",
    "cannot help with that", "can't assist with", "cannot assist with",
    "unable to help with", "i can't create", "i cannot create",
    "violates", "not able to provide",
)


def _refusal_text(raw: str | None, payload: object | None) -> str:
    """The text to classify: the JSON ``error`` field when present, else the raw
    reply. An error object's message is the authoritative signal."""
    if isinstance(payload, dict):
        err = str(payload.get("error") or "").strip()
        if err:
            return err
    return (raw or "").strip()


def classify_refusal(raw: str | None, payload: object | None = None) -> str | None:
    """Classify a ChatGPT scenes reply that carries NO usable scenes.

    Returns a specific kind — ``"size_refusal"``, ``"quota"``, ``"auth"``,
    ``"policy"``, or ``"error_object"`` (a JSON error object that matches none of
    the above) — or ``None`` when the reply is a valid scenes payload (or empty).
    Valid scene payloads are never a refusal.
    """
    text = (raw or "").strip()
    if not text:
        return None
    if payload is None:
        payload = _parse(raw)
    if is_valid_scene_payload(payload):
        return None
    signal = _refusal_text(raw, payload).lower()
    if any(p in signal for p in SIZE_REFUSAL_PATTERNS):
        return "size_refusal"
    if any(p in signal for p in _QUOTA_PATTERNS):
        return "quota"
    if any(p in signal for p in _AUTH_PATTERNS):
        return "auth"
    if any(p in signal for p in _POLICY_PATTERNS):
        return "policy"
    # A JSON error object with no usable scenes and no recognised wording is still
    # a provider refusal — just an unclassified one, not a size refusal.
    if isinstance(payload, dict) and str(payload.get("error") or "").strip():
        return "error_object"
    return None


# kind -> the typed provider error to raise for it.
_REFUSAL_ERRORS: dict[str, type[ChatGPTProviderError]] = {
    "size_refusal": ChatGPTSizeRefusalError,
    "quota": ChatGPTQuotaError,
    "auth": ChatGPTAuthError,
    "policy": ChatGPTPolicyRefusalError,
    "error_object": ChatGPTErrorObjectError,
}


def is_size_refusal_response(raw: str | None, payload: object | None = None) -> bool:
    """True only for a genuine SIZE refusal (bug 20260705 reopen: quota/auth/
    policy/other error objects are classified separately, not as size)."""
    return classify_refusal(raw, payload) == "size_refusal"


# Appended to the scene prompt for the one in-place retry after a size refusal.
_COMPACT_RETRY_ADDENDUM = (
    "\n\nSIZE CORRECTION:\n"
    "Your previous reply refused for response size. That refusal was wrong: a "
    "complete, fully populated 5-8 scene JSON for this task is small (typically "
    "under 6,000 characters) and fits easily in one message.\n"
    "- Return the COMPLETE JSON object now, minified on one single line "
    "(no indentation, no spaces between tokens).\n"
    "- Do NOT return an error object. Do NOT return an empty scenes array. "
    "Do NOT split the answer across messages.\n"
    "- Keep every required field; keep string values concise.\n"
)


def _parse(raw: str) -> dict:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", str(text or "").strip()) if s.strip()]


SHORT_LAYOUTS = (
    "short_hook", "short_pain", "short_tip", "short_checklist",
    "short_myth", "short_quote", "short_cta",
)

# Structured graphic intents (spec v7 §4). These remain planning/prompt
# vocabulary until the asset stage creates the required ChatGPT image, then the
# scene is persisted as a standard image-backed short_tip layout.
SUPPORTED_GRAPHIC_LAYOUTS = (
    "graphic_plate_ratio",
    "graphic_checklist",
    "graphic_step_list",
    "graphic_label_callout",
    "graphic_comparison",
    "graphic_routine_split",
    # Long-form graphic card ports (see orchestrator/stages/graphic_images.py).
    "graphic_stat",
    "graphic_myth",
    "graphic_do_dont",
    "graphic_recipe_snapshot",
    "graphic_quote_portrait",
    "graphic_evidence_nugget",
    "graphic_warning",
)

# Backward-compat adapter (spec v6 §10). Legacy long-form layout names map to
# their nearest short_* equivalent ONLY for legacy artifacts. New
# short_scene_builder output must already use short_* directly.
_LEGACY_TO_SHORT = {
    "hook": "short_hook",
    "subtitle": "short_tip",
    "warning": "short_pain",
    "checklist": "short_checklist",
    "quote": "short_quote",
    "cta": "short_cta",
}


def _map_layout(layout: str) -> str:
    """Return a schema-valid short_* layout.

    short_* is the first-class layout family. Long-form legacy values are
    mapped through the legacy adapter so old artifacts keep rendering.
    Unknown → ``short_tip`` (safest neutral)."""
    raw = str(layout or "").strip().lower()
    if raw in SHORT_LAYOUTS:
        return raw
    # Preserve supported graphic intents until ChatGPT image acquisition.
    if raw in SUPPORTED_GRAPHIC_LAYOUTS:
        return raw
    if raw in _LEGACY_TO_SHORT:
        return _LEGACY_TO_SHORT[raw]
    return "short_tip"


def _chunk(seq: list, n: int) -> list[list]:
    if n <= 0:
        return []
    k = len(seq)
    return [seq[i * k // n : (i + 1) * k // n] for i in range(n)]


def _source_support_by_item(short_script: dict) -> dict[int, list[str]]:
    by_item: dict[int, list[str]] = {}
    for item in list((short_script or {}).get("idea_items") or []):
        if not isinstance(item, dict):
            continue
        try:
            item_id = int(item.get("item_id"))
        except (TypeError, ValueError):
            continue
        refs = [
            str(ref).strip()
            for ref in list(item.get("source_support") or [])
            if str(ref).strip()
        ]
        if refs:
            by_item[item_id] = refs
    return by_item


def _script_hook_text(short_script: dict) -> str:
    return str((short_script or {}).get("hook") or (short_script or {}).get("hook_text") or "").strip()


def _first_hook_beat_narration(short_script: dict) -> str:
    hook = _script_hook_text(short_script)
    for beat in list((short_script or {}).get("beats") or []):
        if not isinstance(beat, dict):
            continue
        narration = str(beat.get("narration") or "").strip()
        if not narration:
            continue
        purpose = str(beat.get("purpose") or "").strip().lower()
        if purpose == "hook" or (hook and hook.lower() in narration.lower()):
            return narration
    return hook


def _restore_first_hook_scene_narration(sc: dict[str, Any], index: int, short_script: dict) -> None:
    if index != 0:
        return
    if str(sc.get("layout") or "").strip() != "short_hook":
        return
    hook = _script_hook_text(short_script)
    if not hook:
        return
    current = str(sc.get("narration") or "").strip()
    if hook.lower() in current.lower():
        return
    # Preserve the mandatory script hook without copying the whole first beat.
    # The first beat often contains hook + setup; forcing that into a 2-3 second
    # short_hook scene causes deterministic scene_narration_fit failures.
    if current and not str(sc.get("caption") or "").strip():
        sc["caption"] = current
    sc["narration"] = hook
    warnings = list(sc.get("planner_warnings") or [])
    warnings.append("first_hook_narration_restored_from_hook")
    sc["planner_warnings"] = warnings


_STALE_FOOD_PAYLOAD_ITEMS = {
    "porción visible",
    "porcion visible",
    "plato pequeño",
    "plato pequeno",
    "comida completa",
}


def _script_is_food_topic(short_script: dict) -> bool:
    text = " ".join(
        str((short_script or {}).get(key) or "")
        for key in ("title", "hook", "narration", "short_format", "topic_family")
    ).lower()
    return any(
        term in text
        for term in (
            "pan",
            "plato",
            "comida",
            "cena",
            "nutric",
            "aliment",
            "proteína",
            "proteina",
            "fibra",
            "etiqueta",
        )
    )


def _split_payload_phrases(text: str) -> list[str]:
    phrases = [
        p.strip(" .,:;!?¡¿")
        for p in re.split(r"[.;\n]+", str(text or ""))
        if p.strip(" .,:;!?¡¿")
    ]
    return [p[:42] for p in phrases if len(p.split()) <= 5][:3]


def _repair_stale_food_payload(sc: dict[str, Any], short_script: dict) -> None:
    payload = sc.get("layout_payload")
    if not isinstance(payload, dict) or _script_is_food_topic(short_script):
        return
    items = [str(item).strip() for item in list(payload.get("items") or []) if str(item).strip()]
    if not items:
        return
    normalized_items = {item.lower() for item in items}
    if not normalized_items or not normalized_items.issubset(_STALE_FOOD_PAYLOAD_ITEMS):
        return
    replacement = _split_payload_phrases(str(sc.get("caption") or ""))
    if len(replacement) < 2:
        replacement = _split_payload_phrases(str(sc.get("narration") or ""))
    if len(replacement) < 2:
        replacement = [str(sc.get("on_screen_text") or "Cierre simple").strip()[:42]]
    payload["items"] = replacement
    sc["layout_payload"] = payload
    warnings = list(sc.get("planner_warnings") or [])
    warnings.append("stale_food_payload_repaired")
    sc["planner_warnings"] = warnings


def normalize_short_scenes(scenes_doc: dict, short_script: dict) -> dict:
    """Make Short scenes compatible with the long-form render/TTS pipeline.

    - rename ``scene_id`` → ``id`` (renderer/prepare_assets read ``id``)
    - guarantee each scene has non-empty ``narration`` (Kokoro TTS needs it):
      reuse existing per-scene narration, else distribute the script narration
      across scenes by sentence, falling back to on_screen_text.
    - fill safe defaults for the visual-quality fields (visual_importance,
      asset_strategy, required_visual_evidence) when the LLM omits them.
    """
    topic = resolve_topic_family(short_script or {}) if short_script else None
    is_movement = topic is not None and topic.name == "MOVEMENT"
    out = dict(scenes_doc or {})
    scenes = list(out.get("scenes") or [])
    n = len(scenes)
    sentences = _split_sentences((short_script or {}).get("narration"))
    groups = _chunk(sentences, n) if sentences else [[] for _ in range(n)]
    source_support_by_item = _source_support_by_item(short_script or {})

    norm_scenes = []
    for i, raw in enumerate(scenes):
        sc = dict(raw)
        if not sc.get("id"):
            sc["id"] = sc.get("scene_id") or f"s{i + 1}"
        if not str(sc.get("narration") or "").strip():
            chunk = groups[i] if i < len(groups) else []
            narr = " ".join(chunk).strip()
            if not narr:
                narr = str(sc.get("on_screen_text") or "").strip() or str(
                    (short_script or {}).get("hook") or ""
                ).strip()
            sc["narration"] = narr
        # Seed the full long-form scene shape the render/TTS pipeline expects.
        sc.setdefault("on_screen_text", "")
        sc.setdefault("caption", sc.get("on_screen_text", ""))
        sc.setdefault("visual_prompt", sc.get("caption", ""))
        sc["layout"] = _map_layout(sc.get("layout"))
        _restore_first_hook_scene_narration(sc, i, short_script or {})
        # Graphic scenes carry a "graphic" visual_type so downstream tooling
        # can distinguish them; stock scenes keep the placeholder type.
        sc.setdefault(
            "visual_type",
            "graphic" if str(sc.get("layout") or "").startswith("graphic_") else "generated_placeholder",
        )
        sc.setdefault("layout_payload", {})
        _repair_stale_food_payload(sc, short_script or {})
        covers_items, covers_warnings = normalize_covers_items(sc.get("covers_items"))
        sc["covers_items"] = covers_items
        if covers_warnings:
            existing_warnings = list(sc.get("planner_warnings") or [])
            existing_warnings.extend(covers_warnings)
            sc["planner_warnings"] = existing_warnings
        sc.setdefault("layout_reason", "short")
        sc.setdefault("motion", "none")
        sc.setdefault("asset_refs", {})
        sc.setdefault("word_segments", [])
        sc.setdefault("planner_warnings", [])
        sc.setdefault("audio_offset_sec", 0.0)
        sc.setdefault("duration_sec", 3.0)
        if sc["layout"] == "short_cta":
            try:
                if float(sc.get("duration_sec") or 0.0) > 2.8:
                    sc["duration_sec"] = 2.8
            except (TypeError, ValueError):
                sc["duration_sec"] = 2.4
        sc.setdefault("transition_from_previous", "")
        sc.setdefault("visual_importance", "normal")
        sc.setdefault("asset_strategy", "stock_ok")
        sc.setdefault("required_visual_evidence", {})
        # Visual-span planner hints (spec §8): preserve valid planner values
        # verbatim, never invent. Absence ⇒ implicit one-scene span downstream.
        # Do NOT add semantic_segment_id; do NOT default visual_span_id.
        _span_id = str(sc.get("visual_span_id") or "").strip()
        if _span_id:
            sc["visual_span_id"] = _span_id
        else:
            sc.pop("visual_span_id", None)
        _span_intent = str(sc.get("visual_span_intent") or "").strip()
        if _span_intent:
            sc["visual_span_intent"] = _span_intent
        else:
            sc.pop("visual_span_intent", None)
        if sc["layout"] in ("short_hook", "short_cta") and sc["visual_importance"] == "normal":
            sc["visual_importance"] = "critical"
        elif "bridge" in sc["layout"] and sc["visual_importance"] == "normal":
            sc["visual_importance"] = "bridge"
        # MOVEMENT Shorts: every real content scene is a critical action scene.
        # Generic frail/medical mobility footage is the failure mode for this
        # 45+ audience, so mark them critical (forces AI fallback when strict
        # stock fails) and seed required/forbidden evidence the post-asset QA
        # gate enforces, unless the LLM already supplied richer evidence.
        is_graphic = str(sc["layout"]).startswith("graphic_")
        if is_movement and not is_graphic and sc["visual_importance"] != "bridge":
            sc["visual_importance"] = "critical"
            if not sc.get("required_visual_evidence"):
                sc["required_visual_evidence"] = dict(_MOVEMENT_EVIDENCE_DEFAULT)
        # Guarantee the structured-evidence shape is always present so downstream
        # QA never KeyErrors on a partial dict from the LLM.
        rve = sc.get("required_visual_evidence") or {}
        for _k in _EVIDENCE_KEYS:
            rve.setdefault(_k, [])
        sc["required_visual_evidence"] = rve
        # Scenes that cover idea items must carry a source_scene_ids list (the
        # strict mapping validator rejects a missing field; [] is its explicit
        # "no support found" value the repair loop then fills).
        if sc.get("covers_items"):
            source_ids = [
                str(ref).strip()
                for ref in list(sc.get("source_scene_ids") or [])
                if str(ref).strip()
            ]
            if not source_ids:
                for item_id in sc["covers_items"]:
                    source_ids.extend(source_support_by_item.get(item_id, []))
            sc["source_scene_ids"] = list(dict.fromkeys(source_ids))
        sc.setdefault("retention_function", "")
        norm_scenes.append(sc)

    # Guarantee a closing short_cta scene whenever the script declared a CTA
    # but the LLM forgot to add the layout. Without a CTA scene viewers swipe
    # away on the last beat with no save/comment prompt → retention drop.
    script_cta = str((short_script or {}).get("cta") or "").strip()
    has_cta_scene = any(
        str(s.get("layout") or "").strip().lower() == "short_cta"
        for s in norm_scenes
    )
    if norm_scenes and script_cta and not has_cta_scene:
        cta_idx = len(norm_scenes) + 1
        use_zero = any(re.match(r"^s\d{2}$", str(s.get("id") or "")) for s in norm_scenes)
        cta_id = f"s{cta_idx:02d}" if use_zero else f"s{cta_idx}"
        cta_caption = script_cta
        cta_oneliner = " ".join(script_cta.split()[:6]).upper() or "GUÁRDALO"
        norm_scenes.append({
            "id": cta_id,
            "narration": script_cta,
            "on_screen_text": cta_oneliner,
            "caption": cta_caption,
            "visual_prompt": "Close-up of a calm person looking warmly at camera, soft natural light",
            "layout": "short_cta",
            "layout_payload": {"title": cta_oneliner, "subtitle": cta_caption},
            "covers_items": [],
            "layout_reason": "auto_cta_appended",
            "motion": "slow_zoom",
            "asset_refs": {},
            "word_segments": [],
            "planner_warnings": [],
            "audio_offset_sec": 0.0,
            "duration_sec": 2.4,
        })

    out["scenes"] = norm_scenes
    total_dur = float(sum(float(s.get("duration_sec") or 0) for s in norm_scenes))
    out["total_duration_sec"] = int(total_dur) if total_dur.is_integer() else round(total_dur, 1)
    return out


def build_short_scenes(
    long_job_dir: Path,
    short_plan: dict,
    short_script: dict,
    channel_config: dict,
    llm_fn: Callable[..., str],
    *,
    retention_plan: dict | None = None,
    spoken_humanization: dict | None = None,
    feedback: str = "",
    attempt: int = 1,
) -> dict[str, Any]:
    """Spec v6 §2.4: ChatGPT writes scenes AND picks each scene's layout.

    The deterministic ``normalize_short_scenes`` only fills missing fields and
    maps any legacy long-form layout names (backward compat); it must NOT
    overwrite layouts the LLM emitted."""
    topic = resolve_topic_family(short_script)
    prompt = prompts.short_scene_prompt_v6(
        channel_config,
        short_plan,
        short_script,
        feedback=feedback,
        retention_plan=retention_plan,
        spoken_humanization=spoken_humanization,
        topic=topic,
    )
    log_llm_call(LLMCallLog(
        task="short_scene_builder", provider=PROVIDER,
        short_id=short_plan.get("short_id", "-"), attempt=attempt,
        input_artifacts=["short_script.json"],
        output_artifact="short_scenes.json",
    ))
    raw = _invoke(llm_fn, "scenes", prompt)
    # Provider-error guard (spec §2): detect "Something went wrong…" etc. BEFORE
    # parsing/normalizing so a browser failure never becomes empty scenes, never
    # enters scene_validation, and never produces "Fix scene_count=0" feedback.
    if is_provider_error_text(raw):
        snippet = (raw or "").strip().splitlines()[0][:200] if (raw or "").strip() else ""
        raise ChatGPTProviderError(
            "ChatGPT returned provider-error text instead of scene JSON.",
            snippet=snippet,
        )
    payload = _parse(raw)
    # Refusal guard (bridge 20260705-141239 + reopen): a reply with no usable
    # scenes is classified by CAUSE, not blanket-labelled a size refusal. Only a
    # genuine size refusal earns the in-place compact-output retry — its bigger
    # repair prompt only reinforces the refusal, so the creative repair loop must
    # never see it. Quota / auth / policy / other error objects cannot be fixed by
    # re-asking compactly, so they raise their specific typed error immediately so
    # the operator sees the real reason.
    kind = classify_refusal(raw, payload)
    if kind == "size_refusal":
        raw = _invoke(llm_fn, "scenes", prompt + _COMPACT_RETRY_ADDENDUM)
        if is_provider_error_text(raw):
            snippet = (raw or "").strip().splitlines()[0][:200] if (raw or "").strip() else ""
            raise ChatGPTProviderError(
                "ChatGPT returned provider-error text instead of scene JSON.",
                snippet=snippet,
            )
        payload = _parse(raw)
        retry_kind = classify_refusal(raw, payload)
        if retry_kind:
            snippet = (raw or "").strip().splitlines()[0][:200] if (raw or "").strip() else ""
            error_cls = _REFUSAL_ERRORS.get(retry_kind, ChatGPTErrorObjectError)
            reason = (
                "ChatGPT refused the scenes JSON for response size twice "
                "(original prompt + compact retry)."
                if retry_kind == "size_refusal"
                else f"ChatGPT still returned no usable scenes after the compact retry ({retry_kind})."
            )
            raise error_cls(reason, snippet=snippet)
    elif kind:
        snippet = (raw or "").strip().splitlines()[0][:200] if (raw or "").strip() else ""
        raise _REFUSAL_ERRORS[kind](
            f"ChatGPT returned a {kind} refusal instead of scene JSON; a compact "
            "retry cannot resolve this.",
            snippet=snippet,
        )
    scenes = normalize_short_scenes(payload, short_script)
    scenes = apply_first_frame_plan(scenes, short_plan, channel_config)
    jd = paths.short_json_dir(long_job_dir, short_plan["short_id"])
    jd.mkdir(parents=True, exist_ok=True)
    atomic_write_json(jd / paths.SHORT_SCENES_FILE, scenes)
    return scenes


def _invoke(llm_fn: Callable[..., str], kind: str, prompt: str) -> str:
    try:
        return llm_fn(prompt)
    except TypeError:
        return llm_fn(kind, prompt)
