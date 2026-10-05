"""Memory reports as the memory step records them, for CLI fixtures."""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.memory.interface import (
    MEMORY_REPORT_SCHEMA,
    MemoryPluginReport,
    MemoryReport,
)


def memory_report(state: dict[str, Any], *, plugin_id: str = "bounded_evidence") -> dict[str, Any]:
    """A one-plugin memory report whose plugin state is ``state``."""

    return MemoryReport(
        schema=MEMORY_REPORT_SCHEMA,
        plugins=(MemoryPluginReport(plugin_id=plugin_id, state=state),),
    ).to_dict()
