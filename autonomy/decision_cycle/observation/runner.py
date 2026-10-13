"""Observation step runner."""

from __future__ import annotations

from typing import ClassVar

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.plugin import ObservationPlugin
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.interface import PerceptionText
from autonomy.decision_cycle.runner import StepRunner, describe_exception
from autonomy.plugins import PluginDefinition


class ObservationRunner(StepRunner[ObservationPlugin]):
    """Run the selected observation plugin; its failure stops the cycle."""

    step: ClassVar[str] = "observation"
    single_plugin: ClassVar[bool] = True

    def validate_plugin(self, plugin: ObservationPlugin, definition: PluginDefinition) -> None:
        if not callable(getattr(plugin, "observe", None)):
            raise TypeError(f"observation plugin {definition.entrypoint} must implement observe()")

    def __call__(
        self, context: DecisionFrameContext, perception: PerceptionText | None
    ) -> Observation | None:
        with self._runtime_lock:
            self.run_count += 1
            try:
                observation = self._single().observe(context, perception)
                if observation is not None and not isinstance(observation, Observation):
                    raise TypeError("observation plugin must return an Observation or None")
            except Exception as exc:
                self.failure_count += 1
                self.last_error = describe_exception(exc)
                raise
            self.last_error = None
            return observation
