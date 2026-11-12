"""Observation step runner."""

from __future__ import annotations

from typing import ClassVar

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.plugin import ObservationPlugin
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.interface import PerceptionText
from autonomy.decision_cycle.plugin_state import unchanged_instance
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
        with self._runtime_lock, self._timed_single() as plugin:
            self.run_count += 1
            try:
                with unchanged_instance(plugin):
                    returned = plugin.observe(context, perception)
                observation = _observation(returned)
            except Exception as exc:
                self.failure_count += 1
                self.last_error = describe_exception(exc)
                raise
            self.last_error = None
            return observation


def _observation(returned: object) -> Observation | None:
    """An observation plugin's return, which must be an ``Observation`` or None."""

    if returned is not None and not isinstance(returned, Observation):
        raise TypeError("observation plugin must return an Observation or None")
    return returned
