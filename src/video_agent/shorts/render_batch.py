"""Durable, sequential multi-idea Short render batches (shorts_render_batch.v1).

Single owner of the batch schema, validation, atomic persistence, derived
counts, lifecycle transitions, and restart recovery — the route, worker, and
both sequential render loops all go through :class:`RenderBatchStore` so batch
JSON mutation logic is never duplicated.

Spec: docs/specs/2026-07-13-shorts-multi-idea-sequential-render-batch.md
"""
from __future__ import annotations

import datetime as _dt
import secrets
from pathlib import Path
from typing import Any

from video_agent.shorts import paths
from video_agent.storage.atomic import atomic_write_json

SCHEMA_VERSION = "shorts_render_batch.v1"

BATCH_STATES = {"queued", "running", "completed", "completed_with_errors", "failed", "cancelled"}
TERMINAL_BATCH_STATES = {"completed", "completed_with_errors", "failed", "cancelled"}
ITEM_STATES = {"pending", "running", "completed", "failed", "cancelled"}

SHORT_TYPES = {"infographic", "narrated"}
MAX_BATCH_IDEAS = 20
_MAX_ERROR_CHARS = 500


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_batch_id() -> str:
    ts = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"srb-{ts}-{secrets.token_hex(3)}"


def _bounded_error(error: Any) -> str:
    text = str(error or "").strip() or "unknown error"
    return text[:_MAX_ERROR_CHARS]


class RenderBatchStore:
    """Atomic reader/writer for one parent job's render batch document."""

    def __init__(self, long_job_dir: Path):
        self.long_job_dir = Path(long_job_dir)
        self.path = paths.render_batch_path(self.long_job_dir)

    # ------------------------------------------------------------------ io --
    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        import json

        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return doc if isinstance(doc, dict) else None

    def _require(self) -> dict[str, Any]:
        doc = self.load()
        if doc is None:
            raise ValueError(f"No render batch document at {self.path}")
        return doc

    def _save(self, doc: dict[str, Any]) -> dict[str, Any]:
        doc["updated_at"] = _now()
        self._recount(doc)
        atomic_write_json(self.path, doc)
        return doc

    # ------------------------------------------------------------ invariants --
    @staticmethod
    def _recount(doc: dict[str, Any]) -> None:
        items = doc.get("items") or []
        doc["total_count"] = len(items)
        doc["completed_count"] = sum(1 for i in items if i.get("status") == "completed")
        doc["failed_count"] = sum(1 for i in items if i.get("status") == "failed")
        doc["remaining_count"] = sum(
            1 for i in items if i.get("status") in ("pending", "running")
        )

    def is_active(self) -> bool:
        """True while a persisted batch exists in a non-terminal state."""
        doc = self.load()
        return bool(doc) and str(doc.get("status")) not in TERMINAL_BATCH_STATES

    # -------------------------------------------------------------- lifecycle --
    def create(
        self,
        *,
        batch_id: str,
        ideas: list[dict[str, Any]],
        short_type: str,
        force: bool,
        generation_id: str | None,
    ) -> dict[str, Any]:
        if not ideas:
            raise ValueError("A render batch needs at least one idea")
        if len(ideas) > MAX_BATCH_IDEAS:
            raise ValueError(f"A render batch is capped at {MAX_BATCH_IDEAS} ideas")
        idea_ids = [str(i.get("idea_id") or "").strip() for i in ideas]
        if any(not idea_id for idea_id in idea_ids):
            raise ValueError("Every batch idea needs a non-empty idea_id")
        if len(set(idea_ids)) != len(idea_ids):
            raise ValueError("Duplicate idea IDs are not allowed in a render batch")
        normalized_type = str(short_type or "").strip().lower()
        if normalized_type not in SHORT_TYPES:
            raise ValueError(f"short_type must be one of {sorted(SHORT_TYPES)}")

        now = _now()
        doc: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "batch_id": str(batch_id),
            "source_long_job_id": self.long_job_dir.name,
            "generation_id": generation_id,
            "short_type": normalized_type,
            "force": bool(force),
            "status": "queued",
            "created_at": now,
            "started_at": None,
            "updated_at": now,
            "completed_at": None,
            "current_idea_id": None,
            "current_position": None,
            "items": [
                {
                    "position": position,
                    "idea_id": idea_id,
                    "title": str(idea.get("title") or ""),
                    "status": "pending",
                    "short_id": None,
                    "error": None,
                    "started_at": None,
                    "completed_at": None,
                }
                for position, (idea_id, idea) in enumerate(zip(idea_ids, ideas, strict=True), start=1)
            ],
        }
        return self._save(doc)

    def _item(self, doc: dict[str, Any], idea_id: str) -> dict[str, Any]:
        for item in doc.get("items") or []:
            if item.get("idea_id") == idea_id:
                return item
        raise ValueError(f"Unknown batch idea: {idea_id}")

    def start_item(self, idea_id: str) -> dict[str, Any]:
        doc = self._require()
        running = [i for i in doc["items"] if i.get("status") == "running"]
        if running:
            raise ValueError(
                f"Item {running[0].get('idea_id')} is already running; "
                "a batch renders one idea at a time"
            )
        item = self._item(doc, idea_id)
        if item.get("status") != "pending":
            raise ValueError(
                f"Item {idea_id} is {item.get('status')}, not pending — cannot start it"
            )
        now = _now()
        item["status"] = "running"
        item["started_at"] = now
        doc["status"] = "running"
        if not doc.get("started_at"):
            doc["started_at"] = now
        doc["current_idea_id"] = idea_id
        doc["current_position"] = item["position"]
        return self._save(doc)

    def _finish_item(self, idea_id: str, status: str, **fields: Any) -> dict[str, Any]:
        doc = self._require()
        item = self._item(doc, idea_id)
        if item.get("status") != "running":
            raise ValueError(
                f"Item {idea_id} is {item.get('status')}, not running — cannot finish it"
            )
        item["status"] = status
        item["completed_at"] = _now()
        item.update(fields)
        if doc.get("current_idea_id") == idea_id:
            doc["current_idea_id"] = None
            doc["current_position"] = None
        return self._save(doc)

    def complete_item(self, idea_id: str, *, short_id: str | None = None) -> dict[str, Any]:
        return self._finish_item(idea_id, "completed", short_id=short_id)

    def fail_item(self, idea_id: str, *, error: Any) -> dict[str, Any]:
        return self._finish_item(idea_id, "failed", error=_bounded_error(error))

    def finish(self) -> dict[str, Any]:
        """Terminal status from item outcomes (spec §8.8)."""
        doc = self._require()
        completed = sum(1 for i in doc["items"] if i.get("status") == "completed")
        failed = sum(1 for i in doc["items"] if i.get("status") == "failed")
        if failed and completed:
            doc["status"] = "completed_with_errors"
        elif failed:
            doc["status"] = "failed"
        else:
            doc["status"] = "completed"
        doc["completed_at"] = _now()
        doc["current_idea_id"] = None
        doc["current_position"] = None
        return self._save(doc)

    def cancel(self, *, error: Any = None) -> dict[str, Any]:
        """Explicit stop: current and pending items become cancelled."""
        doc = self._require()
        reason = _bounded_error(error) if error else "cancelled"
        now = _now()
        for item in doc["items"]:
            if item.get("status") in ("pending", "running"):
                item["status"] = "cancelled"
                item["error"] = reason
                item["completed_at"] = now
        doc["status"] = "cancelled"
        doc["completed_at"] = now
        doc["current_idea_id"] = None
        doc["current_position"] = None
        return self._save(doc)

    def mark_failed(self, *, error: Any) -> dict[str, Any]:
        """Whole-batch failure before/without item execution (e.g. enqueue_failed)."""
        doc = self._require()
        doc["status"] = "failed"
        doc["error"] = _bounded_error(error)
        doc["completed_at"] = _now()
        return self._save(doc)

    # --------------------------------------------------------------- recovery --
    def recover_for_resume(self) -> dict[str, Any]:
        """Idempotent restart recovery: only a stale running item is retried.

        Completed/failed/cancelled items keep their outcome; the interrupted
        ``running`` item returns to ``pending`` so the sequential loop picks it
        up first, in original order.
        """
        doc = self._require()
        for item in doc["items"]:
            if item.get("status") == "running":
                item["status"] = "pending"
                item["started_at"] = None
        doc["current_idea_id"] = None
        doc["current_position"] = None
        if str(doc.get("status")) not in TERMINAL_BATCH_STATES:
            doc["status"] = "running" if doc.get("started_at") else "queued"
        return self._save(doc)

    def pending_idea_ids(self) -> list[str]:
        doc = self._require()
        return [
            str(item.get("idea_id"))
            for item in doc.get("items") or []
            if item.get("status") == "pending"
        ]


class BatchProgress:
    """Progress callbacks shared by the narrated and infographic loops.

    Wraps a :class:`RenderBatchStore` so both render modes emit the same
    deterministic event sequence (spec §9): item_started -> item_completed |
    item_failed per item, then batch_finished once.
    """

    def __init__(self, store: RenderBatchStore):
        self.store = store

    def item_started(self, idea_id: str) -> None:
        self.store.start_item(idea_id)

    def item_completed(self, idea_id: str, short_id: str | None) -> None:
        self.store.complete_item(idea_id, short_id=short_id)

    def item_failed(self, idea_id: str, error: Any) -> None:
        self.store.fail_item(idea_id, error=error)

    def batch_finished(self) -> dict[str, Any]:
        return self.store.finish()

    def batch_cancelled(self, error: Any = None) -> dict[str, Any]:
        return self.store.cancel(error=error)


IDLE_SNAPSHOT: dict[str, Any] = {
    "status": "idle",
    "total_count": 0,
    "completed_count": 0,
    "failed_count": 0,
    "remaining_count": 0,
    "items": [],
}
