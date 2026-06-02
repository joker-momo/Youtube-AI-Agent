"""Regression: transient retryable failures (e.g. 502 from browser-worker) must
NOT trigger notify_job_failed inside execute_run_all. The worker decides
final-vs-retry; only the worker emits the user-visible "Job failed" alert
after retries are exhausted."""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

from video_agent.orchestrator.browser_client import BrowserClientError
from video_agent.web import run_all_pipeline


def test_execute_run_all_does_not_call_notify_job_failed_on_browser_client_error():
    """Source inspection: BrowserClientError branch must not call
    notify_job_failed. The worker queue layer handles user notification only
    after max_attempts is reached."""
    src = inspect.getsource(run_all_pipeline)
    # The OUTER BrowserClientError handler in execute_run_all is the one
    # that calls _browser_http_exception(exc). Inner handlers (in helpers
    # like _open_with_retry / _send_with_retry) only re-raise after their
    # own retry budget. Locate the OUTER handler specifically.
    marker = "http_exc = _browser_http_exception(exc)"
    outer_idx = src.index(marker)
    raise_after = src.index(
        "raise HTTPException(status_code=http_exc.status_code", outer_idx
    )
    span = src[outer_idx:raise_after]
    assert "notify_job_failed" not in span, (
        "BrowserClientError handler must not notify the user — that's the "
        "worker's job after retries are exhausted, otherwise transient 502s "
        "leak a stale ❌ Job failed alert that the dashboard never clears."
    )


def test_worker_mark_failed_sends_notify_job_failed():
    """Conversely: when the worker decides the job is finally failed (not
    retryable, or retries exhausted), it must emit notify_job_failed itself."""
    from video_agent.orchestrator import worker
    src = inspect.getsource(worker)
    # The except branch in run_worker_loop reaches mark_failed when retries
    # are exhausted. notify_job_failed must be called near that branch.
    assert "notify_job_failed" in src, (
        "Worker queue loop must own the final-failure notification."
    )


def test_worker_does_not_retry_browser_quota_exhaustion():
    from fastapi import HTTPException
    from video_agent.orchestrator import worker

    exc = HTTPException(
        status_code=429,
        detail={
            "error": "Gemini quota exhausted",
            "browser_worker_detail": {"quota_exhausted": True},
        },
    )

    assert worker._is_retryable_exception(exc) is False
