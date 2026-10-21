"""Read the commands that held over an observation interval.

Applications describe accepted target writes, not measured vehicle motion.
Missing coverage, non-autonomous execution and clock discontinuities leave the
interval unknown. Policy-specific interpretation stays with each learner.
"""
from __future__ import annotations

from autonomy.decision_cycle.action.publication import CONTROL_APPLICATIONS_KEY
from autonomy.decision_cycle.proposal.values import COMMAND_EPS
from autonomy.runtime.execution import ControlApplication
from autonomy.shared_memory import SharedMemory


def motions_over(memory: SharedMemory, start_ms: int, end_ms: int) -> tuple[str, ...] | None:
    """Motion kinds at the start and every target write through the end, inclusive."""
    applications = memory.get(CONTROL_APPLICATIONS_KEY)
    if not isinstance(applications, tuple) or not applications or end_ms < start_ms:
        return None
    previous = None
    active = None
    held = []
    for application in applications:
        if not isinstance(application, ControlApplication):
            return None
        at = application.delivered_at_ms
        if at is None or (previous is not None and at < previous):
            return None
        previous = at
        if at <= start_ms:
            active = application
        elif at <= end_ms:
            held.append(application)
    if active is None:
        return None
    motions = []
    for application in (active, *held):
        if not application.applied or application.mode != "autonomy":
            return None
        control = application.control
        if abs(control.throttle) < COMMAND_EPS:
            kind = "idle"
        else:
            kind = "forward" if control.throttle > 0 else "reverse"
            if abs(control.steering) >= COMMAND_EPS:
                kind += "_right" if control.steering > 0 else "_left"
        if not motions or motions[-1] != kind:
            motions.append(kind)
    return tuple(motions)


def motion_at(memory: SharedMemory, at_ms: int) -> str | None:
    motions = motions_over(memory, at_ms, at_ms)
    return None if motions is None else motions[-1]


def application_at(memory: SharedMemory, at_ms: int) -> ControlApplication | None:
    """The known autonomous target at this time, including its recorded reason."""
    if motions_over(memory, at_ms, at_ms) is None:
        return None
    return next(application for application in reversed(memory[CONTROL_APPLICATIONS_KEY])
                if application.delivered_at_ms <= at_ms)
