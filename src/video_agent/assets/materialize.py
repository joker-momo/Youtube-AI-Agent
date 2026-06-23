from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def materialize_media(src: str | Path, dst: str | Path) -> Path:
    """Materialize ``src`` at ``dst`` using an APFS copy-on-write clone when
    possible, falling back to a real byte copy.

    Never shares an inode (no hardlink), so a later in-place mutation of
    ``dst`` can never bleed into ``src``. Drop-in semantic replacement for
    ``shutil.copy2(src, dst)``: on return ``dst`` exists and is byte-identical
    to ``src``. Returns ``dst`` as a ``Path``.
    """
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        try:
            if dst.exists():
                dst.unlink()  # clonefile fails on an existing destination
            subprocess.run(
                ["cp", "-c", str(src), str(dst)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return dst
        except Exception:
            pass  # cross-fs / non-APFS / clone unsupported -> real copy
    shutil.copy2(src, dst)
    return dst
