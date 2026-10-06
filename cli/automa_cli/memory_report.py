"""Read memory plugin state from a memory report or memory step status.

Both carry ``plugins``: one entry per applied plugin, in selection order, with
its ``plugin_id`` and ``state``, the plugin's own ``status()`` summary. Both
also carry ``evidence_publisher``: the plugin whose value ``EVIDENCE_KEY``
holds, or None. Readers show every applied plugin and find one by
``plugin_id``; none stands one plugin in for the step by its place in the list.

Diagnostics read the ``LEDGER_SUMMARY_KEYS`` from ``interface`` off each
plugin's state. The framework does not require a ledger, so a plugin that
keeps none reports None for those fields. This projection does not merge
summaries across plugins.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.memory.interface import (
    EPOCH_ID,
    HEALTH,
    LEDGER_HEALTH_EMPTY,
    LEDGER_SUMMARY_KEYS,
    RECORD_COUNT,
)


def plugin_states(report: object) -> list[tuple[str, dict[str, Any] | None]]:
    """``(plugin_id, state)`` of each applied plugin, in selection order.

    A state that is not a dict reads as None. An entry without a string
    ``plugin_id`` is skipped.
    """

    if not isinstance(report, dict):
        return []
    plugins = report.get("plugins")
    if not isinstance(plugins, list):
        return []
    states: list[tuple[str, dict[str, Any] | None]] = []
    for entry in plugins:
        if not isinstance(entry, dict) or not isinstance(entry.get("plugin_id"), str):
            continue
        state = entry.get("state")
        states.append((entry["plugin_id"], state if isinstance(state, dict) else None))
    return states


def evidence_publisher(report: object) -> str | None:
    """The plugin whose value ``EVIDENCE_KEY`` holds, or None."""

    publisher = report.get("evidence_publisher") if isinstance(report, dict) else None
    return publisher if isinstance(publisher, str) and publisher else None


def ledger_summary(state: dict[str, Any] | None) -> dict[str, Any]:
    """The four ledger summary fields. A missing field is None."""

    state = state or {}
    return {key: state.get(key) for key in LEDGER_SUMMARY_KEYS}


def plugin_ledgers(report: object) -> list[dict[str, Any]]:
    """Each applied plugin's ``plugin_id`` with its four ledger summary fields."""

    return [
        {"plugin_id": plugin_id, **ledger_summary(state)}
        for plugin_id, state in plugin_states(report)
    ]


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


def plugin_summaries(report: object) -> list[dict[str, Any]]:
    """Each applied plugin's ``plugin_id`` with its health, record count and epoch.

    These are the rows inspect lists per frame and the workbench lists per
    frame.
    """

    return [
        {"plugin_id": plugin_id, **memory_summary(state)}
        for plugin_id, state in plugin_states(report)
    ]


def ledger_is_empty(summary: dict[str, Any]) -> bool:
    """Whether one plugin's ledger summary shows nothing retained.

    The ledger is empty when it reports no records (0 or no count), or
    ``empty`` or ``unavailable`` health. A plugin that keeps no ledger
    reports no count, so it counts as empty.
    """

    return summary.get(RECORD_COUNT) in {0, None} or summary.get(HEALTH) in {
        LEDGER_HEALTH_EMPTY,
        "unavailable",
    }
