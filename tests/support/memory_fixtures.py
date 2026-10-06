"""Memory reports as the memory step records them, for CLI fixtures."""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.interface import (
    MEMORY_REPORT_SCHEMA,
    MemoryPluginReport,
    MemoryReport,
)
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.observation.values import Observation
from implementations.decision_cycle.catalog import step_plugins
from implementations.decision_cycle.memory.plugins.bounded_evidence.plugin import (
    BoundedEvidenceLedger,
)
from tests.autonomy.decision_cycle.memory.activation_fixtures import _RecordingMemory

# Selection order of ``two_plugin_runner``: the publisher first, so the last
# plugin in the list is not the one that published ``EVIDENCE_KEY``.
TWO_PLUGIN_IDS = ("bounded_evidence", "recording_test")


def memory_report(
    state: dict[str, Any],
    *,
    plugin_id: str = "bounded_evidence",
    publishes: bool = True,
) -> dict[str, Any]:
    """A one-plugin memory report whose plugin state is ``state``.

    The plugin is named the evidence publisher unless ``publishes`` is False.
    """

    return plugins_report(
        {plugin_id: state}, evidence_publisher=plugin_id if publishes else None
    )


def plugins_report(
    states: dict[str, dict[str, Any]], *, evidence_publisher: str | None
) -> dict[str, Any]:
    """A memory report with one entry per ``plugin_id: state``, in that order."""

    return MemoryReport(
        schema=MEMORY_REPORT_SCHEMA,
        plugins=tuple(
            MemoryPluginReport(plugin_id=plugin_id, state=state)
            for plugin_id, state in states.items()
        ),
        evidence_publisher=evidence_publisher,
    ).to_dict()


def two_plugin_runner(*, frames: int = 1) -> tuple[MemoryRunner, dict[str, Any]]:
    """``bounded_evidence`` then the recording test plugin, after ``frames`` updates.

    Returns the runner and its shared-memory map. Each frame observes one
    floor boundary, so both plugins hold one record and ``bounded_evidence``
    publishes ``EVIDENCE_KEY``.
    """

    config = step_plugins("memory")["bounded_evidence"]["default_config"]
    runner = MemoryRunner.from_plugins(
        {
            "bounded_evidence": BoundedEvidenceLedger(**config),
            "recording_test": _RecordingMemory(),
        }
    )
    shared: dict[str, Any] = {}
    for index in range(frames):
        frame_id = f"frame-{index}"
        timestamp_ms = 1_000 + index * 100
        runner.update(
            DecisionFrameContext(frame_id, index, timestamp_ms, shared_memory=shared),
            Observation(
                observation_id=frame_id,
                created_at_ms=timestamp_ms,
                sensor_frame={},
                things=(
                    {
                        "thing_id": "boundary",
                        "kind": "floor_boundary",
                        "label": "boundary",
                        "confidence": 0.9,
                        "location": {"frame": "image", "zone": "center"},
                    },
                ),
            ),
        )
    return runner, shared
