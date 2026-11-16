"""Retain applied control runs beyond the host's short application window.

The packaged ``memory/control_runs`` plugin owns ``memory.control_runs.state``:
``runs`` (last 40, oldest first), ``cursor`` (last consumed application's
frame_id, delivered_at_ms, reason and control), ``initialized``, ``gaps`` (lost
cursors), and ``window_known``. It rewrites ``memory.control_runs`` from that
state every cycle. Seed the state, never the publication. PolicyCycle,
executors and prompt builders read the publication with ``read_control_runs``;
None means it is absent (the plugin is not armed), while () means no runs.
An unknown application window leaves runs and cursor untouched and sets
``window_known=False`` in state/status; it does not assert that nothing applied.

Each run is ``{by, plan, command, start_ms, end_ms}``: proposer ``plugin_id``
(or None when unknown), ``plan_metadata`` (or {}), [throttle, steering], and
application ``delivered_at_ms`` endpoints. Only applied autonomy writes with
a known proposer open runs, including plugin-proposed [0, 0] placeholders.
Other applied writes close the open run. Unapplied writes change no output.
First observation skips the retained window, which can belong to an earlier
pipeline/run. Content cursors survive memory-file and replay round trips.

Delivery and frame times on current hosts are wall-clock milliseconds, which
can step backwards. Runs retain write order, and close at max(start_ms, time)
so durations cannot become negative. This is a record of known writes, not a
claim of continuous motion across missing windows; ``gaps`` reports lost history.
"""

from __future__ import annotations

# pyright: basic

from copy import deepcopy
from typing import TypedDict, cast

from autonomy.decision_cycle.action.publication import read_control_applications
from autonomy.runtime.execution import ControlApplication
from autonomy.serialization import ensure_strict_json_value
from autonomy.shared_memory import SharedMemory

CONTROL_RUNS_KEY = "memory.control_runs"
STATE_KEY = "memory.control_runs.state"
CONTROL_RUN_LIMIT = 40


class ControlRun(TypedDict):
    by: str
    plan: dict[str, object]
    command: list[float]
    start_ms: int
    end_ms: int | None


class ApplicationIdentity(TypedDict):
    frame_id: str | None
    delivered_at_ms: int | None
    reason: str
    control: object


class ControlRunsState(TypedDict):
    runs: list[ControlRun]
    cursor: ApplicationIdentity | None
    initialized: bool
    gaps: int
    window_known: bool


def read_control_runs(shared_memory: SharedMemory | None) -> tuple[ControlRun, ...] | None:
    """Detached runs, oldest first; None when memory/control_runs is not armed."""
    runs = None if shared_memory is None else shared_memory.get(CONTROL_RUNS_KEY)
    return None if runs is None else tuple(deepcopy(cast(list[ControlRun], runs)))


def control_runs_status(shared_memory: SharedMemory | None) -> ControlRunsState:
    """Detached producer state, including whether the host window is known."""
    state = None if shared_memory is None else shared_memory.get(STATE_KEY)
    return deepcopy(cast(ControlRunsState, state)) if state is not None else {
        "runs": [], "cursor": None, "initialized": False, "gaps": 0, "window_known": False,
    }


def _identity(application: ControlApplication) -> ApplicationIdentity:
    return {
        "frame_id": application.frame_id,
        "delivered_at_ms": application.delivered_at_ms,
        "reason": application.reason,
        "control": ensure_strict_json_value(application.control.to_dict()),
    }


def _consume(runs: list[ControlRun], application: ControlApplication) -> None:
    if not application.applied:
        return
    at = application.delivered_at_ms
    if at is None:
        raise ValueError("an applied control requires delivered_at_ms")
    metadata = application.control.metadata
    by = metadata.get("plugin_id")
    entry: ControlRun | None = None
    if application.mode == "autonomy" and isinstance(by, str):
        entry = {
            "by": by, "plan": deepcopy(cast(dict[str, object], metadata.get("plan_metadata") or {})),
            "command": [application.control.throttle, application.control.steering],
            "start_ms": at, "end_ms": None,
        }
    previous = runs[-1] if runs else None
    if previous is not None and previous["end_ms"] is None:
        if entry is not None and all(previous[key] == entry[key] for key in ("by", "plan", "command")):
            return
        previous["end_ms"] = max(previous["start_ms"], at)
    if entry is not None:
        runs.append(entry)


def record_control_runs(shared_memory: SharedMemory) -> None:
    """Consume new host writes once and rebuild the publication from owned state."""
    state = control_runs_status(shared_memory)
    window = read_control_applications(shared_memory)
    state["window_known"] = window is not None
    if window is not None:
        identities = [_identity(application) for application in window]
        if state["initialized"]:
            cursor = state["cursor"]
            after = next((index + 1 for index in range(len(window) - 1, -1, -1)
                          if identities[index] == cursor), None)
            if cursor is not None and after is None:
                state["gaps"] += 1
            for application in window[after or 0:]:
                _consume(state["runs"], application)
        state["initialized"] = True
        if identities:
            state["cursor"] = identities[-1]
        state["runs"] = state["runs"][-CONTROL_RUN_LIMIT:]
    shared_memory[STATE_KEY] = state
    shared_memory[CONTROL_RUNS_KEY] = deepcopy(state["runs"])
