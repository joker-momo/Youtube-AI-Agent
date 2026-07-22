"""Generate the infographic poster PNG via the injected image function.

This is the seam where a poster's palette stops being a pure function and becomes
a committed decision: it is chosen once, written to ``json/poster_palette.json``
before any image is requested, and reused verbatim by every later retry. Sibling
Shorts read each other's committed decisions so consecutive posters under one
parent job do not ship the same look (bug-546).
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from video_agent.orchestrator.image_prompt_log import safe_log_image_prompt
from video_agent.shorts import paths
from video_agent.shorts.infographic.poster_prompt import (
    _content_fingerprint,
    build_poster_body,
    effective_palette_fingerprint,
    effective_palette_values,
    effective_scheme_ids,
    select_palette_contract,
    validate_palette_contract,
    wrap_poster_body,
)
from video_agent.storage.locks import file_lock
from video_agent.utils.json_io import read_json, write_json

# Only ever a local read-decide-write of small JSON; a wait longer than this means
# a stuck holder, and failing loudly beats two siblings racing to the same look.
_PALETTE_LOCK_TIMEOUT_SEC = 10.0
# How far back anti-repeat looks. Bounded on purpose (KTD5): two posters is what a
# viewer holds in mind scrolling a feed, and it keeps this off global state.
_RECENT_SIBLING_LIMIT = 2


def palette_path(short_dir: Path) -> Path:
    """Where a Short's frozen palette decision lives."""
    return Path(short_dir) / "json" / paths.SHORT_POSTER_PALETTE_FILE


def _load_valid_contract(
    path: Path,
    expected_fingerprint: str,
    *,
    allowed_values: frozenset[str],
    canonical_scheme_ids: frozenset[str],
    expected_content_fingerprint: str | None = None,
) -> dict[str, Any] | None:
    """A trustworthy contract from ``path``, or None if there is nothing usable.

    Anything unreadable, malformed, stale against the active Style DNA, off-palette,
    non-canonical (bug-546 reopen), or whose own contrast evidence no longer holds
    is treated as absent — never repaired in place, because a half-trusted palette
    is how unreadable OR off-brand posters ship.
    """
    try:
        data = read_json(path)
    except (OSError, ValueError):
        return None
    if not validate_palette_contract(
        data,
        expected_fingerprint,
        allowed_values=allowed_values,
        canonical_scheme_ids=canonical_scheme_ids,
        expected_content_fingerprint=expected_content_fingerprint,
    ):
        return None
    if not isinstance(data.get("selected_at_utc"), str) or not isinstance(data.get("short_id"), str):
        return None
    return data


def _recent_sibling_contracts(
    short_dir: Path,
    expected_fingerprint: str,
    allowed_values: frozenset[str],
    canonical_scheme_ids: frozenset[str],
) -> tuple[dict[str, Any], ...]:
    """The most recent valid sibling decisions, newest first.

    Ordered by the immutable ``(selected_at_utc, short_id)`` pair rather than file
    mtime, so a resumed batch, a shuffled directory listing or two Shorts committed
    in the same clock tick still produce one deterministic history (R13).

    Sibling sidecars are validated for palette membership but NOT content
    fingerprint — a sibling's content legitimately differs from this Short's.
    """
    parent = Path(short_dir).parent
    current = Path(short_dir).resolve()
    contracts: list[dict[str, Any]] = []
    try:
        siblings = sorted(parent.iterdir())
    except OSError:
        return ()
    for sibling in siblings:
        if not sibling.is_dir() or sibling.resolve() == current:
            continue
        contract = _load_valid_contract(
            palette_path(sibling), expected_fingerprint,
            allowed_values=allowed_values, canonical_scheme_ids=canonical_scheme_ids,
        )
        if contract is not None:
            contracts.append(contract)
    contracts.sort(key=lambda c: (c["selected_at_utc"], c["short_id"]), reverse=True)
    return tuple(contracts[:_RECENT_SIBLING_LIMIT])


def resolve_poster_palette(
    short_dir: Path, plan: dict[str, Any], channel_config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The committed palette decision for this Short, selecting and persisting on
    first call and returning the identical stored decision on every retry (R12)."""
    short_dir = Path(short_dir)
    path = palette_path(short_dir)
    expected_fingerprint = effective_palette_fingerprint(channel_config)
    allowed_values = effective_palette_values(channel_config)
    canonical_ids = effective_scheme_ids(channel_config)
    own_content = _content_fingerprint(plan)

    existing = _load_valid_contract(
        path, expected_fingerprint, allowed_values=allowed_values,
        canonical_scheme_ids=canonical_ids, expected_content_fingerprint=own_content,
    )
    if existing is not None:
        return existing

    lock_path = short_dir.parent / paths.POSTER_PALETTE_LOCK_FILE
    # The lock spans read-history -> select -> persist so two siblings starting
    # together cannot both look at an empty history and pick the same scheme. It is
    # released before any prompt logging or image call: those are slow and external,
    # and holding a lock across them would serialize the whole batch (R20).
    with file_lock(lock_path, timeout_sec=_PALETTE_LOCK_TIMEOUT_SEC):
        existing = _load_valid_contract(
            path, expected_fingerprint, allowed_values=allowed_values,
            canonical_scheme_ids=canonical_ids, expected_content_fingerprint=own_content,
        )
        if existing is not None:
            return existing
        recent = _recent_sibling_contracts(short_dir, expected_fingerprint, allowed_values, canonical_ids)
        contract = select_palette_contract(plan, channel_config, recent=recent)
        contract["selected_at_utc"] = datetime.now(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
        contract["short_id"] = short_dir.name
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_json(path, contract)
        except OSError as exc:
            raise RuntimeError(
                f"could not persist the poster palette to {path}: {exc}. Refusing to "
                "generate a poster whose palette no retry could reproduce."
            ) from exc
    return contract


async def generate_poster(
    short_dir: Path, plan: dict[str, Any], image_fn, channel_config: dict[str, Any] | None = None
) -> Path:
    """Generate the infographic poster image for a short.

    Args:
        short_dir: The short's root directory.
        plan: The poster plan dict (contains title, items, format, etc.).
        image_fn: Async image generation function that accepts prompt, project_name,
                  out_path, and aspect_ratio kwargs.
        channel_config: Supplies the channel's brand identity as creative direction
                        for the poster (never rendered as on-screen text/watermark).

    Returns:
        Path to the generated poster PNG.
    """
    short_dir = Path(short_dir)
    out_path = short_dir / "assets" / paths.SHORT_POSTER_IMAGE_NAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Commit the palette BEFORE asking for pixels (R11): if this Short is retried,
    # the sidecar — not a re-run of the selector against newer history — decides.
    contract = resolve_poster_palette(short_dir, plan, channel_config)
    # ONE body per generation (KTD4/R15): the audit log wraps this exact string and
    # image_fn receives it verbatim, so the logged effective palette is provably
    # the palette that reached the model — never an independently recomputed one.
    body = build_poster_body(plan, channel_config, palette_contract=contract)
    # Log the full wrapped prompt (what conceptually reaches ChatGPT) for audit...
    safe_log_image_prompt(
        short_dir,
        stage="infographic",
        kind="infographic_poster",
        prompt=wrap_poster_body(body),
        out_path=str(out_path),
        aspect_ratio="9:16",
    )
    # ...but pass the RAW body: the driver prepends the dimension instruction once.
    await image_fn(
        prompt=body,
        project_name=f"{short_dir.name[:38]}-poster"[:45],
        out_path=str(out_path),
        aspect_ratio="9:16",
    )
    return out_path
