"""The decision cycle's one clock, and the timing record each cycle publishes.

Every duration the cycle reports is measured with ``clock`` and
``elapsed_ms``: the cycle's total and each step call's in ``DecisionCycle.run``,
and each plugin's where the step's runner invokes it. A step's runner keeps
its plugins' last durations, which its ``plugin_report`` shows.
``cycle_timing`` copies them into the cycle's record, so nothing is timed
twice.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from autonomy.decision_cycle.activation import STEPS


def clock() -> float:
    """A monotonic reading to pass to ``elapsed_ms`` later."""

    return time.perf_counter()


def elapsed_ms(started: float) -> float:
    """Milliseconds since ``started``, rounded to microseconds."""

    return round((time.perf_counter() - started) * 1000.0, 3)


def cycle_timing(
    duration_ms: float, step_durations: Mapping[str, float], steps: Any
) -> dict[str, Any]:
    """The cycle's total and each step's timing, in ``STEPS`` order.

    ``step_durations`` holds each step call's duration; an empty slot has
    none and reports null. ``steps`` is the cycle's ``DecisionSteps``; each
    entry's ``plugins`` is the slot's ``StepRunner.plugin_timing()``: each
    applied plugin's ``plugin_id``, ``duration_ms`` and ``error``, or none
    for a step callable that is not a runner. The steps are a list so the
    order survives JSON with sorted keys.
    """

    records = []
    for step in STEPS:
        plugin_timing = getattr(getattr(steps, step, None), "plugin_timing", None)
        records.append(
            {
                "step": step,
                "duration_ms": step_durations.get(step),
                "plugins": plugin_timing() if callable(plugin_timing) else [],
            }
        )
    return {"duration_ms": duration_ms, "steps": records}


def step_timing(timing: Any, step: str) -> dict[str, Any] | None:
    """``step``'s entry in a ``cycle_timing`` record, or None when it has none."""

    steps = timing.get("steps") if isinstance(timing, Mapping) else None
    return next(
        (entry for entry in steps or () if isinstance(entry, dict) and entry.get("step") == step),
        None,
    )
