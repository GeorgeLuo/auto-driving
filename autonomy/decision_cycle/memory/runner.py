"""Memory step runner.

``MemoryRunner`` applies the manager's selection and runs each selected
plugin through ``MemoryPluginRuntime`` in selection order. Its return value is
a report of each plugin's own state summary for diagnostics; decisions read
plugin-published keys in the host map, not the report.
"""

from __future__ import annotations

import time
from threading import RLock
from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.activation import StepActivation
from autonomy.decision_cycle.memory.execution.plugin_runtime import MemoryPluginRuntime
from autonomy.decision_cycle.memory.plugin import MemoryPlugin
from autonomy.decision_cycle.memory.publication import withdraw_publication
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.runner import (
    PROVIDED_ENTRYPOINT,
    require_step_activation,
    require_step_manager,
)
from autonomy.plugins import (
    PluginDefinition,
    PluginManager,
    PluginSelectionRuntime,
    plugin_report as build_plugin_report,
)
from autonomy.shared_memory import SharedMemory

MEMORY_REPORT_SCHEMA = "memory_report_v0"


class MemoryRunner:
    """Run the manager's selected memory plugins once per decision cycle.

    Plugins run in selection order on the same host map. Each plugin writes its
    own keys; where two plugins publish to the same key, the later one wins.
    The framework does not merge retention policies.
    """

    step = "memory"

    def __init__(
        self,
        plugin_manager: PluginManager,
        *,
        provided: dict[str, MemoryPlugin] | None = None,
    ) -> None:
        self.activation: StepActivation | None = None
        self.plugin_manager = require_step_manager(plugin_manager, self.step)
        self._provided = dict(provided or {})
        self._selection_runtime = PluginSelectionRuntime(plugin_manager)
        self._runtime_lock = RLock()
        self.plugin_ids: tuple[str, ...] = ()
        self.plugins: tuple[MemoryPluginRuntime, ...] = ()
        self.last_duration_ms: float | None = None
        self.last_error: str | None = None
        self.update_count = 0
        self.reset_count = 0
        self.failure_count = 0
        self._apply_selection()

    @classmethod
    def from_activation(cls, activation: StepActivation) -> "MemoryRunner":
        runner = cls(require_step_activation(activation, cls.step).plugin_manager())
        runner.activation = activation
        return runner

    @classmethod
    def from_plugins(cls, plugins: dict[str, MemoryPlugin]) -> "MemoryRunner":
        """Run already constructed plugins, selected in the mapping's order."""

        manager = PluginManager.from_specs(
            cls.step, {plugin_id: f"{PROVIDED_ENTRYPOINT}:{plugin_id}" for plugin_id in plugins}
        )
        manager.select(tuple(plugins))
        return cls(manager, provided=plugins)

    def _load_plugin(self, definition: PluginDefinition) -> MemoryPluginRuntime:
        provided = None
        if definition.entrypoint == f"{PROVIDED_ENTRYPOINT}:{definition.plugin_id}":
            provided = self._provided[definition.plugin_id]
        source_path = self.activation.source_path if self.activation else definition.source
        return MemoryPluginRuntime(definition, source_path=source_path, plugin=provided)

    def prepare_selection(self) -> None:
        """Load the manager selection without resetting or publishing it."""

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

    def _apply_selection(self, shared_memory: SharedMemory | None = None) -> None:
        self._prepare_selection()
        self._commit_selection(shared_memory)

    def _prepare_selection(self) -> None:
        self._selection_runtime.prepare(load=self._load_plugin)

    def _commit_selection(self, shared_memory: SharedMemory | None = None) -> None:
        applied = self._selection_runtime.commit(
            reset=lambda plugin: plugin.reset(shared_memory),
        )
        self.plugin_ids = tuple(definition.plugin_id for definition, _plugin in applied)
        self.plugins = tuple(plugin for _definition, plugin in applied)

    def _discard_selection(self) -> None:
        self._selection_runtime.discard()

    def __call__(
        self, context: DecisionFrameContext, observation: Observation | None,
    ) -> dict[str, Any]:
        return self.update(context, observation)

    def update(
        self, context: DecisionFrameContext, observation: Observation | None,
    ) -> dict[str, Any]:
        """Run each selected plugin in order and return the memory report."""

        with self._runtime_lock:
            self._apply_selection(context.shared_memory)
            started = time.perf_counter()
            self.last_error = None
            try:
                for plugin in self.plugins:
                    plugin.update(context, observation)
                if not self.plugins and context.shared_memory is not None:
                    withdraw_publication(context.shared_memory)
            except Exception:
                self.failure_count += 1
                self.last_error = plugin.last_error
                raise
            finally:
                self.update_count += 1
                self.last_duration_ms = (time.perf_counter() - started) * 1000.0
            return self._report()

    def reset(self, shared_memory: SharedMemory | None = None) -> dict[str, Any]:
        """Reset each plugin and return the keys the plugins wrote while resetting.

        A host that clears its map at a reset restores these so plugins keep the
        fresh state (for example a new epoch) they started.
        """

        with self._runtime_lock:
            started = time.perf_counter()
            self.last_error = None
            before = dict(shared_memory) if shared_memory is not None else {}
            for plugin in self.plugins:
                failures = plugin.failure_count
                plugin.reset(shared_memory)
                self.failure_count += plugin.failure_count - failures
                self.last_error = plugin.last_error or self.last_error
            if not self.plugins and shared_memory is not None:
                withdraw_publication(shared_memory)
            self.reset_count += 1
            self.last_duration_ms = (time.perf_counter() - started) * 1000.0
            if shared_memory is None:
                return {}
            return {
                key: value
                for key, value in shared_memory.items()
                if key not in before or before[key] is not value
            }

    def report(self) -> dict[str, Any]:
        """Each applied plugin's own state summary, as the cycle records it."""

        with self._runtime_lock:
            return self._report()

    def _report(self) -> dict[str, Any]:
        return {
            "schema": MEMORY_REPORT_SCHEMA,
            "plugins": [
                {
                    "plugin_id": plugin.plugin_id,
                    "implementation_id": plugin.implementation_id,
                    "state": plugin.plugin_status(),
                }
                for plugin in self.plugins
            ],
        }

    def plugin_report(self) -> dict[str, Any]:
        """Report catalog, requested, and published plugins for applied instances."""

        with self._runtime_lock:
            return self._plugin_report()

    def _plugin_report(self) -> dict[str, Any]:
        records = [
            {
                "plugin_id": plugin.plugin_id,
                "implementation_id": plugin.implementation_id,
                "duration_ms": plugin.last_duration_ms,
                "error": plugin.last_error,
            }
            for plugin in self.plugins
        ]
        return build_plugin_report(
            self.plugin_manager,
            self._selection_runtime.applied,
            records,
        )

    def status(self) -> dict[str, Any]:
        with self._runtime_lock:
            final = self.plugins[-1] if self.plugins else None
            return {
                "implementation_id": final.implementation_id if final else None,
                "implementation_spec": final.definition.entrypoint if final else None,
                "activation": (
                    str(self.activation.source_path)
                    if self.activation and self.activation.source_path
                    else None
                ),
                "available_plugins": sorted(self.plugin_manager.available_ids),
                "selected_plugin_ids": list(self.plugin_manager.selected_ids),
                "plugin_ids": list(self.plugin_ids),
                "plugins": [plugin.status() for plugin in self.plugins],
                "plugin_report": self._plugin_report(),
                "update_count": self.update_count,
                "reset_count": self.reset_count,
                "failure_count": self.failure_count,
                "last_duration_ms": self.last_duration_ms,
                "last_error": self.last_error,
            }
