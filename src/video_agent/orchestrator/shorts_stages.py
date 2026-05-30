"""DEPRECATED legacy Shorts stages.

The old ``auto_shorts_*`` stages wrote bare ``jobs/<id>/shorts/<N>/`` folders
without manifest/source-map/status structure. They are replaced by the
sequential Shorts Autopilot in ``video_agent.shorts`` (run via
``POST /jobs/{job_id}/shorts/autopilot`` or automatically after long Review
PASS). These stubs remain only so any old job still listing these stages fails
loudly instead of silently writing into the directory the new system owns.
"""
from __future__ import annotations

from pathlib import Path

_DEPRECATION_MSG = (
    "Legacy auto_shorts_* stages are deprecated. Use the Shorts Autopilot: "
    "POST /jobs/{job_id}/shorts/autopilot or video_agent.shorts.autopilot.run_shorts_autopilot()."
)


class LegacyShortsDeprecatedError(RuntimeError):
    pass


async def auto_shorts_script_stage(job_dir: Path, channel_path: Path, session_fn) -> Path:
    raise LegacyShortsDeprecatedError(_DEPRECATION_MSG)


async def auto_shorts_scenes_stage(job_dir: Path, channel_path: Path, session_fn) -> Path:
    raise LegacyShortsDeprecatedError(_DEPRECATION_MSG)


async def auto_shorts_tts_stage(job_dir: Path, channel_path: Path, session_fn) -> Path:
    raise LegacyShortsDeprecatedError(_DEPRECATION_MSG)


async def auto_shorts_render_stage(job_dir: Path, channel_path: Path, session_fn) -> Path:
    raise LegacyShortsDeprecatedError(_DEPRECATION_MSG)
