"""The shared shape of a step runner.

A runner is the callable a ``DecisionSteps`` slot holds. It owns one step's
``PluginManager``, loads the manager's selection through ``instantiate_plugin``,
checks each instance against the step's plugin protocol, and runs the applied
plugins when the cycle calls it. Every runner can be built from a
``StepActivation`` and exposes the same selection and report surface:
``plugins``, the applied plugins by selected plugin ID in selection order, and
their ``plugin_ids``;
``prepare_selection``/``commit_selection``/``discard_selection`` to change the
selection between cycles (each call also picks up the manager's selection);
``plugin_report`` and ``status`` for diagnostics; and
``reset(shared_memory)`` to start a new epoch. ``reset`` resets every applied
plugin with the host map, and a plugin whose reset raises follows the step's
``FAILURE_POLICY.reset``. Memory's ``reset`` also returns the keys its plugins
wrote, for a host that clears its map at a reset to restore.

Perception, memory, and proposal declare what a plugin failure or a missing
input does as one ``FailurePolicy``, ``FAILURE_POLICY`` in the step's
``interface``. Their ``describe_schema`` methods report it under
``failure_policy``. A step that describes its contract builds the
``configuration`` and ``plugins``
entries of that schema with ``describe_configuration`` and ``describe_plugin``.

Every step's runner is a ``StepRunner``. A step adds what its plugins need
through the hooks ``StepRunner`` names, not by reimplementing the surface.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable, Mapping
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass
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

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in FAILURE_POLICY_FIELDS}


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

    A step customizes these hooks:

    - ``load_plugin`` and ``validate_plugin``: what one selected plugin loads as.
    - ``validate_selection``: whether the loaded selection can run together.
    - ``_prepare_selection``, ``_commit_selection`` and ``_discard_selection``:
      state the step publishes with its plugins. They run under the lock the
      public selection methods take.
    - ``_reset_plugin``: how one plugin resets.
    - ``_plugin_records``: each plugin's timing and error in ``plugin_report``.
    - ``status``: extend ``super().status()`` with the step's own fields.

    A subclass sets the state its hooks read before calling
    ``super().__init__``, which applies the manager's selection.
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
        self._selection_held = 0
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

    def validate_selection(self, plugins: tuple[PluginT, ...]) -> None:
        """Raise when a changed selection's loaded plugins cannot run together."""

        if self.single_plugin and len(plugins) != 1:
            raise ValueError(
                f"the {self.step} step runs exactly one plugin; {len(plugins)} selected"
            )

    def prepare_selection(self) -> None:
        """Load and validate the manager's selection without publishing it."""

        with self._runtime_lock:
            self._prepare_selection()

    def commit_selection(self, shared_memory: SharedMemory | None = None) -> None:
        """Reset removed plugins and publish the prepared selection."""

        with self._runtime_lock:
            self._commit_selection(shared_memory)

    def discard_selection(self) -> None:
        """Drop a prepared selection without resetting published plugins."""

        with self._runtime_lock:
            self._discard_selection()

    def apply_selection(self, shared_memory: SharedMemory | None = None) -> None:
        """Prepare and commit the manager's selection; a failure raises."""

        with self._runtime_lock:
            if self._selection_held:
                return
            self._prepare_selection()
            self._commit_selection(shared_memory)

    @contextmanager
    def hold_selection(self):
        """Keep a host's prepared group fixed throughout one cycle."""

        with self._runtime_lock:
            self._selection_held += 1
            try:
                yield
            finally:
                self._selection_held -= 1

    def adopt_activation(self, activation: StepActivation) -> None:
        """Record a staged document only after its definitions have applied."""

        self.activation = require_step_activation(activation, self.step)

    def _prepare_selection(self) -> None:
        self._selection_runtime.prepare(load=self.load_plugin, validate=self.validate_selection)

    def _commit_selection(self, shared_memory: SharedMemory | None = None) -> None:
        self._selection_runtime.commit(
            reset=lambda plugin: self._reset_plugin(plugin, shared_memory)
        )

    def _discard_selection(self) -> None:
        self._selection_runtime.discard()

    def refresh_selection(self, shared_memory: SharedMemory | None = None) -> None:
        """Apply the manager's current selection before a call.

        A selection that fails to load or validate keeps the applied plugins
        and is recorded as ``last_error``, so a bad edit cannot stop the cycle.
        Perception and memory call ``apply_selection`` instead: a selection
        they cannot load stops the step, with the applied plugins kept.
        """

        try:
            self.apply_selection(shared_memory)
        except Exception as exc:  # noqa: BLE001 - keep the applied plugins
            self.last_error = describe_exception(exc)

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        with self._runtime_lock:
            for _definition, plugin in self.applied:
                self._reset_plugin(plugin, shared_memory)
            self.last_error = None

    def _reset_plugin(self, plugin: PluginT, shared_memory: SharedMemory | None) -> None:
        reset_plugin(plugin, shared_memory)

    # Reports ---------------------------------------------------------------

    def plugin_report(self) -> dict[str, Any]:
        """Report catalog, requested selection, and each applied plugin's record."""

        with self._runtime_lock:
            return build_plugin_report(self.plugin_manager, self.applied, self._plugin_records())

    def _plugin_records(self) -> list[dict[str, Any]]:
        """Each applied plugin's ``plugin_id``, ``duration_ms`` and ``error``."""

        return [
            {"plugin_id": definition.plugin_id, "duration_ms": None, "error": self.last_error}
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


def describe_configuration(
    plugin_manager: PluginManager,
    applied: Iterable[tuple[PluginDefinition, Any]],
) -> dict[str, Any]:
    """The ``configuration`` entry of a step schema: catalog, selection, and applied plugins."""

    report = build_plugin_report(plugin_manager, applied)
    available = plugin_manager.available
    return {
        "plugins": list(report["applied_plugin_ids"]),
        "available_plugins": sorted(item.plugin_id for item in available),
        "selected_plugin_ids": list(report["selected_plugin_ids"]),
        "applied_plugin_ids": list(report["applied_plugin_ids"]),
        "plugin_specs": {item.plugin_id: item.entrypoint for item in available},
        "plugin_configs": {item.plugin_id: deepcopy(dict(item.config)) for item in available},
    }


def describe_plugin(definition: PluginDefinition) -> dict[str, Any]:
    """One applied plugin in a step schema's ``plugins`` list; a step adds its own fields."""

    return {
        "plugin_id": definition.plugin_id,
        "spec": definition.entrypoint,
        "config": deepcopy(dict(definition.config)),
    }


def describe_exception(exc: BaseException) -> str:
    try:
        return f"{type(exc).__name__}: {exc}"
    except Exception:  # noqa: BLE001 - unprintable exceptions still get a label
        return f"{type(exc).__name__}: <unprintable exception>"
