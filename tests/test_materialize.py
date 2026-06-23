from pathlib import Path

from video_agent.assets.materialize import materialize_media


def test_materialize_clone_identical(tmp_path: Path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello world " * 1000)
    dst = tmp_path / "out" / "dst.bin"  # parent does not exist yet
    result = materialize_media(src, dst)
    assert result == dst
    assert dst.exists()
    assert dst.read_bytes() == src.read_bytes()


def test_materialize_cow_independent(tmp_path: Path):
    """Clone must NOT share an inode: mutating dst leaves src unchanged."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"A" * 2000)
    dst = tmp_path / "dst.bin"
    materialize_media(src, dst)
    dst.write_bytes(b"B" * 2000)  # in-place overwrite of the clone
    assert src.read_bytes() == b"A" * 2000


def test_materialize_overwrites_existing_dst(tmp_path: Path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"new-content")
    dst = tmp_path / "dst.bin"
    dst.write_bytes(b"stale")
    materialize_media(src, dst)
    assert dst.read_bytes() == b"new-content"


def test_materialize_fallback_on_clone_failure(tmp_path: Path, monkeypatch):
    """When the clone path raises, fall back to a real copy."""
    import video_agent.assets.materialize as m

    src = tmp_path / "src.bin"
    src.write_bytes(b"data" * 100)
    dst = tmp_path / "dst.bin"

    def boom(*args, **kwargs):
        raise RuntimeError("clone unavailable")

    monkeypatch.setattr(m.subprocess, "run", boom)
    materialize_media(src, dst)
    assert dst.read_bytes() == src.read_bytes()
