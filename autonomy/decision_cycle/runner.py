"""The shared shape of a step runner.

A runner is the callable a ``DecisionSteps`` slot holds. It is constructed
from one ordered sequence of plugin definitions, loads those definitions
through ``instantiate_plugin``, checks each instance against the step's plugin
protocol, and keeps that applied tuple. A step call runs those plugins. It
does not take a new selection. A different selection is a new runner.
``plugin_report``, ``status`` and ``plugin_timing`` are the diagnostics, and
``reset(shared_memory)`` clears the applied plugins' history. A plugin whose
reset raises follows the step's ``FAILURE_POLICY.reset``, or propagates in a
step that declares none.
Memory's ``reset`` also returns the keys its plugins wrote, for a host that
clears its map at a reset to restore.

Perception, memory, and plan declare what a plugin failure or a missing input
does as one ``FailurePolicy``, ``FAILURE_POLICY`` in the step's ``interface``,
which the step's runner follows.

Every step's runner is a ``StepRunner``. A step adds what its plugins need
through the hooks ``StepRunner`` names, not by reimplementing the surface.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Any, ClassVar, Generic, TypeVar

from autonomy.decision_cycle.activation import (
    StepActivation, activation_presets, require_step, selected_definitions,
)
from autonomy.decision_cycle.timing import clock, elapsed_ms
from autonomy.plugins import (
    PluginDefinition,
    instantiate_plugin,
    plugin_report as build_plugin_report,
    require_plugin_id,
)
from autonomy.shared_memory import SharedMemory

PluginT = TypeVar("PluginT")
# Entrypoint module name for instances handed to ``StepRunner.from_plugins``.
PROVIDED_ENTRYPOINT = "provided"

FAILURE_POLICY_FIELDS = ("update", "reset", "missing_input")
# The values a step may declare for each field:
# - update: a plugin that raises while it runs. ``isolate_plugin`` records the
#   error as that plugin's result and the other plugins still run;
#   ``stop_cycle`` re-raises, which ends the step and the cycle.
# - reset: a plugin whose reset raises. ``propagate`` re-raises to the caller;
#   ``record`` records the error on the plugin and the other plugins still
#   reset.
# - missing_input: an input the plugin reads is absent this cycle.
#   ``skip_plugin`` does not call the plugin; ``invoke`` calls it with the
#   input missing.
FAILURE_POLICY_VALUES: Mapping[str, frozenset[str]] = {
    "update": frozenset({"isolate_plugin", "stop_cycle"}),
    "reset": frozenset({"propagate", "record"}),
    "missing_input": frozenset({"skip_plugin", "invoke"}),
}


@dataclass(frozen=True)
class FailurePolicy:
    """What one step does when a plugin fails or an input is missing."""

    update: str
    reset: str
    missing_input: str

    def __post_init__(self) -> None:
        for name in FAILURE_POLICY_FIELDS:
            value = getattr(self, name)
            if value not in FAILURE_POLICY_VALUES[name]:
                allowed = ", ".join(sorted(FAILURE_POLICY_VALUES[name]))
                raise ValueError(f"failure policy {name} must be one of {allowed}; got {value!r}")


def require_step_activation(activation: object, step: str) -> StepActivation:
    if not isinstance(activation, StepActivation):
        raise TypeError("activation must be a StepActivation")
    if activation.step != step:
        raise ValueError(f"activation is for step {activation.step!r}, not {step!r}")
    return activation


class StepRunner(Generic[PluginT]):
    """Load, publish, and report one step's selected plugins.

    ``single_plugin`` steps run exactly one selected plugin; the others run
    every selected plugin, in selection order, with no count limit.

    A step customizes these hooks:

    - ``load_plugin`` and ``validate_plugin``: what one selected plugin loads as.
    - ``validate_selection``: whether the loaded selection can run together.
      It runs once, during construction, before the runner is published.
    - ``_reset_plugin``: how one plugin resets.
    - ``_record_plugin``: one plugin's duration and error, measured with
      ``timing.clock`` where the step invokes it. ``_timed_single`` times a
      ``single_plugin`` step's one plugin. A step call that may skip plugins
      starts with ``_clear_plugin_records``.
    - ``_plugin_records``: each plugin's timing and error in ``plugin_report``
      and ``plugin_timing``.
    - ``status``: extend ``super().status()`` with the step's own fields.

    A subclass sets the state its hooks read before calling
    ``super().__init__``, which loads the definitions.
    """

    step: ClassVar[str]
    single_plugin: ClassVar[bool] = False

    def __init__(
        self,
        definitions: Sequence[PluginDefinition],
        *,
        activation: StepActivation | None = None,
        provided: Mapping[str, PluginT] | None = None,
    ) -> None:
        require_step(self.step)
        if isinstance(definitions, (str, bytes)) or not isinstance(definitions, Sequence):
            raise TypeError("definitions must be an ordered sequence of PluginDefinition")
        self.activation = activation
        self._provided = dict(provided or {})
        self._runtime_lock = RLock()
        self.last_error: str | None = None
        self.run_count = 0
        self.failure_count = 0
        self._plugin_runs: dict[str, dict[str, Any]] = {}
        loaded: list[tuple[PluginDefinition, PluginT]] = []
        seen: set[str] = set()
        for definition in definitions:
            if not isinstance(definition, PluginDefinition):
                raise TypeError("definitions must be PluginDefinition values")
            if definition.step != self.step:
                raise ValueError(
                    f"plugin {definition.plugin_id!r} belongs to {definition.step!r}, not {self.step!r}"
                )
            if definition.plugin_id in seen:
                raise ValueError(f"{self.step} plugins must be unique")
            seen.add(definition.plugin_id)
            loaded.append((definition, self.load_plugin(definition)))
        self.validate_selection(tuple(plugin for _definition, plugin in loaded))
        self._applied = tuple(loaded)

    @classmethod
    def from_activation(cls, activation: StepActivation):
        activation = require_step_activation(activation, cls.step)
        return cls(selected_definitions(activation), activation=activation)

    @classmethod
    def from_plugins(cls, plugins: Mapping[str, PluginT]):
        """Run already constructed plugins, selected in the mapping's order.

        For hosts and tools that build plugin instances themselves. This runner
        cannot be rebuilt from an activation. A pipeline replacement does not
        keep it.
        """

        if not isinstance(plugins, Mapping):
            raise TypeError("plugins must map plugin IDs to plugin instances")
        definitions = tuple(
            PluginDefinition(cls.step, plugin_id, f"{PROVIDED_ENTRYPOINT}:{plugin_id}")
            for plugin_id in plugins
        )
        return cls(definitions, provided=plugins)

    # Selection -------------------------------------------------------------

    @property
    def applied(self) -> tuple[tuple[PluginDefinition, PluginT], ...]:
        return self._applied

    @property
    def plugin_ids(self) -> tuple[str, ...]:
        return tuple(definition.plugin_id for definition, _plugin in self.applied)

    @property
    def plugins(self) -> dict[str, PluginT]:
        """The applied plugins by selected plugin ID, in selection order."""

        return {definition.plugin_id: plugin for definition, plugin in self.applied}

    def load_plugin(self, definition: PluginDefinition) -> PluginT:
        if definition.entrypoint == f"{PROVIDED_ENTRYPOINT}:{definition.plugin_id}":
            plugin = self._provided[definition.plugin_id]
        else:
            plugin = instantiate_plugin(definition)
        require_plugin_id(plugin, definition)
        self.validate_plugin(plugin, definition)
        return plugin

    def validate_plugin(self, plugin: PluginT, definition: PluginDefinition) -> None:
        """Raise ``TypeError`` when ``plugin`` does not satisfy the step protocol."""

    def validate_selection(self, plugins: tuple[PluginT, ...]) -> None:
        """Raise when the loaded plugins cannot run together."""

        if self.single_plugin and len(plugins) != 1:
            raise ValueError(
                f"the {self.step} step runs exactly one plugin; {len(plugins)} selected"
            )

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        with self._runtime_lock:
            for _definition, plugin in self.applied:
                self._reset_plugin(plugin, shared_memory)
            self.last_error = None
            self._clear_plugin_records()

    def _reset_plugin(self, plugin: PluginT, shared_memory: SharedMemory | None) -> None:
        reset_plugin(plugin, shared_memory)

    # Reports ---------------------------------------------------------------

    def plugin_report(self) -> dict[str, Any]:
        """Report each fixed applied plugin and its execution record."""

        with self._runtime_lock:
            return build_plugin_report(self.applied, self._plugin_records())

    def plugin_timing(self) -> list[dict[str, Any]]:
        """Each applied plugin's duration and error from the last step call.

        ``DecisionCycle`` copies this into the cycle's ``timing``; the cycle
        times the step call itself. It is the record ``plugin_report`` shows,
        without the catalog.
        """

        with self._runtime_lock:
            return self._plugin_records()

    def _clear_plugin_records(self) -> None:
        """Start a step call with no plugin records. The caller holds the lock.

        A plugin the call skips then reports a null duration instead of the
        previous call's.
        """

        self._plugin_runs.clear()

    def _record_plugin(self, plugin_id: str, duration_ms: float | None, error: str | None) -> None:
        """Keep one plugin invocation for ``_plugin_records``. The caller holds the lock.

        ``duration_ms`` is ``timing.elapsed_ms`` of a ``timing.clock`` reading
        taken before the call. A ``reset`` clears the records.
        """

        self._plugin_runs[plugin_id] = {
            "plugin_id": plugin_id,
            "duration_ms": duration_ms,
            "error": error,
        }

    def _plugin_records(self) -> list[dict[str, Any]]:
        """Each applied plugin's ``plugin_id``, ``duration_ms`` and ``error``."""

        return [
            {
                "plugin_id": definition.plugin_id,
                "duration_ms": None,
                "error": None,
                **self._plugin_runs.get(definition.plugin_id, {}),
            }
            for definition, _plugin in self.applied
        ]

    def status(self) -> dict[str, Any]:
        with self._runtime_lock:
            return {
                "step": self.step,
                "activation": (
                    str(self.activation.source_path)
                    if self.activation is not None and self.activation.source_path
                    else None
                ),
                "preset": activation_presets({self.step: self.activation}).get(self.step),
                "selected_plugin_ids": list(self.plugin_ids),
                "plugin_ids": list(self.plugin_ids),
                # What each applied plugin runs with; a restaged config waits for a restart.
                "plugin_configs": {
                    definition.plugin_id: deepcopy(dict(definition.config))
                    for definition, _plugin in self.applied
                },
                "plugin_report": self.plugin_report(),
                "run_count": self.run_count,
                "failure_count": self.failure_count,
                "last_error": self.last_error,
            }

    @contextmanager
    def _timed_single(self) -> Iterator[PluginT]:
        """Yield a ``single_plugin`` step's plugin and record its call.

        The record's error is ``last_error`` when the block exits, which the
        step sets for the call. The caller holds the lock.
        """

        definition, plugin = self.applied[0]
        started = clock()
        try:
            yield plugin
        finally:
            self._record_plugin(definition.plugin_id, elapsed_ms(started), self.last_error)


def reset_plugin(plugin: Any, shared_memory: SharedMemory | None = None) -> None:
    """Call the plugin's optional ``reset``, passing the host map when it takes one."""

    reset = getattr(plugin, "reset", None)
    if not callable(reset):
        return
    if _accepts_argument(reset):
        reset(shared_memory)
    else:
        reset()


def _accepts_argument(function: Any) -> bool:
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        )
        for parameter in parameters
    )


def describe_exception(exc: BaseException) -> str:
    try:
        return f"{type(exc).__name__}: {exc}"
    except Exception:  # noqa: BLE001 - unprintable exceptions still get a label
        return f"{type(exc).__name__}: <unprintable exception>"
