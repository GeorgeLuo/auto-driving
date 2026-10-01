"""The shared shape of a step runner.

A runner is the callable a ``DecisionSteps`` slot holds. It owns one step's
``PluginManager``, loads the manager's selection through ``instantiate_plugin``,
checks each instance against the step's plugin protocol, and runs the applied
plugins when the cycle calls it. Every runner can be built from a
``StepActivation`` and exposes the same selection and report surface:
``prepare_selection``/``commit_selection``/``discard_selection`` to change the
selection between cycles, ``plugin_report`` and ``status`` for diagnostics,
and ``reset`` to start a new epoch.

``StepRunner`` implements that surface for steps whose plugins need no more
than load, validate, and reset. The perception and memory runners implement the
same surface with their own component and host-map handling.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from threading import RLock
from typing import Any, ClassVar, Generic, TypeVar

from autonomy.decision_cycle.activation import StepActivation, require_step
from autonomy.plugins import (
    PluginDefinition,
    PluginManager,
    PluginSelectionRuntime,
    instantiate_plugin,
    plugin_report as build_plugin_report,
    require_plugin_id,
)
from autonomy.shared_memory import SharedMemory

PluginT = TypeVar("PluginT")
# Entrypoint module name for instances handed to ``StepRunner.from_plugins``.
PROVIDED_ENTRYPOINT = "provided"


def require_step_manager(plugin_manager: object, step: str) -> PluginManager:
    if not isinstance(plugin_manager, PluginManager):
        raise TypeError("plugin_manager must be a PluginManager")
    if plugin_manager.step != step:
        raise ValueError(f"plugin_manager is scoped to {plugin_manager.step!r}, not {step!r}")
    return plugin_manager


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
    """

    step: ClassVar[str]
    single_plugin: ClassVar[bool] = False

    def __init__(
        self,
        plugin_manager: PluginManager,
        *,
        provided: Mapping[str, PluginT] | None = None,
    ) -> None:
        require_step(self.step)
        self.plugin_manager = require_step_manager(plugin_manager, self.step)
        self.activation: StepActivation | None = None
        self._provided = dict(provided or {})
        self._selection_runtime: PluginSelectionRuntime[PluginT] = PluginSelectionRuntime(
            plugin_manager
        )
        self._runtime_lock = RLock()
        self.last_error: str | None = None
        self.run_count = 0
        self.failure_count = 0
        self.apply_selection()

    @classmethod
    def from_activation(cls, activation: StepActivation):
        runner = cls(require_step_activation(activation, cls.step).plugin_manager())
        runner.activation = activation
        return runner

    @classmethod
    def from_plugins(cls, plugins: Mapping[str, PluginT]):
        """Run already constructed plugins, selected in the mapping's order.

        For hosts and tools that build plugin instances themselves; the
        instances are validated like loaded ones.
        """

        if not isinstance(plugins, Mapping):
            raise TypeError("plugins must map plugin IDs to plugin instances")
        manager = PluginManager.from_specs(
            cls.step, {plugin_id: f"{PROVIDED_ENTRYPOINT}:{plugin_id}" for plugin_id in plugins}
        )
        manager.select(tuple(plugins))
        return cls(manager, provided=plugins)

    # Selection -------------------------------------------------------------

    @property
    def applied(self) -> tuple[tuple[PluginDefinition, PluginT], ...]:
        return self._selection_runtime.applied

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

    def _validate_selection(self, plugins: tuple[PluginT, ...]) -> None:
        if self.single_plugin and len(plugins) != 1:
            raise ValueError(
                f"the {self.step} step runs exactly one plugin; {len(plugins)} selected"
            )

    def prepare_selection(self) -> None:
        """Load and validate the manager's selection without publishing it."""

        with self._runtime_lock:
            self._selection_runtime.prepare(
                load=self.load_plugin, validate=self._validate_selection
            )

    def commit_selection(self, shared_memory: SharedMemory | None = None) -> None:
        """Reset removed plugins and publish the prepared selection."""

        with self._runtime_lock:
            self._selection_runtime.commit(
                reset=lambda plugin: reset_plugin(plugin, shared_memory)
            )

    def discard_selection(self) -> None:
        with self._runtime_lock:
            self._selection_runtime.discard()

    def apply_selection(self, shared_memory: SharedMemory | None = None) -> None:
        with self._runtime_lock:
            self._selection_runtime.apply(
                load=self.load_plugin,
                validate=self._validate_selection,
                reset=lambda plugin: reset_plugin(plugin, shared_memory),
            )

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        with self._runtime_lock:
            for _definition, plugin in self.applied:
                reset_plugin(plugin, shared_memory)
            self.last_error = None

    # Reports ---------------------------------------------------------------

    def plugin_report(self) -> dict[str, Any]:
        with self._runtime_lock:
            records = [
                {
                    "plugin_id": definition.plugin_id,
                    "implementation_id": getattr(plugin, "plugin_id", None),
                    "duration_ms": None,
                    "error": self.last_error,
                }
                for definition, plugin in self.applied
            ]
            return build_plugin_report(self.plugin_manager, self.applied, records)

    def status(self) -> dict[str, Any]:
        with self._runtime_lock:
            return {
                "step": self.step,
                "activation": (
                    str(self.activation.source_path)
                    if self.activation is not None and self.activation.source_path
                    else None
                ),
                "available_plugins": sorted(self.plugin_manager.available_ids),
                "selected_plugin_ids": list(self.plugin_manager.selected_ids),
                "plugin_ids": list(self.plugin_ids),
                "plugin_report": self.plugin_report(),
                "run_count": self.run_count,
                "failure_count": self.failure_count,
                "last_error": self.last_error,
            }

    def _single(self) -> PluginT:
        return self.applied[0][1]


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
