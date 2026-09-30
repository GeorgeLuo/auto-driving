"""Memory plugin selection and execution at the decision-cycle boundary.

``PluginMemoryRunner`` applies the manager's selection and runs each selected
plugin through ``MemoryPluginRuntime`` in selection order.
"""

from __future__ import annotations

import time
from pathlib import Path
from threading import RLock
from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.activation import MemoryActivation, memory_manager_from_activation
from autonomy.decision_cycle.memory.execution.plugin_runtime import MemoryPluginRuntime
from autonomy.decision_cycle.memory.plugin import MemoryImplementation
from autonomy.decision_cycle.memory.publication import withdraw_publication
from autonomy.decision_cycle.memory.snapshots.values import MemorySnapshot
from autonomy.decision_cycle.observation.values import Observation
from autonomy.plugins import (
    PluginDefinition,
    PluginManager,
    PluginSelectionRuntime,
    plugin_report as build_plugin_report,
)
from autonomy.shared_memory import SharedMemory


class PluginMemoryRunner:
    """Run the manager's selected memory plugins once per decision cycle.

    Plugins run in selection order on the same host map, and each plugin's
    runtime publishes its accepted value before the next plugin runs. The final
    plugin's value is the decision-facing one; the framework does not merge
    retention policies.
    """

    def __init__(
        self,
        activation: MemoryActivation | None = None,
        *,
        plugin_manager: PluginManager | None = None,
    ) -> None:
        if plugin_manager is None:
            if activation is None:
                raise ValueError("memory requires an activation or plugin_manager")
            plugin_manager = memory_manager_from_activation(activation)
        if not isinstance(plugin_manager, PluginManager):
            raise TypeError("plugin_manager must be a PluginManager")
        if plugin_manager.step != "memory":
            raise ValueError(f"plugin_manager is scoped to {plugin_manager.step!r}, not 'memory'")
        self.activation = activation
        self.plugin_manager = plugin_manager
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

    @property
    def implementation(self) -> MemoryImplementation | None:
        """Compatibility access for callers inspecting a single applied plugin."""
        return self.plugins[0].implementation if len(self.plugins) == 1 else None

    def _load_plugin(self, definition: PluginDefinition) -> MemoryPluginRuntime:
        return MemoryPluginRuntime(
            definition,
            source_path=(
                self.activation.source_path if self.activation else definition.source or Path("memory-manager")
            ),
        )

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
    ) -> MemorySnapshot | None:
        return self.update(context, observation)

    def update(
        self, context: DecisionFrameContext, observation: Observation | None,
    ) -> MemorySnapshot | None:
        with self._runtime_lock:
            self._apply_selection(context.shared_memory)
            started = time.perf_counter()
            self.last_error = None
            snapshot = None
            try:
                for plugin in self.plugins:
                    snapshot = plugin.update(context, observation)
                if not self.plugins and context.shared_memory is not None:
                    withdraw_publication(context.shared_memory)
            except Exception:
                self.failure_count += 1
                self.last_error = plugin.last_error
                raise
            finally:
                self.update_count += 1
                self.last_duration_ms = (time.perf_counter() - started) * 1000.0
            return snapshot

    def reset(self, shared_memory: SharedMemory | None = None) -> MemorySnapshot | None:
        with self._runtime_lock:
            started = time.perf_counter()
            self.last_error = None
            snapshot = None
            for plugin in self.plugins:
                failures = plugin.failure_count
                snapshot = plugin.reset(shared_memory)
                self.failure_count += plugin.failure_count - failures
                self.last_error = plugin.last_error or self.last_error
            if not self.plugins and shared_memory is not None:
                withdraw_publication(shared_memory)
            self.reset_count += 1
            self.last_duration_ms = (time.perf_counter() - started) * 1000.0
            return snapshot

    def snapshot(self) -> MemorySnapshot | None:
        with self._runtime_lock:
            # The final plugin owns the shared decision-facing snapshot.
            snapshot = None
            if self.plugins:
                plugin = self.plugins[-1]
                failures = plugin.failure_count
                snapshot = plugin.snapshot()
                self.failure_count += plugin.failure_count - failures
                self.last_error = plugin.last_error
            return snapshot

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
                "activation": str(self.activation.source_path) if self.activation else None,
                "bounds": final.bounds.to_dict() if final else None,
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
                "last_health": final.last_health if final else None,
                "last_epoch_id": final.last_epoch_id if final else None,
                "last_record_count": final.last_record_count if final else None,
            }
