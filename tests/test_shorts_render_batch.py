"""Acceptance tests for durable, sequential multi-idea Short render batches.

These tests intentionally describe the new public internal contract before its
implementation. Claude must make them green without deleting, skipping,
xfailing, or weakening them.
"""

from __future__ import annotations

import json

import pytest

from video_agent.shorts.render_batch import RenderBatchStore


def _ideas():
    return [
        {"idea_id": "idea-03", "title": "Tercera"},
        {"idea_id": "idea-01", "title": "Primera"},
        {"idea_id": "idea-08", "title": "Octava"},
    ]


def test_batch_creation_preserves_selection_order_and_derived_counts(tmp_path):
    store = RenderBatchStore(tmp_path)

    batch = store.create(
        batch_id="srb-test-1",
        ideas=_ideas(),
        short_type="infographic",
        force=False,
        generation_id="ideas-1",
    )

    assert batch["schema_version"] == "shorts_render_batch.v1"
    assert [item["idea_id"] for item in batch["items"]] == ["idea-03", "idea-01", "idea-08"]
    assert [item["position"] for item in batch["items"]] == [1, 2, 3]
    assert batch["status"] == "queued"
    assert batch["total_count"] == 3
    assert batch["completed_count"] == 0
    assert batch["failed_count"] == 0
    assert batch["remaining_count"] == 3
    assert json.loads(store.path.read_text(encoding="utf-8")) == batch


@pytest.mark.parametrize(
    "ideas",
    [[], [{"idea_id": "idea-01", "title": "A"}, {"idea_id": "idea-01", "title": "A again"}]],
)
def test_batch_creation_rejects_empty_or_duplicate_idea_ids(tmp_path, ideas):
    store = RenderBatchStore(tmp_path)

    with pytest.raises(ValueError):
        store.create(
            batch_id="srb-invalid",
            ideas=ideas,
            short_type="infographic",
            force=False,
            generation_id=None,
        )


def test_lifecycle_keeps_exact_counts_and_only_one_running_item(tmp_path):
    store = RenderBatchStore(tmp_path)
    store.create(
        batch_id="srb-life",
        ideas=_ideas(),
        short_type="narrated",
        force=False,
        generation_id="ideas-1",
    )

    running = store.start_item("idea-03")
    assert running["status"] == "running"
    assert running["current_idea_id"] == "idea-03"
    assert running["current_position"] == 1
    assert running["remaining_count"] == 3
    with pytest.raises(ValueError):
        store.start_item("idea-01")

    after_first = store.complete_item("idea-03", short_id="short-11")
    assert after_first["completed_count"] == 1
    assert after_first["remaining_count"] == 2
    assert after_first["items"][0]["short_id"] == "short-11"

    store.start_item("idea-01")
    after_failure = store.fail_item("idea-01", error="poster generation failed")
    assert after_failure["failed_count"] == 1
    assert after_failure["remaining_count"] == 1

    store.start_item("idea-08")
    store.complete_item("idea-08", short_id="short-12")
    terminal = store.finish()
    assert terminal["status"] == "completed_with_errors"
    assert terminal["completed_count"] == 2
    assert terminal["failed_count"] == 1
    assert terminal["remaining_count"] == 0


def test_restart_recovery_retries_only_interrupted_item(tmp_path):
    store = RenderBatchStore(tmp_path)
    store.create(
        batch_id="srb-resume",
        ideas=_ideas(),
        short_type="infographic",
        force=True,
        generation_id="ideas-1",
    )
    store.start_item("idea-03")
    store.complete_item("idea-03", short_id="short-done")
    store.start_item("idea-01")

    recovered = store.recover_for_resume()

    assert recovered["items"][0]["status"] == "completed"
    assert recovered["items"][0]["short_id"] == "short-done"
    assert recovered["items"][1]["status"] == "pending"
    assert recovered["items"][2]["status"] == "pending"
    assert recovered["completed_count"] == 1
    assert recovered["remaining_count"] == 2
    assert store.pending_idea_ids() == ["idea-01", "idea-08"]


def test_cancel_marks_running_and_pending_items_without_touching_completed(tmp_path):
    store = RenderBatchStore(tmp_path)
    store.create(
        batch_id="srb-cancel",
        ideas=_ideas(),
        short_type="infographic",
        force=False,
        generation_id="ideas-1",
    )
    store.start_item("idea-03")
    store.complete_item("idea-03", short_id="short-done")
    store.start_item("idea-01")

    cancelled = store.cancel(error="operator requested stop")

    assert cancelled["status"] == "cancelled"
    assert [item["status"] for item in cancelled["items"]] == ["completed", "cancelled", "cancelled"]
    assert cancelled["remaining_count"] == 0

