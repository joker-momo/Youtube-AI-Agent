"""Verify BROWSER_HUMAN_MODE knob trims human-cadence overhead correctly."""

from __future__ import annotations

import importlib
import os

import pytest


def _reload_humanize(monkeypatch, **env):
    """Reload the humanize module with a custom environment so module-level
    constants pick up the new ``BROWSER_HUMAN_MODE`` value."""
    for k in (
        "BROWSER_HUMAN_MODE",
        "BROWSER_HUMAN_PAUSE_MIN_MS",
        "BROWSER_HUMAN_PAUSE_MAX_MS",
        "BROWSER_HUMAN_TYPING_MIN_MS",
        "BROWSER_HUMAN_TYPING_MAX_MS",
        "BROWSER_HUMAN_PASTE_THRESHOLD",
        "BROWSER_HUMAN_PASTE_PAUSE_MIN_MS",
        "BROWSER_HUMAN_PASTE_PAUSE_MAX_MS",
        "BROWSER_HUMAN_THINK_PROB",
        "BROWSER_HUMAN_POST_READ_PAUSE",
        "BROWSER_HUMAN_STABLE_MS",
        "BROWSER_HUMAN_STABLE_POLL_MS",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import video_agent.browser_worker.drivers.humanize as humanize
    return importlib.reload(humanize)


def test_fast_mode_is_default(monkeypatch):
    humanize = _reload_humanize(monkeypatch)
    assert humanize.HUMAN_MODE == "fast"
    assert humanize._FAST_MODE is True
    # Tightened windows
    assert humanize.PAUSE_MIN_MS == 100
    assert humanize.PAUSE_MAX_MS == 400
    assert humanize.TYPING_MIN_MS == 18
    assert humanize.TYPING_MAX_MS == 55
    assert humanize.PASTE_THRESHOLD_CHARS == 100
    assert humanize.STABLE_MS == 600
    assert humanize.STABLE_POLL_MS == 250
    # Post-read pause disabled.
    assert humanize.POST_READ_PAUSE_ENABLED is False


def test_human_mode_restores_slower_cadence(monkeypatch):
    humanize = _reload_humanize(monkeypatch, BROWSER_HUMAN_MODE="human")
    assert humanize.HUMAN_MODE == "human"
    assert humanize._FAST_MODE is False
    # Original slower windows
    assert humanize.PAUSE_MIN_MS == 400
    assert humanize.PAUSE_MAX_MS == 1400
    assert humanize.TYPING_MIN_MS == 35
    assert humanize.TYPING_MAX_MS == 110
    assert humanize.PASTE_THRESHOLD_CHARS == 200
    assert humanize.STABLE_MS == 2000
    assert humanize.STABLE_POLL_MS == 500
    assert humanize.POST_READ_PAUSE_ENABLED is True


def test_estimate_read_pause_zero_in_fast_mode(monkeypatch):
    humanize = _reload_humanize(monkeypatch)
    # Fast mode always returns 0 regardless of text length.
    assert humanize.estimate_read_pause_ms("short") == 0
    long_text = " ".join(["palabra"] * 500)
    assert humanize.estimate_read_pause_ms(long_text) == 0


def test_estimate_read_pause_nonzero_in_human_mode(monkeypatch):
    humanize = _reload_humanize(monkeypatch, BROWSER_HUMAN_MODE="human")
    # Human mode keeps the 0.8-4 s reading window.
    pause = humanize.estimate_read_pause_ms("palabra " * 30)
    assert 800 <= pause <= 4000


def test_env_overrides_take_precedence(monkeypatch):
    humanize = _reload_humanize(
        monkeypatch,
        BROWSER_HUMAN_MODE="fast",
        BROWSER_HUMAN_STABLE_MS="1234",
        BROWSER_HUMAN_PAUSE_MIN_MS="50",
    )
    assert humanize.STABLE_MS == 1234
    assert humanize.PAUSE_MIN_MS == 50


@pytest.fixture(autouse=True)
def restore_humanize_module():
    """Reload the module with the real environment after the test so
    other tests see the production defaults."""
    yield
    import video_agent.browser_worker.drivers.humanize as humanize
    importlib.reload(humanize)
