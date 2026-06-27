"""Enforces the Shorts<->Long asset-layer boundary (spec 2026-06-27, §6).

A future change that re-couples the two pipelines turns the suite red. The
`stages/assets ↛ shorts` assertion is added in P4 once the long file is stripped.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "video_agent"


def _imports(py: Path) -> set[str]:
    tree = ast.parse(py.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_shorts_assets_never_imports_stages():
    pkg = SRC / "shorts" / "assets"
    offenders = {
        str(p.relative_to(SRC)): sorted(m for m in _imports(p) if m.startswith("video_agent.stages"))
        for p in pkg.rglob("*.py")
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, f"shorts/assets must not import video_agent.stages.*: {offenders}"


def test_other_shorts_never_imports_stages_assets():
    offenders = []
    for p in (SRC / "shorts").rglob("*.py"):
        if "video_agent.stages.assets" in _imports(p):
            offenders.append(str(p.relative_to(SRC)))
    assert not offenders, f"shorts must not import stages.assets: {offenders}"


def test_core_primitives_are_leaves():
    for name in ("stock_core.py", "media_ops.py", "audio_ops.py", "scene_prep.py"):
        mods = _imports(SRC / "assets" / name)
        bad = sorted(m for m in mods if m.startswith(("video_agent.stages", "video_agent.shorts")))
        assert not bad, f"assets/{name} must be a leaf primitive: {bad}"
