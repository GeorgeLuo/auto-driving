"""Memory step runner.

``MemoryRunner`` applies the manager's selection and runs each selected
plugin through ``MemoryPluginRuntime`` in selection order. Its return value is
the ``MemoryReport`` dict from ``interface``: each plugin's own state summary
for diagnostics, and the evidence publisher, the plugin whose value
``EVIDENCE_KEY`` holds. Decisions read plugin-published keys in the host map,
not the report.

``MemoryPluginRuntime`` times and reports one applied plugin. It sits here, in
the runner's file, as perception keeps its per-plugin execution in its own.
Update and reset failures follow ``failure_policy`` in ``interface``.
"""

from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.activation import StepActivation
from autonomy.decision_cycle.memory.interface import (
    LEDGER_SUMMARY_KEYS,
    MEMORY_REPORT_SCHEMA,
    MEMORY_SCHEMA,
    MISSING_INPUT,
    RESET_FAILURE,
    UPDATE_FAILURE,
    MemoryPluginReport,
    MemoryReport,
    composition_declaration,
    failure_policy,
)
from autonomy.decision_cycle.memory.plugin import MemoryPlugin, plugin_status
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
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
    instantiate_plugin,
    plugin_report as build_plugin_report,
    require_plugin_id,
)
from autonomy.shared_memory import SharedMemory

# Cap for status and worker-facing diagnostic strings.
DEFAULT_MAX_DIAGNOSTIC_CHARS = 1_024

# Stands for an absent ``EVIDENCE_KEY``, so a stored None still counts as a value.
_ABSENT = object()


class MemoryPluginRuntime:
    """Time and report one applied memory plugin."""

    def __init__(
        self,
        definition: PluginDefinition,
        plugin: MemoryPlugin,
        *,
        source_path: Path | None = None,
    ) -> None:
        self.definition = definition
        self.plugin_id = definition.plugin_id
        self.source_path = source_path
        self.implementation = plugin
        self.last_duration_ms: float | None = None
        self.last_error: str | None = None
        self.update_count = 0
        self.reset_count = 0
        self.failure_count = 0
        # The map the plugin last worked in, for status reads between cycles.
        self._shared_memory: SharedMemory | None = None

    def __call__(self, context: DecisionFrameContext, observation: Observation | None) -> None:
        self.update(context, observation)

    def update(self, context: DecisionFrameContext, observation: Observation | None) -> None:
        started = time.perf_counter()
        if context.shared_memory is not None:
            self._shared_memory = context.shared_memory
        try:
            self.implementation.update(context, observation)
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - step isolation boundary
            self.failure_count += 1
            self.last_error = _diagnostic(exc)
            if UPDATE_FAILURE == "stop_cycle":
                raise
        finally:
            self.last_duration_ms = (time.perf_counter() - started) * 1000.0
            self.update_count += 1

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        started = time.perf_counter()
        if shared_memory is not None:
            self._shared_memory = shared_memory
        try:
            if self._shared_memory is None:
                raise ValueError("memory reset requires a shared-memory map")
            self.implementation.reset(self._shared_memory)
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - step isolation boundary
            self.failure_count += 1
            self.last_error = _diagnostic(exc)
            if RESET_FAILURE == "propagate":
                raise
        self.last_duration_ms = (time.perf_counter() - started) * 1000.0
        self.reset_count += 1

    def plugin_status(self) -> dict[str, Any] | None:
        """The plugin's own summary of its state, or None when it offers none."""

        try:
            return plugin_status(self.implementation, self._shared_memory)
        except Exception as exc:  # noqa: BLE001 - status must not fail the caller
            return {"status_error": _diagnostic(exc)}

    def status(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "plugin_spec": self.definition.entrypoint,
            "activation": str(self.source_path) if self.source_path is not None else None,
            "update_count": self.update_count,
            "reset_count": self.reset_count,
            "failure_count": self.failure_count,
            "last_duration_ms": self.last_duration_ms,
            "last_error": self.last_error,
            "state": self.plugin_status(),
        }


class MemoryRunner:
    """Run the manager's selected memory plugins once per decision cycle.

    This runner is the step's ``MemoryBackend``.

    Plugins run in selection order on the same host map. Each plugin writes its
    own keys; where two plugins publish to the same key, the later one wins.
    The framework does not merge retention policies.

    ``evidence_publisher`` names the applied plugin whose value
    ``EVIDENCE_KEY`` holds. The runner compares the key's value before and
    after each plugin's update or reset: a plugin that leaves a different
    object there becomes the publisher, and one that removes the key clears
    it. Storing the object already there, or changing it in place, is not a
    new value. The publisher is None while the key is absent or holds a value
    no applied plugin left there, for example after a host clears its map at a
    reset, and once the publisher leaves the selection.
    """

    step = "memory"
    plugin_id = "autonomy.memory.plugin-runner-v0"

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
        # The map the plugins last worked in, and the plugin whose value
        # EVIDENCE_KEY held after the last change the runner saw.
        self._shared_memory: SharedMemory | None = None
        self._publisher: str | None = None
        self._published_evidence: object = _ABSENT
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
        if definition.entrypoint == f"{PROVIDED_ENTRYPOINT}:{definition.plugin_id}":
            plugin = self._provided[definition.plugin_id]
        else:
            plugin = instantiate_plugin(definition)
        _validate_plugin(plugin, definition)
        source_path = self.activation.source_path if self.activation else None
        return MemoryPluginRuntime(definition, plugin, source_path=source_path)

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
        if shared_memory is not None:
            self._shared_memory = shared_memory
        if self._publisher not in self.plugin_ids:
            self._publisher = None
            self._published_evidence = _ABSENT

    def _discard_selection(self) -> None:
        self._selection_runtime.discard()

    def __call__(
        self, context: DecisionFrameContext, observation: Observation | None,
    ) -> dict[str, Any]:
        return self.update(context, observation)

    def update(
        self, context: DecisionFrameContext, observation: Observation | None,
    ) -> dict[str, Any]:
        """Run each selected plugin in order and return the memory report.

        A selection that cannot load raises here, before any plugin runs, and
        leaves the applied plugins in place. Plugins get the frame context
        without ``sensor_frame``: memory reads the shared map and the
        observation, and perception puts anything it needs from the feed there.
        """

        memory_context = replace(context, sensor_frame=None)
        with self._runtime_lock:
            self._apply_selection(context.shared_memory)
            started = time.perf_counter()
            self.last_error = None
            try:
                for plugin in self.plugins:
                    before = _evidence(self._shared_memory)
                    try:
                        # A missing observation still reaches the plugin when
                        # the declared policy is ``invoke``.
                        if observation is not None or MISSING_INPUT == "invoke":
                            plugin.update(memory_context, observation)
                    finally:
                        self._note_evidence(plugin.plugin_id, before)
            except Exception as exc:
                self.failure_count += 1
                self.last_error = _diagnostic(exc)
                raise
            finally:
                self.update_count += 1
                self.last_duration_ms = (time.perf_counter() - started) * 1000.0
            return self._report()

    def reset(self, shared_memory: SharedMemory | None = None) -> dict[str, Any]:
        """Reset each plugin and return the keys the plugins wrote while resetting.

        A host that clears its map at a reset restores these so plugins keep the
        fresh state (for example a new epoch) they started. A plugin whose
        reset leaves a new value at ``EVIDENCE_KEY`` becomes the evidence
        publisher, as in an update; a key the host drops leaves none.
        """

        with self._runtime_lock:
            started = time.perf_counter()
            self.last_error = None
            if shared_memory is not None:
                self._shared_memory = shared_memory
            before = dict(shared_memory) if shared_memory is not None else {}
            for plugin in self.plugins:
                failures = plugin.failure_count
                evidence = _evidence(self._shared_memory)
                plugin.reset(shared_memory)
                self._note_evidence(plugin.plugin_id, evidence)
                self.failure_count += plugin.failure_count - failures
                self.last_error = plugin.last_error or self.last_error
            self.reset_count += 1
            self.last_duration_ms = (time.perf_counter() - started) * 1000.0
            if shared_memory is None:
                return {}
            return {
                key: value
                for key, value in shared_memory.items()
                if key not in before or before[key] is not value
            }

    def _note_evidence(self, plugin_id: str, before: object) -> None:
        after = _evidence(self._shared_memory)
        if after is not before:
            self._publisher = plugin_id if after is not _ABSENT else None
            self._published_evidence = after

    @property
    def evidence_publisher(self) -> str | None:
        """The applied plugin whose value ``EVIDENCE_KEY`` holds, or None."""

        with self._runtime_lock:
            if _evidence(self._shared_memory) is not self._published_evidence:
                return None
            return self._publisher

    def describe_schema(self) -> dict[str, Any]:
        """The step's contract: configuration, inputs, plugins, output, composition, failure."""

        with self._runtime_lock:
            return self._describe_schema()

    def _describe_schema(self) -> dict[str, Any]:
        catalog = self._plugin_report()
        available = self.plugin_manager.available
        return {
            "schema": MEMORY_SCHEMA,
            "plugin_id": self.plugin_id,
            "runner": f"{type(self).__module__}:{type(self).__name__}",
            "configuration": {
                "plugins": list(self.plugin_ids),
                "available_plugins": sorted(item.plugin_id for item in available),
                "selected_plugin_ids": list(catalog["selected_plugin_ids"]),
                "applied_plugin_ids": list(catalog["applied_plugin_ids"]),
                "plugin_specs": {item.plugin_id: item.entrypoint for item in available},
                "plugin_configs": {
                    item.plugin_id: deepcopy(dict(item.config)) for item in available
                },
            },
            "inputs": [
                {
                    "name": "observation",
                    "required": False,
                    "source": "the cycle's observation, written by perception",
                    "missing_behavior": (
                        "the plugin is still called with observation None"
                        if MISSING_INPUT == "invoke"
                        else "the plugin is not called when observation is None"
                    ),
                },
                {
                    "name": "shared_memory",
                    "required": True,
                    "source": "the host map on the frame context",
                    "missing_behavior": (
                        "update calls the plugin with the map as given; "
                        "reset records a failure when the plugin has no map"
                    ),
                },
            ],
            "plugins": [
                {
                    "plugin_id": definition.plugin_id,
                    "spec": definition.entrypoint,
                    "config": deepcopy(dict(definition.config)),
                }
                for definition, _plugin in self._selection_runtime.applied
            ],
            "output": {
                "schema": MEMORY_REPORT_SCHEMA,
                "ledger_summary_keys": list(LEDGER_SUMMARY_KEYS),
                "missing_field_behavior": (
                    "the report preserves plugin status without adding missing keys; "
                    "the framework does not reject the plugin; live CLI ledger "
                    "projections return null for missing keys; inspect and workbench "
                    "frame rows default a missing record_count to 0, preserve an "
                    "explicit null, and omit bounds"
                ),
            },
            "composition": composition_declaration(),
            "failure_policy": failure_policy(),
        }

    def report(self) -> dict[str, Any]:
        """Each applied plugin's own state summary, as the cycle records it."""

        with self._runtime_lock:
            return self._report()

    def _report(self) -> dict[str, Any]:
        return MemoryReport(
            schema=MEMORY_REPORT_SCHEMA,
            plugins=tuple(
                MemoryPluginReport(
                    plugin_id=plugin.plugin_id,
                    state=plugin.plugin_status(),
                )
                for plugin in self.plugins
            ),
            evidence_publisher=self.evidence_publisher,
        ).to_dict()

    def plugin_report(self) -> dict[str, Any]:
        """Report catalog, requested, and published plugins for applied instances."""

        with self._runtime_lock:
            return self._plugin_report()

    def _plugin_report(self) -> dict[str, Any]:
        records = [
            {
                "plugin_id": plugin.plugin_id,
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
            return {
                "activation": (
                    str(self.activation.source_path)
                    if self.activation and self.activation.source_path
                    else None
                ),
                "available_plugins": sorted(self.plugin_manager.available_ids),
                "selected_plugin_ids": list(self.plugin_manager.selected_ids),
                "plugin_ids": list(self.plugin_ids),
                "plugins": [plugin.status() for plugin in self.plugins],
                "evidence_publisher": self.evidence_publisher,
                "plugin_report": self._plugin_report(),
                "update_count": self.update_count,
                "reset_count": self.reset_count,
                "failure_count": self.failure_count,
                "last_duration_ms": self.last_duration_ms,
                "last_error": self.last_error,
            }


def _evidence(shared_memory: SharedMemory | None) -> object:
    """The value at ``EVIDENCE_KEY``, or ``_ABSENT``."""

    if shared_memory is None:
        return _ABSENT
    return shared_memory.get(EVIDENCE_KEY, _ABSENT)


def _validate_plugin(plugin: Any, definition: PluginDefinition) -> None:
    if not isinstance(plugin, MemoryPlugin):
        raise TypeError(f"memory plugin {definition.entrypoint} does not satisfy MemoryPlugin")
    require_plugin_id(plugin, definition)


def _diagnostic(exc: BaseException) -> str:
    return _truncate_text(format_exception_safely(exc), DEFAULT_MAX_DIAGNOSTIC_CHARS)


def format_exception_safely(exc: BaseException) -> str:
    """Format an exception without letting ``__str__`` bypass isolation."""

    type_name = type(exc).__name__
    try:
        detail = str(exc)
    except Exception:  # noqa: BLE001 - secondary failure must not escape
        return f"{type_name}: <unprintable exception>"
    return f"{type_name}: {detail}"


def _truncate_text(value: str, max_chars: int) -> str:
    text = str(value)
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."
