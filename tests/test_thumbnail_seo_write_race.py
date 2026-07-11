"""Regression tests for bug-528: thumbnail stage clobbering concurrent seo.json updates.

The thumbnail_image stage reads seo.json at stage start, spends ~40 minutes
generating images, then writes its in-memory seo object back to attach
``thumbnail_path``. Meanwhile the whisper_timestamps stage (running
concurrently under the DAG scheduler) rewrites the description's YouTube
chapter block against real audio durations. The stale write-back reverted
that resync, shipping chapter timestamps computed from planned durations
(observed: chapters up to 21:55 on a 15:48 video).
"""

from __future__ import annotations

import json
from pathlib import Path

from video_agent.orchestrator.stages.assets_thumbnail import _persist_thumbnail_path


def _write_seo(path: Path, description: str) -> None:
    path.write_text(
        json.dumps({"title": "T", "description": description, "thumbnail_path": ""}),
        encoding="utf-8",
    )


def test_persist_thumbnail_path_preserves_concurrent_description_update(tmp_path):
    seo_path = tmp_path / "seo.json"
    _write_seo(seo_path, "old description\n\n00:00 - A\n21:55 - Way too far\n")
    stale_snapshot = json.loads(seo_path.read_text(encoding="utf-8"))

    # Concurrent whisper resync rewrites the description on disk while the
    # thumbnail stage still holds the stale snapshot in memory.
    _write_seo(seo_path, "fixed description\n\n00:00 - A\n15:01 - Real end\n")

    _persist_thumbnail_path(seo_path, stale_snapshot, "jobs/x/outputs/thumbnail_1.jpg")

    final = json.loads(seo_path.read_text(encoding="utf-8"))
    assert final["thumbnail_path"] == "jobs/x/outputs/thumbnail_1.jpg"
    assert "fixed description" in final["description"]
    assert "21:55" not in final["description"]


def test_persist_thumbnail_path_fails_loudly_on_unreadable_file(tmp_path):
    """bug-529 acceptance rule: NEVER recover an invalid seo.json by writing a
    stale full snapshot over it — a corrupt artifact fails the stage loudly and
    the file is left exactly as found for a human/repair pass."""
    import pytest

    seo_path = tmp_path / "seo.json"
    snapshot = {"title": "T", "description": "snapshot description", "thumbnail_path": ""}
    corrupt = "{not valid json"
    seo_path.write_text(corrupt, encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        _persist_thumbnail_path(seo_path, snapshot, "jobs/x/outputs/thumbnail_1.jpg")

    assert seo_path.read_text(encoding="utf-8") == corrupt  # untouched


# ── bug-529 round 2: robust serialization, not a smaller window ───────────────

import threading  # noqa: E402  (regression block appended below original tests)

from video_agent.operator import update_seo_fields  # noqa: E402


def test_update_seo_fields_touches_only_the_given_fields(tmp_path):
    """A writer holding a full stale snapshot must be structurally unable to
    resurrect other writers' fields: updates are merged into a FRESH read
    inside the lock, only the named fields change."""
    seo_path = tmp_path / "seo.json"
    _write_seo(seo_path, "fixed description")

    update_seo_fields(seo_path, {"thumbnail_path": "jobs/x/thumb.jpg"})

    final = json.loads(seo_path.read_text(encoding="utf-8"))
    assert final["thumbnail_path"] == "jobs/x/thumb.jpg"
    assert final["description"] == "fixed description"
    assert final["title"] == "T"


def test_update_seo_fields_deterministic_interleaving(tmp_path):
    """Deterministic interleaving regression: writer A (description) enters the
    critical section first and is held there while writer B (thumbnail_path)
    arrives; B must serialize behind A and BOTH fields must survive."""
    seo_path = tmp_path / "seo.json"
    _write_seo(seo_path, "old description")

    a_inside = threading.Event()
    release_a = threading.Event()

    import video_agent.operator as operator_mod
    real_atomic = operator_mod.atomic_write_json

    def slow_atomic(path, payload):
        # Only writer A (the description update) is slowed, deterministically.
        if "NEW chapters" in str(payload.get("description", "")):
            a_inside.set()
            assert release_a.wait(timeout=10)
        return real_atomic(path, payload)

    operator_mod.atomic_write_json = slow_atomic
    try:
        a = threading.Thread(
            target=update_seo_fields,
            args=(seo_path, {"description": "NEW chapters description"}),
        )
        b = threading.Thread(
            target=update_seo_fields,
            args=(seo_path, {"thumbnail_path": "jobs/x/thumb.jpg"}),
        )
        a.start()
        assert a_inside.wait(timeout=10)  # A is inside its critical section
        b.start()  # B now contends for the lock while A is mid-write
        release_a.set()
        a.join(timeout=10)
        b.join(timeout=10)
        assert not a.is_alive() and not b.is_alive()
    finally:
        operator_mod.atomic_write_json = real_atomic

    final = json.loads(seo_path.read_text(encoding="utf-8"))
    assert final["description"] == "NEW chapters description"
    assert final["thumbnail_path"] == "jobs/x/thumb.jpg"


def test_update_seo_fields_many_concurrent_writers_lose_nothing(tmp_path):
    seo_path = tmp_path / "seo.json"
    _write_seo(seo_path, "d")

    threads = [
        threading.Thread(target=update_seo_fields, args=(seo_path, {f"field_{i}": i}))
        for i in range(12)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    final = json.loads(seo_path.read_text(encoding="utf-8"))
    for i in range(12):
        assert final[f"field_{i}"] == i
    assert final["description"] == "d"
