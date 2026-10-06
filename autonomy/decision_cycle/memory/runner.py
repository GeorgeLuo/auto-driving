"""Memory step runner.

``MemoryRunner`` is a ``StepRunner`` whose ``load_plugin`` wraps each
selected plugin in a ``MemoryPluginRuntime``; it runs them in selection order.
Its return value is the ``MemoryReport`` dict from ``interface``: each
plugin's own state summary for diagnostics, and the evidence publisher, the
plugin whose value ``EVIDENCE_KEY`` holds. Decisions read plugin-published
keys in the host map, not the report.

``MemoryPluginRuntime`` times and reports one applied plugin. It sits here, in
the runner's file, as perception keeps its per-plugin execution in its own.
Update and reset failures follow ``FAILURE_POLICY`` in ``interface``.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.interface import (
    LEDGER_SUMMARY_KEYS,
    MEMORY_REPORT_SCHEMA,
    FAILURE_POLICY,
    MEMORY_SCHEMA,
    MemoryPluginReport,
    MemoryReport,
    composition_declaration,
)
from autonomy.decision_cycle.memory.plugin import MemoryPlugin, plugin_status
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.runner import StepRunner, describe_configuration, describe_plugin
from autonomy.plugins import PluginDefinition, PluginManager
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
            if FAILURE_POLICY.update == "stop_cycle":
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
            if FAILURE_POLICY.reset == "propagate":
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


class MemoryRunner(StepRunner[MemoryPluginRuntime]):
    """Run the manager's selected memory plugins once per decision cycle.

    This runner is the step's ``MemoryBackend``. Each applied plugin is held
    in a ``MemoryPluginRuntime`` that times and reports it.

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

    step: ClassVar[str] = "memory"
    plugin_id: ClassVar[str] = "autonomy.memory.plugin-runner-v0"

    def __init__(
        self,
        plugin_manager: PluginManager,
        *,
        provided: Mapping[str, MemoryPlugin] | None = None,
    ) -> None:
        self.last_duration_ms: float | None = None
        self.reset_count = 0
        # The map the plugins last worked in, and the plugin whose value
        # EVIDENCE_KEY held after the last change the runner saw.
        self._shared_memory: SharedMemory | None = None
        self._publisher: str | None = None
        self._published_evidence: object = _ABSENT
        super().__init__(plugin_manager, provided=provided)

    @property
    def update_count(self) -> int:
        """Memory's name for ``run_count``, which its status also reports."""

        return self.run_count

    # Selection hooks ------------------------------------------------------

    def load_plugin(self, definition: PluginDefinition) -> MemoryPluginRuntime:
        plugin = super().load_plugin(definition)
        source_path = self.activation.source_path if self.activation else None
        return MemoryPluginRuntime(definition, plugin, source_path=source_path)

    def validate_plugin(self, plugin: Any, definition: PluginDefinition) -> None:
        if not isinstance(plugin, MemoryPlugin):
            raise TypeError(f"memory plugin {definition.entrypoint} does not satisfy MemoryPlugin")

    def _reset_plugin(
        self, plugin: MemoryPluginRuntime, shared_memory: SharedMemory | None
    ) -> None:
        plugin.reset(shared_memory)

    def _commit_selection(self, shared_memory: SharedMemory | None = None) -> None:
        super()._commit_selection(shared_memory)
        if shared_memory is not None:
            self._shared_memory = shared_memory
        if self._publisher not in self.plugin_ids:
            self._publisher = None
            self._published_evidence = _ABSENT

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
            self.apply_selection(context.shared_memory)
            started = time.perf_counter()
            self.last_error = None
            try:
                for plugin in self.plugins.values():
                    before = _evidence(self._shared_memory)
                    try:
                        # A missing observation still reaches the plugin when
                        # the declared policy is ``invoke``.
                        if observation is not None or FAILURE_POLICY.missing_input == "invoke":
                            plugin.update(memory_context, observation)
                    finally:
                        self._note_evidence(plugin.plugin_id, before)
            except Exception as exc:
                self.failure_count += 1
                self.last_error = _diagnostic(exc)
                raise
            finally:
                self.run_count += 1
                self.last_duration_ms = (time.perf_counter() - started) * 1000.0
            return self._report()

    def reset(self, shared_memory: SharedMemory | None = None) -> dict[str, Any]:
        """Reset each plugin and return the keys the plugins wrote while resetting.

        Memory overrides ``StepRunner.reset`` to count resets, record each
        plugin's reset failure by ``FAILURE_POLICY.reset``, and track the
        evidence publisher. A host that clears its map at a reset restores the
        returned keys so plugins keep the fresh state (for example a new
        epoch) they started. A plugin whose reset leaves a new value at
        ``EVIDENCE_KEY`` becomes the evidence publisher, as in an update; a
        key the host drops leaves none.
        """

        with self._runtime_lock:
            started = time.perf_counter()
            self.last_error = None
            if shared_memory is not None:
                self._shared_memory = shared_memory
            before = dict(shared_memory) if shared_memory is not None else {}
            for plugin in self.plugins.values():
                failures = plugin.failure_count
                evidence = _evidence(self._shared_memory)
                self._reset_plugin(plugin, shared_memory)
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
        applied = self.applied
        return {
            "schema": MEMORY_SCHEMA,
            "plugin_id": self.plugin_id,
            "runner": f"{type(self).__module__}:{type(self).__name__}",
            "configuration": describe_configuration(self.plugin_manager, applied),
            "inputs": [
                {
                    "name": "observation",
                    "required": False,
                    "source": "the cycle's observation, written by perception",
                    "missing_behavior": (
                        "the plugin is still called with observation None"
                        if FAILURE_POLICY.missing_input == "invoke"
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
            "plugins": [describe_plugin(definition) for definition, _plugin in applied],
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
            "failure_policy": FAILURE_POLICY.to_dict(),
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
                for plugin in self.plugins.values()
            ),
            evidence_publisher=self.evidence_publisher,
        ).to_dict()

    def _plugin_records(self) -> list[dict[str, Any]]:
        return [
            {
                "plugin_id": plugin.plugin_id,
                "duration_ms": plugin.last_duration_ms,
                "error": plugin.last_error,
            }
            for plugin in self.plugins.values()
        ]

    def status(self) -> dict[str, Any]:
        with self._runtime_lock:
            return {
                **super().status(),
                "plugins": [plugin.status() for plugin in self.plugins.values()],
                "evidence_publisher": self.evidence_publisher,
                # Live memory readers, including the CLI, read update_count.
                "update_count": self.update_count,
                "reset_count": self.reset_count,
                "last_duration_ms": self.last_duration_ms,
            }


def _evidence(shared_memory: SharedMemory | None) -> object:
    """The value at ``EVIDENCE_KEY``, or ``_ABSENT``."""

    if shared_memory is None:
        return _ABSENT
    return shared_memory.get(EVIDENCE_KEY, _ABSENT)


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
