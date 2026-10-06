"""Host one decision cycle on a vehicle loop.

``AutonomyCycleHost`` owns the host map every step's plugins share, runs the
cycle once per frame, and keeps the last result. ``set_step`` swaps one step's
runner between frames (for example after its activation changes), and
``status`` reports each step runner's status.
"""

from __future__ import annotations

import threading
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from autonomy.decision_cycle.activation import STEPS, require_step
from autonomy.decision_cycle.cycle import (
    DecisionCycle,
    DecisionCycleResult,
    DecisionFrameContext,
    DecisionSteps,
)
from autonomy.decision_cycle.steps import decision_steps, load_decision_steps
from autonomy.runtime.control import AutonomyControl
from autonomy.shared_memory import SharedMemory

IDLE_REASON = "cycle-idle"


class AutonomyCycleHost:
    """Run the decision cycle around the host map."""

    def __init__(self, *, steps: DecisionSteps | None = None) -> None:
        self._lock = threading.RLock()
        self.cycle = DecisionCycle(steps or decision_steps(), idle_reason=IDLE_REASON)
        self.shared_memory: SharedMemory = {}
        self.last_result: DecisionCycleResult | None = None
        self.cycle_count = 0
        self.error_count = 0
        self.last_error: str | None = None
        self._status_providers: dict[str, Callable[[], dict[str, Any]]] = {}

    @classmethod
    def from_runtime(cls, runtime_root: Path) -> "AutonomyCycleHost":
        """Load every step from ``runtime_root/<step>/active.json``."""

        return cls(steps=load_decision_steps(runtime_root))

    @property
    def steps(self) -> DecisionSteps:
        return self.cycle.steps

    def step(self, step: str) -> Any:
        return getattr(self.cycle.steps, require_step(step))

    def set_step(self, step: str, runner: Any) -> None:
        """Replace one step's runner; the next frame uses it."""

        with self._lock:
            self.cycle.steps = replace(self.cycle.steps, **{require_step(step): runner})

    def run(self, context: DecisionFrameContext) -> DecisionCycleResult:
        with self._lock:
            if context.shared_memory is None:
                context = replace(context, shared_memory=self.shared_memory)
            else:
                self.shared_memory = context.shared_memory
            try:
                result = self.cycle.run(context)
            except Exception as exc:
                self.error_count += 1
                self.last_error = "".join(
                    traceback.format_exception_only(type(exc), exc)
                ).strip()
                raise
            self.cycle_count += 1
            self.last_error = None
            self.last_result = result
            return result

    def register_status_provider(
        self, component_id: str, provider: Callable[[], dict[str, Any]]
    ) -> None:
        """Report a host component (not a step) under ``status()["components"]``."""

        if not component_id or not callable(provider):
            raise ValueError("runtime status providers require an id and callable")
        with self._lock:
            self._status_providers[component_id] = provider

    @property
    def last_control(self) -> AutonomyControl | None:
        result = self.last_result
        return result.control if result is not None else None

    def status(self) -> dict[str, Any]:
        with self._lock:
            steps: dict[str, Any] = {}
            for step in STEPS:
                runner = getattr(self.cycle.steps, step)
                report = getattr(runner, "status", None)
                if runner is None:
                    steps[step] = None
                elif callable(report):
                    try:
                        steps[step] = report()
                    except Exception as exc:  # noqa: BLE001 - status must not fail the host
                        steps[step] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
                else:
                    steps[step] = {"runner": type(runner).__name__}
            components: dict[str, Any] = {}
            for component_id, provider in self._status_providers.items():
                try:
                    components[component_id] = provider()
                except Exception as exc:  # noqa: BLE001 - status must not fail the host
                    components[component_id] = {
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
            return {
                "steps": steps,
                "components": components,
                "cycle_count": self.cycle_count,
                "error_count": self.error_count,
                "last_error": self.last_error,
                "last_control": (
                    self.last_control.to_dict() if self.last_control is not None else None
                ),
                "last_cycle": self.last_result.to_dict() if self.last_result is not None else None,
            }

    def reset_memory(self) -> dict[str, Any] | None:
        """Reset the memory step when present.

        Clears the host map and keeps only the fresh state memory plugins
        wrote while resetting (such as a new epoch). Returns the memory report
        after the reset, or ``None`` when no memory step is configured.
        """

        with self._lock:
            memory = self.cycle.steps.memory
            if memory is None:
                self.shared_memory.clear()
                return None
            if not callable(getattr(memory, "reset", None)) or not callable(
                getattr(memory, "report", None)
            ):
                raise TypeError("configured memory step does not support reset")
            fresh = memory.reset(self.shared_memory)
            self.shared_memory.clear()
            self.shared_memory.update(fresh)
            return memory.report()
