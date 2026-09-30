from __future__ import annotations

from dataclasses import replace
from typing import Any

from autonomy.decision_cycle.memory.plugin_runner import PluginMemoryRunner
from autonomy.shared_memory import SharedMemory
from autonomy.decision_cycle.cycle import (
    DecisionCycle,
    DecisionCycleResult,
    DecisionFrameContext,
    DecisionSteps,
)
from .manager import AutonomyManager


class AutonomyCycleHost:
    """Run one decision cycle around a loadable autonomy engine."""

    def __init__(
        self,
        *,
        manager: AutonomyManager | None = None,
        steps: DecisionSteps | None = None,
    ) -> None:
        configured_steps = steps or DecisionSteps()
        if configured_steps.act is not None:
            raise ValueError("AutonomyCycleHost owns the decision action step")

        self.manager = manager or AutonomyManager()
        self.cycle = DecisionCycle(
            replace(configured_steps, act=self.manager.act),
            idle_reason="engine-idle",
        )
        self.shared_memory: SharedMemory = {}
        self.last_result: DecisionCycleResult | None = None

    def run(self, context: DecisionFrameContext) -> DecisionCycleResult:
        if context.shared_memory is None:
            context = replace(context, shared_memory=self.shared_memory)
        else:
            self.shared_memory = context.shared_memory
        result = self.cycle.run(context)
        self.last_result = result
        return result

    def status(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "engine": self.manager.status(),
            "last_cycle": self.last_result.to_dict() if self.last_result is not None else None,
        }
        remember = self.cycle.steps.remember
        if remember is not None and callable(getattr(remember, "status", None)):
            payload["memory"] = remember.status()
        return payload

    def reset_memory(self) -> dict[str, Any] | None:
        """Reset the activated memory step when present.

        Clears the host map and keeps only the fresh state memory plugins
        wrote while resetting (such as a new epoch). Returns the memory report
        after the reset, or ``None`` when no memory step is configured.
        """
        remember = self.cycle.steps.remember
        if remember is None:
            self.shared_memory.clear()
            return None
        if not isinstance(remember, PluginMemoryRunner):
            raise TypeError("configured memory step does not support reset")
        fresh = remember.reset(self.shared_memory)
        self.shared_memory.clear()
        self.shared_memory.update(fresh)
        return remember.report()
