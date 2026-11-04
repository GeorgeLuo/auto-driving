"""Browser payloads for the workbench's live state and replay history.

Terminal text for a CLI replay is ``render``. This module turns a completed
cycle into the dictionaries the workbench page reads.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.interface import PerceptionText

from ..shared.memory_report import evidence_publisher, plugin_summaries


def frame_summary(
    *,
    perception: PerceptionText | None = None,
    observation: Observation | None = None,
    memory: Any = None,
    duration_ms: int | float | None = None,
    frames_completed: int,
    frames_total: int,
) -> dict[str, Any]:
    return {
        "frames_completed": int(frames_completed),
        "frames_total": int(frames_total),
        "perception_status": perception.status if perception else None,
        "perception_things": len(perception.things) if perception else 0,
        "perception_signals": len(perception.signals) if perception else 0,
        "observation_available": observation is not None,
        "memory_plugins": plugin_summaries(memory),
        "memory_evidence_publisher": evidence_publisher(memory),
        "last_duration_ms": round(float(duration_ms), 3)
        if duration_ms is not None
        else None,
    }
