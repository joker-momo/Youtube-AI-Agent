from __future__ import annotations

import subprocess
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parents[1]
HELPER = REPO_DIR / "installers" / "lib" / "run_state.sh"


def run_bash(script: str, cwd: Path) -> str:
    result = subprocess.run(
        ["bash", "-lc", script],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def test_compute_path_fingerprint_changes_with_file_content(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\n")

    script = f"""
source "{HELPER}"
compute_path_fingerprint "{file_path}"
"""
    first = run_bash(script, REPO_DIR)

    file_path.write_text("beta\n")
    second = run_bash(script, REPO_DIR)

    assert first != second


def test_hash_persistence_detects_change(tmp_path: Path) -> None:
    hash_file = tmp_path / "runtime.hash"
    script = f"""
source "{HELPER}"
write_smart_auto_hash "hash-a" "{hash_file}"
if smart_auto_hash_changed "hash-a" "{hash_file}"; then
  echo changed
else
  echo same
fi
if smart_auto_hash_changed "hash-b" "{hash_file}"; then
  echo changed
else
  echo same
fi
"""

    output = run_bash(script, REPO_DIR).splitlines()

    assert output == ["same", "changed"]


def test_service_targets_and_native_restart_rules() -> None:
    script = f"""
source "{HELPER}"
printf '%s\\n' "$(smart_auto_service_targets dashboard)"
printf '%s\\n' "$(smart_auto_service_targets native)"
printf '%s\\n' "$(smart_auto_service_targets docker)"
if smart_auto_should_restart_native_worker false true; then
  echo restart
else
  echo keep
fi
if smart_auto_should_restart_native_worker true true; then
  echo restart
else
  echo keep
fi
if smart_auto_should_restart_native_worker false false; then
  echo restart
else
  echo keep
fi
"""

    output = run_bash(script, REPO_DIR).splitlines()

    assert output == [
        "app",
        "app browser-worker browser-runtime",
        "app worker browser-worker browser-runtime",
        "keep",
        "restart",
        "restart",
    ]
