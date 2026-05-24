from __future__ import annotations

import json
import threading
import time

import pytest

from video_agent.storage.atomic import (
    append_jsonl_locked,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
)
from video_agent.storage.locks import FileLockTimeout, file_lock


def test_atomic_write_json_writes_valid_json(tmp_path):
    path = tmp_path / "nested" / "artifact.json"

    atomic_write_json(path, {"ok": True, "items": [1, 2]})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "ok": True,
        "items": [1, 2],
    }


def test_atomic_write_text_replaces_existing_file(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("old", encoding="utf-8")

    atomic_write_text(path, "new", encoding="utf-8")

    assert path.read_text(encoding="utf-8") == "new"


def test_atomic_write_bytes_replaces_existing_file(tmp_path):
    path = tmp_path / "image.bin"
    path.write_bytes(b"old")

    atomic_write_bytes(path, b"\x00\x01new")

    assert path.read_bytes() == b"\x00\x01new"


def test_atomic_write_json_replace_failure_preserves_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "artifact.json"
    path.write_text('{"old": true}\n', encoding="utf-8")

    def fail_replace(src, dst):
        raise RuntimeError("replace failed")

    monkeypatch.setattr("video_agent.storage.atomic.os.replace", fail_replace)

    with pytest.raises(RuntimeError, match="replace failed"):
        atomic_write_json(path, {"old": False})

    assert json.loads(path.read_text(encoding="utf-8")) == {"old": True}
    assert list(tmp_path.glob(".artifact.json.*.tmp")) == []


def test_append_jsonl_locked_preserves_concurrent_events(tmp_path):
    path = tmp_path / "events.jsonl"

    def write_range(offset: int) -> None:
        for i in range(50):
            append_jsonl_locked(path, {"idx": offset + i})

    threads = [threading.Thread(target=write_range, args=(n * 50,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 200
    assert sorted(row["idx"] for row in rows) == list(range(200))


def test_file_lock_blocks_concurrent_writer(tmp_path):
    lock_path = tmp_path / ".job.lock"
    acquired_at: list[float] = []

    def contender() -> None:
        with file_lock(lock_path, timeout_sec=2):
            acquired_at.append(time.monotonic())

    with file_lock(lock_path, timeout_sec=2):
        thread = threading.Thread(target=contender)
        start = time.monotonic()
        thread.start()
        time.sleep(0.15)
        assert acquired_at == []

    thread.join(timeout=2)
    assert len(acquired_at) == 1
    assert acquired_at[0] - start >= 0.12


def test_file_lock_timeout(tmp_path):
    lock_path = tmp_path / ".job.lock"

    with file_lock(lock_path, timeout_sec=2):
        with pytest.raises(FileLockTimeout):
            with file_lock(lock_path, timeout_sec=0.05):
                pass
