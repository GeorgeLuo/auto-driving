"""Read the commands that held over an observation interval.

Applications describe accepted target writes, not measured vehicle motion.
Missing coverage, non-autonomous execution and clock discontinuities leave the
interval unknown. Policy-specific interpretation stays with each learner.
"""
from __future__ import annotations

from autonomy.decision_cycle.action.publication import control_applications_over, read_control_applications
from autonomy.decision_cycle.proposal.values import COMMAND_EPS
from autonomy.runtime.execution import ControlApplication
from autonomy.shared_memory import SharedMemory


def motions_over(memory: SharedMemory, start_ms: int, end_ms: int) -> tuple[str, ...] | None:
    """Motion kinds at the start and every target write through the end, inclusive."""
    applications = control_applications_over(memory, start_ms, end_ms)
    if applications is None:
        return None
    motions = []
    for application in applications:
        kind = _motion(application)
        if not motions or motions[-1] != kind:
            motions.append(kind)
    return tuple(motions)


def motion_at(memory: SharedMemory, at_ms: int) -> str | None:
    motions = motions_over(memory, at_ms, at_ms)
    return None if motions is None else motions[-1]


def application_at(memory: SharedMemory, at_ms: int) -> ControlApplication | None:
    """The known autonomous target at this time, including its recorded reason."""
    applications = control_applications_over(memory, at_ms, at_ms)
    return None if applications is None else applications[-1]


def latest_motion(memory: SharedMemory) -> str | None:
    """The last issued target, including writes delivered after this frame's capture."""
    applications = read_control_applications(memory)
    if not applications:
        return None
    at = applications[-1].delivered_at_ms
    return None if at is None else motion_at(memory, at)


def _motion(application: ControlApplication) -> str:
    control = application.control
    if abs(control.throttle) < COMMAND_EPS:
        return "idle"
    kind = "forward" if control.throttle > 0 else "reverse"
    if abs(control.steering) >= COMMAND_EPS:
        kind += "_right" if control.steering > 0 else "_left"
    return kind
