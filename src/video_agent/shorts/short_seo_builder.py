"""Build ``short_seo.json`` for a Short (LLM-generated, parsed + normalized)."""
from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import re
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

from video_agent.audience_age import resolve_target_min_age
from video_agent.shorts import paths, prompts
from video_agent.shorts.idea_preservation import validate_seo_idea_consistency
from video_agent.storage.atomic import atomic_write_json

# A Shorts title must be a <=40-char scroll-stopper using one of the 4 proven
# formulas, and must echo the hook (Shorts have no thumbnail — the first 3
# seconds ARE the title). These are enforced deterministically after the LLM
# call so a drifting title is regenerated (and hard-trimmed as a last resort).
MAX_SHORT_TITLE_CHARS = 40

_TITLE_FORMULA_SIGNALS = (
    re.compile(r"error al ", re.IGNORECASE),                 # Warning
    re.compile(r"deja de ", re.IGNORECASE),                  # Warning variant
    re.compile(r"en 60 segundos", re.IGNORECASE),            # Quick Win
    re.compile(r"\(sin ", re.IGNORECASE),                    # Quick Win "(Sin …)"
    re.compile(r"la verdad", re.IGNORECASE),                 # Myth-Buster ("¿…? La verdad")
    re.compile(r"si tienes m[aá]s de", re.IGNORECASE),       # Call Out
    re.compile(r"necesitas saber", re.IGNORECASE),           # Call Out
)

# Standalone micro-promise (2026-07-12, mirrors long-form bug-530): a title
# that hides its content behind a bare deictic ("escucha esto", "deja de hacer
# esto") tells the 45+ viewer nothing — they will not infer, they scroll past.
_DEICTIC_TITLE_RE = re.compile(r"\b(esto|eso|aquello)\b", re.IGNORECASE)
# NOTE: a bare "¿…?" is intentionally NOT a formula signal — a plain question
# like "¿Café en ayunas?" is not a complete Myth-Buster (that needs "La verdad
# científica"). Each of the 4 formulas above has a stronger, specific marker.

_TITLE_STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "que",
    "esto", "este", "esta", "con", "por", "para", "más", "mas", "tus", "sus",
    "como", "cada", "sin", "hoy", "eso", "año", "años", "tras", "sobre",
    # 3-letter fillers (only relevant now that the length floor dropped to 3).
    "muy", "tan", "les", "nos", "soy", "voy", "doy",
}


def _title_content_tokens(text: str) -> set[str]:
    """Lower-cased, accent-stripped content words (>=3 chars, non-stopword).

    >=3 (not 4): many real Spanish topic nouns are exactly 3 letters (pan, sal,
    sol, voz, luz, paz, red, oro, uva, té) — a >=4 floor silently dropped them
    from the topic-keyword contract, so a bread ("pan") Short's fallback title
    synthesis had no way to ever embed its own topic word (bug-523 follow-up).
    """
    decomposed = unicodedata.normalize("NFKD", str(text).lower())
    ascii_text = "".join(c for c in decomposed if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9]+", ascii_text)
    return {w for w in words if len(w) >= 3 and w not in _TITLE_STOPWORDS}


def _title_issues(title: str, hook: str) -> list[str]:
    """Deterministic scroll-stopper title checks (feed the SEO retry loop)."""
    issues: list[str] = []
    t = (title or "").strip()
    if len(t) > MAX_SHORT_TITLE_CHARS:
        issues.append(
            f"Title is {len(t)} characters; a Shorts title MUST be <= {MAX_SHORT_TITLE_CHARS}."
        )
    if not any(pat.search(t) for pat in _TITLE_FORMULA_SIGNALS):
        issues.append(
            "Title uses none of the 4 scroll-stopper formulas "
            "(Warning '¡Error al…' / Quick Win '… en 60 segundos (Sin …)' / "
            "Myth-Buster '¿…? La verdad científica' / Call Out 'Si tienes más de …')."
        )
    if hook and not (_title_content_tokens(t) & _title_content_tokens(hook)):
        issues.append(
            "Title shares no content word with the hook; the first 3 seconds of the "
            "Short must echo the title, so the title must be built from the hook's topic."
        )
    if _DEICTIC_TITLE_RE.search(t):
        issues.append(
            "Title hides its content behind 'esto/eso' — a context-free fragment. "
            "Name the concrete topic object and action instead "
            "(e.g. 'Si tienes más de 60, revisa tu sal', never 'escucha esto')."
        )
    return issues


def _topic_tokens(short_plan: dict) -> set[str]:
    """Content words of the Short's actual TOPIC (idea title / topic / pillar).

    Used to enforce the topic-keyword SEO contract: a title like "Si tienes más
    de 45, descubre tu ritmo" carries audience+benefit but NO topic, so YouTube
    cannot classify the Short (operator audit scored such a title 53/100).
    """
    parts = " ".join(
        str(short_plan.get(k) or "") for k in ("title", "topic", "pillar", "detected_pillar")
    )
    return _title_content_tokens(parts)


def _seo_topic_issues(
    title: str, description: str, hashtags: list[str], topic_tokens: set[str]
) -> list[str]:
    """Deterministic topic-keyword checks (feed the SEO retry loop).

    1. Title must contain a topic keyword (inside its scroll-stopper formula).
    2. Description's FIRST sentence must contain a topic keyword.
    3. At least one hashtag must be topic-specific (e.g. #caféysalud), because
       generic wellness tags alone never surface the Short in topic searches.
    """
    if not topic_tokens:
        return []
    issues: list[str] = []
    keyword_list = ", ".join(sorted(topic_tokens))
    if not (_title_content_tokens(title) & topic_tokens):
        issues.append(
            "Title carries no topic keyword — YouTube cannot classify the Short. "
            f"Work one of these topic words into the formula: {keyword_list}."
        )
    first_sentence = re.split(r"[.!?\n]", str(description or ""), maxsplit=1)[0]
    if not (_title_content_tokens(first_sentence) & topic_tokens):
        issues.append(
            "Description's FIRST sentence must contain the main topic keyword "
            f"naturally (one of: {keyword_list})."
        )
    stems = {t[:4] for t in topic_tokens}
    normalized_tags = []
    for tag in hashtags:
        decomposed = unicodedata.normalize("NFKD", str(tag).lower().lstrip("#"))
        normalized_tags.append("".join(c for c in decomposed if not unicodedata.combining(c)))
    if not any(any(stem in tag for stem in stems) for tag in normalized_tags):
        issues.append(
            "No hashtag is topic-specific — add at least one combining the topic "
            "with the benefit (e.g. #caféysalud style) built from: " + keyword_list + "."
        )
    return issues


def _seo_engagement_issue(description: str) -> str | None:
    """The description body (before the trailing hashtags) must END with one
    short question that invites a comment — early interaction is what the
    algorithm rewards in the first hour (operator audit 2026-07-10)."""
    body = re.sub(r"(?:\s*#[^#\s]+)+\s*$", "", str(description or "").strip()).strip()
    if not body:
        return None  # covered by other checks; nothing to anchor a question to
    if body.endswith("?"):
        return None
    return (
        "Description must END with ONE short engagement question inviting a "
        "comment (e.g. \"¿Qué error cometes más? \") placed right before the hashtags."
    )


def _hard_trim_title(title: str) -> str:
    """Last-resort guarantee that a title is <= MAX_SHORT_TITLE_CHARS."""
    t = (title or "").strip()
    if len(t) <= MAX_SHORT_TITLE_CHARS:
        return t
    return t[:MAX_SHORT_TITLE_CHARS].rstrip(" ,.;:-–—…¿¡")


def _ordered_content_tokens(text: str) -> list[str]:
    """Content words in hook order, ORIGINAL form (case + accents preserved).

    Length + stopword filtering use an accent-stripped normalized form, but the
    returned words keep their accents so a synthesised title reads naturally
    (``título``, not ``titulo``).
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"\w+", str(text), re.UNICODE):
        norm = "".join(
            c for c in unicodedata.normalize("NFKD", raw.lower()) if not unicodedata.combining(c)
        )
        if len(norm) >= 3 and norm not in _TITLE_STOPWORDS and norm not in seen:
            seen.add(norm)
            out.append(raw)
    return out


def _fallback_title_from_hook(hook: str, min_age: int) -> str:
    """Deterministic, VALID scroll-stopper title synthesised from the hook.

    Used only when the LLM cannot produce a compliant title after all retries —
    a valid Call-Out formula that carries a real hook word (accents preserved) so
    it satisfies the formula + hook-alignment + <=40 checks, which beats silently
    publishing a broken one.
    """
    prefix = f"Si tienes más de {min_age}: "  # carries the "si tienes más de" signal
    budget = MAX_SHORT_TITLE_CHARS - len(prefix)
    # Prefer the hook PHRASE: pack leading hook words (stopwords included, so it
    # reads naturally) while they fit the budget. A single lonely word shipped a
    # skeletal 'Si tienes más de 45: Mide' on a live render — the fuller
    # 'Mide tu cansancio' fit all along.
    content = set(_ordered_content_tokens(hook))
    packed: list[str] = []
    used = 0
    for raw in re.findall(r"\S+", str(hook)):
        word = raw.strip(" ,.;:!?¡¿-–—…")
        if not word:
            continue
        extra = len(word) + (1 if packed else 0)
        if used + extra > budget:
            break
        packed.append(word)
        used += extra
    if packed and any(w in content for w in packed):
        return prefix + " ".join(packed)
    for word in _ordered_content_tokens(hook):
        if len(word) <= budget and len(prefix + word) <= MAX_SHORT_TITLE_CHARS:
            return prefix + word
    # No hook word fits (empty/pathological hook) — a generic Call Out is still a
    # valid formula and <=40; the hook-alignment check is skipped when hook is
    # empty. Never 'escucha esto': even the last resort must not ship a deictic.
    return f"Si tienes más de {min_age}, cuida tu salud"


# Generic gym/virality tags that almost never match a Spain-first wellness
# Short for 45+. Off-topic tags push the Short to the wrong audience and
# tank retention, so we strip them post-LLM as a hard safety net even when
# the prompt told the model not to emit them.
_FORBIDDEN_HASHTAGS = {
    "#gym", "#fitness", "#workout", "#crossfit", "#musculacion",
    "#musculación", "#pesas", "#cardio", "#abs", "#motivation",
    "#mindset", "#shortsviral", "#fyp", "#parati", "#viral",
    "#foryou", "#trending",
}

_DEFAULT_FALLBACK_HASHTAGS = ["#bienestar", "#vida45plus", "#saludable", "#shorts"]

# Repairable SEO mismatches (wrong title format, missing core action, off-topic
# hashtags) trigger a regeneration with cumulative feedback instead of a hard
# failure. Keep this small: the prompt itself already carries the rules.
MAX_SEO_RETRIES = 2

# ── Parent-job title uniqueness (spec 2026-07-13) ──────────────────────────
# Two Shorts from the SAME long-form parent must not publish the same or
# cosmetically-different SEO title (live repro: idea-01/07 both shipped
# "Si tienes más de 45, revisa tu sal", idea-09 differed only tu/la).

# Formula scaffolding that carries NO topical meaning — stripping it before
# comparison keeps "si tienes más de 45, X" vs "…, Y" from reading as similar
# just because the boilerplate matches, while still leaving the payload (X/Y)
# to decide duplication.
_TITLE_FORMULA_BOILERPLATE = (
    re.compile(r"\bsi tienes mas de\s+\d+\b"),
    re.compile(r"\ba los\s+\d+\s*\+?\b"),
    re.compile(r"\bmayores de\s+\d+\b"),
    re.compile(r"\bdespues de los\s+\d+\b"),
    re.compile(r"\ben\s+\d+\s+segundos\b"),
    re.compile(r"\bla verdad cientifica\b"),
    re.compile(r"\bla verdad\b"),
    re.compile(r"\bnecesitas saber\b"),
)
# Near-duplicate threshold on content-token Jaccard similarity. 0.8 keeps
# "revisa sal" vs "revisa sal" (tu/la variants → 1.0) a duplicate, while
# "revisa sal" vs "reduce sal" (0.33) stays distinct (AC2).
_TITLE_NEAR_DUPLICATE_THRESHOLD = 0.8


def _normalize_title_for_uniqueness(title: str) -> tuple[str, ...]:
    """Canonical content-token signature for parent-scoped title comparison.

    Accent/case/punctuation-insensitive, strips formula boilerplate and
    low-information determiners/pronouns (via ``_TITLE_STOPWORDS``) and pure
    numbers, then returns the SORTED remaining content tokens. Exact tuple
    equality is a canonical duplicate."""
    decomposed = unicodedata.normalize("NFKD", str(title or "").lower())
    ascii_text = "".join(c for c in decomposed if not unicodedata.combining(c))
    for pat in _TITLE_FORMULA_BOILERPLATE:
        ascii_text = pat.sub(" ", ascii_text)
    tokens = re.findall(r"[a-z0-9]+", ascii_text)
    content = [
        t for t in tokens
        if len(t) >= 3 and t not in _TITLE_STOPWORDS and not t.isdigit()
    ]
    return tuple(sorted(content))


def _title_uniqueness_issues(title: str, sibling_titles: list[str]) -> list[str]:
    """Deterministic collision report vs the parent's sibling titles.

    Exact canonical equality OR content-token Jaccard >= threshold counts as a
    duplicate; each issue string names the matched sibling so the retry feedback
    and audit can cite it. An empty sibling set never flags (AC11)."""
    cand = _normalize_title_for_uniqueness(title)
    if not cand:
        return []
    cand_set = set(cand)
    issues: list[str] = []
    for sibling in sibling_titles:
        sib = _normalize_title_for_uniqueness(sibling)
        if not sib:
            continue
        if cand == sib:
            issues.append(
                f"Title duplicates the sibling Short title {sibling!r} "
                "(same after removing formula boilerplate and determiners)."
            )
            continue
        sib_set = set(sib)
        union = cand_set | sib_set
        jaccard = len(cand_set & sib_set) / len(union) if union else 0.0
        if jaccard >= _TITLE_NEAR_DUPLICATE_THRESHOLD:
            issues.append(
                f"Title is a near-duplicate (similarity {jaccard:.2f}) of the "
                f"sibling Short title {sibling!r}."
            )
    return issues


# Bound on how long the final check-and-write waits for the parent title lock.
# Module-level so tests can shrink it; the value is a safety cap, not a tuning
# knob for throughput, and it never touches render concurrency.
_TITLE_LOCK_TIMEOUT_SEC = 10.0


@contextlib.contextmanager
def _parent_title_lock(lock_path: Path):
    """Bounded exclusive lock serializing the final title check-and-write.

    FAIL-CLOSED (spec §Generation final check): if the lock cannot be acquired
    within the bound, raise instead of yielding — a contended writer must NOT
    re-read and write outside the lock, or two builders could each pass their
    own stale snapshot and publish the same title. Non-blocking poll so a wedged
    holder surfaces as a clear bounded error rather than an unbounded hang."""
    fd = open(lock_path, "a+")
    acquired = False
    deadline = time.monotonic() + _TITLE_LOCK_TIMEOUT_SEC
    try:
        while True:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Could not acquire the parent Short-title lock ({lock_path}) "
                        f"within {_TITLE_LOCK_TIMEOUT_SEC}s; refusing to write SEO "
                        "without the lock to avoid a duplicate-title race."
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fd.close()


def _collect_sibling_short_titles(
    long_job_dir: Path, current_short_id: str | None = None
) -> list[str]:
    """Parent-scoped, deterministic sibling SEO titles (spec §Sibling discovery).

    Reads ``<parent>/shorts/<short_id>/json/short_seo.json`` (legacy fallback via
    ``resolve_short_json``), sorted by short_id. Ignores the current Short, other
    parents, missing/malformed artifacts, and empty titles."""
    shorts_root = paths.shorts_dir(Path(long_job_dir))
    if not shorts_root.exists():
        return []
    titles: list[str] = []
    for child in sorted(shorts_root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name == current_short_id:
            continue
        seo_path = paths.resolve_short_json(child, paths.SHORT_SEO_FILE)
        if not seo_path.exists():
            continue
        try:
            doc = json.loads(seo_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        title = str(doc.get("title") or "").strip()
        if title:
            titles.append(title)
    return titles


def _build_seo_retry_feedback(issues: list) -> str:
    lines = [
        "SEO RETRY FEEDBACK",
        "",
        "The previous SEO did not match the final Short. Fix these issues:",
    ]
    for n, issue in enumerate(issues, start=1):
        hint = f" {issue.repair_hint}" if getattr(issue, "repair_hint", None) else ""
        lines.append(f"{n}. [{issue.type}] {issue.detail}{hint}")
    lines.extend([
        "",
        "DO NOT REGRESS:",
        "- Do not use an \"errores\" title unless the Short is genuinely a mistake_list.",
        "- Keep the title a Spanish scroll-stopper, <= 40 characters, using one of the 4 Shorts title formulas (Warning / Quick Win / Myth-Buster / Call Out).",
        "- Keep 3-5 hashtags with #shorts last.",
    ])
    return "\n".join(lines)

_HASHTAG_ALIASES = {
    "#nutricion45": "#nutricion",
    "#nutrición45": "#nutricion",
}


def _parse(raw: str) -> dict:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def _invoke(llm_fn: Callable[..., str], kind: str, prompt: str) -> str:
    try:
        return llm_fn(prompt)
    except TypeError:
        return llm_fn(kind, prompt)


def _normalize_hashtags(raw_tags: Any) -> list[str]:
    """Lowercase, prefix '#', dedupe, drop forbidden + empty entries."""
    if not isinstance(raw_tags, list):
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for entry in raw_tags:
        if not isinstance(entry, str):
            continue
        raw = entry.strip().lower()
        if not raw:
            continue
        raw_parts = re.findall(r"#[^#\s]+", raw) if raw.count("#") > 1 else [raw]
        for raw_part in raw_parts:
            tag = raw_part.strip().lower()
            if not tag:
                continue
            if not tag.startswith("#"):
                tag = "#" + tag.lstrip("#")
            # Strip whitespace inside (LLM sometimes emits "# salud mental") and
            # punctuation around the token, while preserving Spanish letters.
            tag = "#" + re.sub(r"[^\wáéíóúüñ]+", "", "".join(tag[1:].split()), flags=re.IGNORECASE)
            tag = _HASHTAG_ALIASES.get(tag, tag)
            if not tag or tag == "#":
                continue
            if tag in _FORBIDDEN_HASHTAGS:
                continue
            if tag in seen:
                continue
            seen.add(tag)
            cleaned.append(tag)
    return cleaned


def _description_with_spaced_hashtags(description: str, hashtags: list[str]) -> str:
    """Ensure visible YouTube hashtags are separated and match normalized tags."""
    base = re.sub(r"(?:\s*#[^#\s]+)+\s*$", "", description.strip()).strip()
    tag_text = " ".join(hashtags)
    if not tag_text:
        return base
    return f"{base} {tag_text}".strip() if base else tag_text


def build_short_seo(
    long_job_dir: Path,
    short_id: str,
    short_plan: dict,
    short_script: dict,
    channel_config: dict,
    llm_fn: Callable[..., str],
    long_video_url: str = "",
    retention_plan: dict | None = None,
    history_recorder: Any = None,
) -> dict[str, Any]:
    funnel = (channel_config.get("shorts") or {}).get("funnel") or {}
    pinned_template = funnel.get("pinned_comment_template", "")
    trigger_question = str(((retention_plan or {}).get("comment_trigger") or {}).get("question") or "").strip()

    retry_feedback = ""
    seo: dict[str, Any] = {}
    for attempt in range(MAX_SEO_RETRIES + 1):
        # Refresh sibling titles before EVERY attempt (spec §Generation): the
        # prompt lists them so the model steers clear up front.
        used_titles = _collect_sibling_short_titles(long_job_dir, short_id)
        prompt = prompts.short_seo_prompt(
            channel_config, short_plan, short_script, long_video_url,
            retention_plan=retention_plan, retry_feedback=retry_feedback,
            used_titles=used_titles,
        )
        # Tag the history entry with the attempt number so a self-correction
        # regen reads as ``seo:attempt-2`` instead of a second generic ``seo``
        # that looks like a duplicate run.
        if history_recorder is not None:
            try:
                history_recorder.set_kind_hint(f"seo:attempt-{attempt + 1}")
            except Exception:  # pragma: no cover - tagging must never break SEO
                pass
        parsed = _parse(_invoke(llm_fn, "seo", prompt))
        # Prefer the LLM's pinned_comment (richer, on-topic, already a question).
        # The retention-plan trigger_question and the funnel template are only
        # FALLBACKS — never overwrite a good LLM comment with the short generic
        # trigger (that silently replaced full comments with "¿También te pasa?").
        pinned = (parsed.get("pinned_comment") or "").strip()
        if not pinned and trigger_question and "?" in trigger_question and not any(
            term in trigger_question.lower() for term in ("suscr", "urgente", "miedo", "cura")
        ):
            pinned = trigger_question
        if not pinned and pinned_template:
            pinned = pinned_template.replace("{long_video_url}", long_video_url)
        hashtags = _normalize_hashtags(parsed.get("hashtags"))
        if not hashtags:
            hashtags = list(_DEFAULT_FALLBACK_HASHTAGS)
        # YouTube allows many tags but the spec asks for 3-5 visible hashtags.
        hashtags = hashtags[:5]
        title = (parsed.get("title") or short_script.get("hook", "")).strip()
        hook = str(short_script.get("hook") or "")
        description = _description_with_spaced_hashtags((parsed.get("description") or "").strip(), hashtags)
        seo = {
            "short_id": short_id,
            "title": title,
            "description": description,
            "hashtags": hashtags,
            "pinned_comment": (pinned or "").strip(),
            "long_video_url": long_video_url,
            "language": "es-ES",
            "ai_disclosure": True,
        }
        issues = validate_seo_idea_consistency(seo, short_script)
        blocking = [i for i in issues if i.severity == "blocking_error"]
        repairable = [i for i in issues if i.severity == "repairable_error"]
        title_problems = _title_issues(title, hook)
        topic_tokens = _topic_tokens(short_plan)
        topic_problems = _seo_topic_issues(title, description, hashtags, topic_tokens)
        engagement_problem = _seo_engagement_issue(description)
        if engagement_problem:
            topic_problems = [*topic_problems, engagement_problem]
        # Uniqueness gate uses a FRESH sibling read (not the attempt-start list),
        # so a sibling that finished SEO WHILE this attempt was generating is
        # still caught — this is the stale-snapshot close (spec §Generation 6).
        gate_siblings = _collect_sibling_short_titles(long_job_dir, short_id)
        uniqueness_problems = _title_uniqueness_issues(title, gate_siblings)
        if blocking:
            detail = "; ".join(i.detail for i in blocking)
            raise ValueError(f"SEO idea fidelity validation failed: {detail}")
        if not repairable and not title_problems and not topic_problems and not uniqueness_problems:
            break
        if attempt >= MAX_SEO_RETRIES:
            # A duplicate title must NEVER be published, appended with a number,
            # or papered over by a fallback — fail loudly (spec §Generation).
            if uniqueness_problems:
                raise ValueError(
                    "Could not produce a parent-unique Short title after "
                    f"{MAX_SEO_RETRIES} retries: {uniqueness_problems[0]}"
                )
            # Idea fidelity is a hard contract — still fails loudly.
            if repairable:
                detail = "; ".join(i.detail for i in repairable)
                raise ValueError(
                    f"SEO idea fidelity validation failed after {MAX_SEO_RETRIES} retries: {detail}"
                )
            if title_problems:
                # NEVER silently publish a title that fails the scroll-stopper
                # contract. Replace it with a deterministic, VALID formula title
                # synthesised from the hook. If even that cannot be made valid
                # (pathological hook), fail loudly rather than ship a bad title.
                min_age = resolve_target_min_age(
                    channel_config,
                    str(short_plan.get("title") or ""),
                    hook,
                    str(short_script.get("narration") or "")[:400],
                )
                fallback = _fallback_title_from_hook(hook, min_age)
                if _title_issues(fallback, hook):
                    raise ValueError(
                        "Could not produce a valid scroll-stopper Short title after "
                        f"{MAX_SEO_RETRIES} retries (last invalid title: {title!r})."
                    )
                seo["title"] = fallback
            else:
                seo["title"] = _hard_trim_title(title)
            # Topic hashtag is deterministically repairable: prepend a tag built
            # from the strongest topic token so the Short stays searchable even
            # when the model kept emitting only generic wellness tags. Title and
            # description keep the best LLM attempt (retries carry the burden).
            if topic_problems and topic_tokens:
                stems = {t[:4] for t in topic_tokens}
                has_topical = any(
                    any(stem in tag.lstrip("#") for stem in stems) for tag in seo["hashtags"]
                )
                if not has_topical:
                    topic_tag = "#" + sorted(topic_tokens, key=len, reverse=True)[0]
                    seo["hashtags"] = ([topic_tag] + seo["hashtags"])[:5]
                    seo["description"] = _description_with_spaced_hashtags(
                        seo["description"], seo["hashtags"]
                    )
            # NEVER ship a hashtags-only description (live repro: the model
            # returned an empty description on every attempt). Synthesize a
            # deterministic body from the narration's first sentence plus an
            # engagement question so the artifact stays useful and on-contract.
            desc_body = re.sub(
                r"(?:\s*#[^#\s]+)+\s*$", "", str(seo.get("description") or "").strip()
            ).strip()
            if not desc_body:
                narration = str(short_script.get("narration") or "")
                first_sentence = re.split(r"(?<=[.!?])\s+", narration.strip())[0].strip()
                body = (first_sentence or hook or "").strip().rstrip(".")[:150]
                fallback_body = f"{body}. ¿Y tú, ya lo haces?" if body else "¿Y tú, ya lo haces?"
                seo["description"] = _description_with_spaced_hashtags(
                    fallback_body, seo["hashtags"]
                )
            break
        # Regenerate SEO with cumulative feedback so the model can self-correct.
        feedback_parts = []
        if repairable:
            feedback_parts.append(_build_seo_retry_feedback(repairable))
        if title_problems:
            feedback_parts.append(
                "TITLE FIXES REQUIRED (scroll-stopper contract):\n"
                + "\n".join(f"- {p}" for p in title_problems)
            )
        if topic_problems:
            feedback_parts.append(
                "TOPIC KEYWORD FIXES REQUIRED (SEO classification contract):\n"
                + "\n".join(f"- {p}" for p in topic_problems)
            )
        if uniqueness_problems:
            feedback_parts.append(
                "DUPLICATE TITLE — a sibling Short from THIS SAME video already "
                "uses a title too similar to yours. Choose a clearly different "
                "angle/object/action (not just a tu/la or single-word swap):\n"
                + "\n".join(f"- {p}" for p in uniqueness_problems)
            )
        retry_feedback = "\n\n".join(feedback_parts)

    # Final atomic check-and-write under a bounded parent-level lock so two
    # sibling Shorts finishing SEO concurrently cannot each pass their own
    # snapshot and then both persist a collision (spec §Generation final check).
    jd = paths.short_json_dir(long_job_dir, short_id)
    jd.mkdir(parents=True, exist_ok=True)
    lock_path = paths.shorts_dir(Path(long_job_dir)) / ".seo-title.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _parent_title_lock(lock_path):
        final_siblings = _collect_sibling_short_titles(long_job_dir, short_id)
        final_dupe = _title_uniqueness_issues(seo["title"], final_siblings)
        if final_dupe:
            raise ValueError(
                "Refusing to publish a duplicate Short title "
                f"(final pre-write check): {final_dupe[0]}"
            )
        atomic_write_json(jd / paths.SHORT_SEO_FILE, seo)
    return seo
