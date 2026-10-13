"""The decision cycle's one clock, and the timing record each cycle publishes.

Every duration the cycle reports is measured with ``clock`` and
``elapsed_ms``: the cycle's total in ``DecisionCycle.run``, each step's in
``StepRunner._timed_step``, and each plugin's where the step's runner invokes
it. A step's runner keeps its own and its plugins' last durations, which its
``status`` and ``plugin_report`` show. ``cycle_timing`` copies them into the
cycle's record, so nothing is timed twice.
"""

from __future__ import annotations

import time
from typing import Any

from autonomy.decision_cycle.activation import STEPS


def clock() -> float:
    """A monotonic reading to pass to ``elapsed_ms`` later."""

    return time.perf_counter()


def elapsed_ms(started: float) -> float:
    """Milliseconds since ``started``, rounded to microseconds."""

    return round((time.perf_counter() - started) * 1000.0, 3)


def cycle_timing(duration_ms: float, steps: Any) -> dict[str, Any]:
    """The cycle's total and each step's timing, in ``STEPS`` order.

    ``steps`` is the cycle's ``DecisionSteps``. Each entry is the step's
    ``StepRunner.timing()``: its duration and each applied plugin's
    ``plugin_id``, ``duration_ms`` and ``error``. An empty slot, or a step
    callable that is not a runner, has a null duration and no plugins. The
    steps are a list so the order survives JSON with sorted keys.
    """

    records = []
    for step in STEPS:
        runner = getattr(steps, step, None)
        timing = getattr(runner, "timing", None)
        record = timing() if callable(timing) else {"duration_ms": None, "plugins": []}
        records.append({"step": step, **record})
    return {"duration_ms": duration_ms, "steps": records}
