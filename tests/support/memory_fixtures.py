"""Memory reports as the memory step records them, for CLI fixtures."""

from __future__ import annotations

from typing import Any


def memory_report(state: dict[str, Any], *, plugin_id: str = "bounded_evidence") -> dict[str, Any]:
    """A one-plugin memory report whose plugin state is ``state``."""

    return {
        "schema": "memory_report_v0",
        "plugins": [{"plugin_id": plugin_id, "state": state}],
    }
