"""Shared orchestration types & tuning constants for the Short builder."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

# Average product score (0-10) at/above which a soft-only scene-QA FAIL is
# auto-passed as WARN instead of regenerated (spec §6).
SCORE_AUTOPASS_AVERAGE = 8.5

MAX_QA_RETRIES_PER_STAGE = 1
MAX_SCENE_REGEN_ATTEMPTS = 2
MAX_SCRIPT_REGEN_ATTEMPTS = 1


class StageSignal(Enum):
    PROCEED = "proceed"
    RESTART_SCRIPT = "restart"
    DONE = "done"


@dataclass
class StageResult:
    signal: StageSignal = StageSignal.PROCEED
    returns: dict[str, Any] | None = None


_PROCEED = StageResult(StageSignal.PROCEED)
