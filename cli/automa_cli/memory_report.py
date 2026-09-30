"""Read memory plugin state from a memory report or memory step status.

Both carry ``plugins``: one entry per applied plugin with its ``state``, the
plugin's own ``status()`` summary. Diagnostics that follow one retained-evidence
ledger (epoch, records, health) read the last plugin's state: plugins run in
order, so the last one publishes the evidence decisions read.
"""

from __future__ import annotations

from typing import Any


def memory_state(report: object) -> dict[str, Any] | None:
    """State of the last applied memory plugin, or None when there is none."""

    if not isinstance(report, dict):
        return None
    plugins = report.get("plugins")
    if not isinstance(plugins, list) or not plugins or not isinstance(plugins[-1], dict):
        return None
    state = plugins[-1].get("state")
    return state if isinstance(state, dict) else None
