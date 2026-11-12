"""Chase's steering sign, converted where the adapter sends a command.

The engine's steering is -1 for full left and +1 for full right (``VehicleAction``).
Chase steers left on +1: its keyboard sends +1 for the left key. ``ChaseSimCar``
flips the sign of the steering it sends. Records read back from Chase for
evaluation and session checks (``chaser_input``, ``chaser_action``, ``control_input``)
keep Chase's own sign.
"""

from __future__ import annotations


def chase_steering(steering: float) -> float:
    """Engine steering as Chase's steering input."""

    return 0.0 - float(steering)
