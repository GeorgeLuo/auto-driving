"""Read memory plugin state from a memory report or memory step status.

Both carry ``plugins``: one entry per applied plugin with its ``state``, the
plugin's own ``status()`` summary. Diagnostics that follow one retained-evidence
ledger read ``LEDGER_SUMMARY_KEYS`` from ``interface`` off the last applied
plugin's state. That plugin need not publish evidence: the framework does not
require every memory plugin to write ``EVIDENCE_KEY``. This projection neither
merges summaries nor identifies the publisher of the evidence decisions read.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.memory.interface import (
    EPOCH_ID,
    HEALTH,
    LEDGER_SUMMARY_KEYS,
    RECORD_COUNT,
)


def last_plugin_state(report: object) -> dict[str, Any] | None:
    """State of the last applied memory plugin, or None when there is none."""

    if not isinstance(report, dict):
        return None
    plugins = report.get("plugins")
    if not isinstance(plugins, list) or not plugins or not isinstance(plugins[-1], dict):
        return None
    state = plugins[-1].get("state")
    return state if isinstance(state, dict) else None


def ledger_summary(state: dict[str, Any] | None) -> dict[str, Any]:
    """The four ledger summary fields. A missing field is None."""

    state = state or {}
    return {key: state.get(key) for key in LEDGER_SUMMARY_KEYS}


def memory_summary(state: dict[str, Any] | None) -> dict[str, Any]:
    """Health, record count and epoch of one plugin state, as each frame reports them.

    Inspect frames report these three. ``record_count`` is 0 when the state
    omits it, and stays None when the state sets it to None. ``bounds`` stays
    on ``ledger_summary``; a frame row does not repeat the ledger's capacity.
    """

    state = state or {}
    summary = ledger_summary(state)
    record_count = summary[RECORD_COUNT]
    if RECORD_COUNT not in state:
        record_count = 0
    return {
        HEALTH: summary[HEALTH],
        RECORD_COUNT: record_count,
        EPOCH_ID: summary[EPOCH_ID],
    }
