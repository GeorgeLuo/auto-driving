"""What the host publishes from the action step for later cycles.

Before each cycle the host writes the drive mode and user controls under
``MODE_KEY``, ``USER_STEERING_KEY`` and ``USER_THROTTLE_KEY``. Missing values
mean unknown. A host with control execution also writes its recent
``ControlApplication`` records at ``CONTROL_APPLICATIONS_KEY``: a tuple or list,
oldest first, of at most ``CONTROL_APPLICATION_LIMIT`` target writes, each with
the mode it ran in. ``()`` means no writes are retained. History survives run
and memory resets. The key is absent when the host has no execution or a replay
source recorded none, so readers treat the car's motion as unknown.

``read_control_applications`` reads the window; ``control_applications_over``
selects a covered interval of applied autonomy writes. Manual and observe-only
applications do not describe the operator's motion. What ``delivered_at_ms``
marks on each vehicle, and why it can step backwards, is described in
``autonomy/runtime/execution.py``. Motion classification belongs to plugins.
"""
from __future__ import annotations

from autonomy.runtime.execution import ControlApplication
from autonomy.shared_memory import SharedMemory

MODE_KEY = "action.mode"
USER_STEERING_KEY = "action.user_steering"
USER_THROTTLE_KEY = "action.user_throttle"


def read_mode(shared_memory: SharedMemory | None) -> str | None:
    """The host's drive mode, or None when it is unknown."""
    value = None if shared_memory is None else shared_memory.get(MODE_KEY)
    return value if isinstance(value, str) else None


CONTROL_APPLICATIONS_KEY = "action.control_applications"


def read_control_applications(shared_memory: SharedMemory | None) -> tuple[ControlApplication, ...] | None:
    """The published window, or None when the key holds no valid window."""
    value = None if shared_memory is None else shared_memory.get(CONTROL_APPLICATIONS_KEY)
    if not isinstance(value, (tuple, list)) or any(not isinstance(item, ControlApplication) for item in value):
        return None
    return tuple(value)


def control_applications_over(
    shared_memory: SharedMemory | None, start_ms: int, end_ms: int,
) -> tuple[ControlApplication, ...] | None:
    """The write in force at start and subsequent writes through the inclusive end.

    Unknown coverage, backwards clocks, and non-autonomous or unapplied writes
    in the selected interval return None. Equal timestamps retain write order.
    """
    applications = read_control_applications(shared_memory)
    if not applications or end_ms < start_ms:
        return None
    times = [item.delivered_at_ms for item in applications]
    if any(at is None for at in times) or any(left > right for left, right in zip(times, times[1:])):
        return None
    start = next((index for index in range(len(times) - 1, -1, -1) if times[index] <= start_ms), None)
    if start is None:
        return None
    selected = tuple(item for item in applications[start:] if item.delivered_at_ms <= end_ms)
    if any(not item.applied or item.mode != "autonomy" for item in selected):
        return None
    return selected
