"""Perception step runner.

``PerceptionRunner`` resolves the component inputs each selected perception
plugin's contract declares, runs the plugins in selection order on one sensor
frame, and merges their evidence into one ``PerceptionText``.
"""

from __future__ import annotations

import importlib
import time
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock
from typing import Any

from autonomy.decision_cycle.activation import StepActivation, step_activation
from autonomy.decision_cycle.perception.inputs import build_perception_request
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
)
from autonomy.decision_cycle.perception.components.context import PerceptionRequest
from autonomy.decision_cycle.perception.components.interface import ComponentProvider
from autonomy.decision_cycle.perception.diagnostics.sink import PerceptionDiagnosticSink
from autonomy.decision_cycle.perception.evidence.rendering import signal_line, thing_line
from autonomy.decision_cycle.perception.evidence.values import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionSignal,
)
from autonomy.decision_cycle.perception.interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionPluginRun,
    PerceptionText,
    PluginResultStatus,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginContract,
    PerceptionPluginInputs,
    PerceptionPluginWarmingUp,
)


# ``context.metadata`` key naming the directory a recording host wants
# perception diagnostics written to for the frame.
PERCEPTION_OUTPUT_DIR_KEY = "perception_output_dir"


@dataclass(frozen=True)
class _PluginExecution:
    status: PluginResultStatus
    batch: PerceptionEvidenceBatch
    artifacts: dict[str, str]
    duration_ms: float
    error: str | None = None


class PerceptionRunner:
    """Run the manager's selected perception plugins once per sensor frame.

    The cycle calls the runner with the frame context. ``perceive`` runs the
    selection on an already built ``PerceptionRequest`` for offline tools.
    """

    step = "perception"
    plugin_id = "autonomy.perception.plugin-runner-v0"

    def __init__(
        self,
        plugin_manager: PluginManager,
        *,
        provided: dict[str, Any] | None = None,
    ) -> None:
        self.plugin_manager = require_step_manager(plugin_manager, self.step)
        self.activation: StepActivation | None = None
        self._provided = dict(provided or {})
        self._selection_runtime = PluginSelectionRuntime(plugin_manager)
        self._runtime_lock = RLock()
        self.plugin_ids: tuple[str, ...] = ()
        self.plugins: tuple[Any, ...] = ()
        self.last_output: PerceptionText | None = None
        self.last_duration_ms: float | None = None
        self.last_frame_index: int | None = None
        self._component_providers: dict[str, ComponentProvider] = {}
        self._component_provider_specs: dict[str, str] = {}
        self._pending_providers: dict[str, ComponentProvider] | None = None
        self._pending_provider_specs: dict[str, str] | None = None
        self._execution_runs: tuple[PerceptionPluginRun, ...] = ()
        self._apply_selection()

    @classmethod
    def from_activation(cls, activation: StepActivation) -> "PerceptionRunner":
        runner = cls(require_step_activation(activation, cls.step).plugin_manager())
        runner.activation = activation
        return runner

    @classmethod
    def from_selection(
        cls,
        plugins: list[str] | tuple[str, ...],
        plugin_specs: dict[str, str],
        plugin_configs: dict[str, dict[str, Any]] | None = None,
    ) -> "PerceptionRunner":
        return cls.from_activation(
            step_activation(cls.step, plugins, plugin_specs, plugin_configs)
        )

    @classmethod
    def from_plugins(cls, plugins: dict[str, Any]) -> "PerceptionRunner":
        """Run already constructed plugins, selected in the mapping's order."""

        manager = PluginManager.from_specs(
            cls.step, {plugin_id: f"{PROVIDED_ENTRYPOINT}:{plugin_id}" for plugin_id in plugins}
        )
        manager.select(tuple(plugins))
        return cls(manager, provided=plugins)

    def __call__(self, context) -> PerceptionText | None:
        """Perceive the context's sensor frame; without one, reset and return None."""

        with self._runtime_lock:
            if context.sensor_snapshot is None:
                self.reset(context.shared_memory)
                self.last_frame_index = context.frame_index
                return None
            started = time.perf_counter()
            metadata = context.metadata if isinstance(context.metadata, dict) else {}
            # A host that records diagnostics names the directory per frame.
            output_dir = metadata.get(PERCEPTION_OUTPUT_DIR_KEY)
            try:
                self.last_output = self.perceive(
                    build_perception_request(
                        context.sensor_snapshot,
                        shared_memory=context.shared_memory,
                        output_dir=Path(output_dir) if isinstance(output_dir, str) else None,
                        metadata={
                            "runtime": "onboard",
                            "activation": (
                                str(self.activation.source_path)
                                if self.activation and self.activation.source_path
                                else None
                            ),
                            "frame_index": context.frame_index,
                        },
                    )
                )
            finally:
                self.last_duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
                self.last_frame_index = context.frame_index
            return self.last_output

    def status(self) -> dict[str, Any]:
        with self._runtime_lock:
            output = self.last_output
            return {
                "step": self.step,
                "activation": (
                    str(self.activation.source_path)
                    if self.activation and self.activation.source_path
                    else None
                ),
                "available_plugins": sorted(self.plugin_manager.available_ids),
                "selected_plugin_ids": list(self.plugin_manager.selected_ids),
                "plugin_ids": list(self.plugin_ids),
                "last_status": output.status if output is not None else None,
                "last_duration_ms": self.last_duration_ms,
                "last_frame_index": self.last_frame_index,
                "last_thing_count": len(output.things) if output is not None else 0,
                "last_plugin_runs": (
                    [plugin_run.to_dict() for plugin_run in output.plugin_runs]
                    if output is not None
                    else []
                ),
                "plugin_report": self._plugin_report(),
            }

    @property
    def plugin_specs(self) -> dict[str, str]:
        return {item.plugin_id: item.entrypoint for item in self.plugin_manager.available}

    @property
    def plugin_configs(self) -> dict[str, dict[str, Any]]:
        return {
            item.plugin_id: deepcopy(dict(item.config))
            for item in self.plugin_manager.available
        }

    def reset(self, shared_memory=None) -> None:
        with self._runtime_lock:
            self._execution_runs = ()
            self.last_output = None
            self.last_duration_ms = None
            self.last_frame_index = None
            for plugin in self.plugins:
                _reset_plugin(plugin, shared_memory)

    def describe_schema(self) -> dict[str, Any]:
        with self._runtime_lock:
            return self._describe_schema()

    def plugin_report(self) -> dict[str, Any]:
        """Report catalog, requested selection, and the last published execution.

        Timing and error come from that execution. They stay null before the
        first run and after reset. Domain run fields stay on the perception
        result, not in this envelope.
        """

        with self._runtime_lock:
            return self._plugin_report()

    def _plugin_report(self) -> dict[str, Any]:
        runs = {run.plugin_id: run for run in self._execution_runs}
        records = []
        for definition, plugin in self._selection_runtime.applied:
            run = runs.get(definition.plugin_id)
            implementation_id = plugin.plugin_id
            duration_ms = None
            error = None
            if run is not None:
                if run.implementation_id:
                    implementation_id = run.implementation_id
                duration_ms = run.duration_ms
                error = run.error
            records.append(
                {
                    "plugin_id": definition.plugin_id,
                    "implementation_id": implementation_id,
                    "duration_ms": duration_ms,
                    "error": error,
                }
            )
        return build_plugin_report(
            self.plugin_manager,
            self._selection_runtime.applied,
            records,
        )

    def _describe_schema(self) -> dict[str, Any]:
        report = self._plugin_report()
        component_consumers: dict[str, list[str]] = {}
        component_providers: dict[str, str] = {}
        plugin_schemas = []
        available = self.plugin_manager.available
        for definition, plugin in self._selection_runtime.applied:
            contract = plugin.contract
            for item in contract.inputs:
                component_consumers.setdefault(item.component_id, []).append(
                    definition.plugin_id
                )
                component_providers[item.component_id] = item.provider_spec
            plugin_schemas.append(
                {
                    "plugin_id": definition.plugin_id,
                    "implementation_id": plugin.plugin_id,
                    "spec": definition.entrypoint,
                    "config": deepcopy(dict(definition.config)),
                    "contract": contract.to_dict(),
                }
            )
        return {
            "schema": "perception_algorithm_schema_v2",
            "plugin_id": self.plugin_id,
            "runner": f"{self.__class__.__module__}:{self.__class__.__name__}",
            "configuration": {
                "plugins": list(self.plugin_ids),
                "available_plugins": sorted(item.plugin_id for item in available),
                "selected_plugin_ids": list(report["selected_plugin_ids"]),
                "applied_plugin_ids": list(report["applied_plugin_ids"]),
                "plugin_specs": {item.plugin_id: item.entrypoint for item in available},
                "plugin_configs": {
                    item.plugin_id: deepcopy(dict(item.config)) for item in available
                },
            },
            "inputs": [
                {
                    "component_id": component_id,
                    "provider_spec": component_providers[component_id],
                    "required": True,
                    "required_by": plugin_ids,
                    "source": "resolved once by the framework and injected by plugin-local name",
                    "missing_behavior": "framework marks the plugin unavailable without invoking it",
                }
                for component_id, plugin_ids in sorted(component_consumers.items())
            ],
            "plugins": plugin_schemas,
            "output": {
                "schema": PERCEPTION_TEXT_SCHEMA,
                "format": "structured signals and spatial evidence with a derived text view",
                "records": [
                    {
                        "record": "signals[]",
                        "meaning": "structured boolean or scalar evidence signals",
                    },
                    {
                        "record": "things[]",
                        "meaning": "structured spatial evidence with source-plugin provenance",
                    },
                    {
                        "record": "plugin_runs[]",
                        "meaning": "framework-derived status, timing, counts, and errors",
                    },
                ],
                "limits": [
                    "plugin outputs are current evidence, not durable world facts",
                    "confidence remains local to each evidence record",
                    "no calibrated metric geometry unless a plugin states otherwise",
                ],
            },
        }

    def perceive(self, request: PerceptionRequest) -> PerceptionText:
        with self._runtime_lock:
            # Core snapshots the manager's selection once for this sensor frame.
            self._apply_selection(request.shared_memory)
            return self._perceive_selected(request)

    def _perceive_selected(self, request: PerceptionRequest) -> PerceptionText:
        lines = [
            f"schema={PERCEPTION_TEXT_SCHEMA}",
            f"plugin={self.plugin_id}",
            f"plugins={','.join(self.plugin_ids)}",
        ]
        signals: list[PerceptionSignal] = []
        things: list[PerceivedThing] = []
        measurements: dict[str, dict[str, Any]] = {}
        artifacts: dict[str, str] = {}
        limits: list[str] = []
        plugin_runs: list[PerceptionPluginRun] = []

        # Catalog plugin_id attributes results. The instance plugin_id is the
        # implementation identity and is not rewritten to repair provenance.
        for definition, plugin in self._selection_runtime.applied:
            catalog_id = definition.plugin_id
            execution = self._execute_plugin(plugin, request, catalog_id)
            attributed_signals = tuple(
                replace(signal, source_plugin_id=catalog_id)
                for signal in execution.batch.signals
            )
            attributed_things = tuple(
                replace(thing, source_plugin_id=catalog_id)
                for thing in execution.batch.things
            )
            plugin_runs.append(
                PerceptionPluginRun(
                    plugin_id=catalog_id,
                    status=execution.status,
                    duration_ms=execution.duration_ms,
                    signal_count=len(attributed_signals),
                    thing_count=len(attributed_things),
                    artifact_count=len(execution.artifacts),
                    error=execution.error,
                    implementation_id=plugin.plugin_id,
                )
            )
            lines.append(
                f"plugin_run id={catalog_id} status={execution.status} "
                f"duration_ms={execution.duration_ms:.3f} "
                f"signals={len(attributed_signals)} things={len(attributed_things)} "
                f"artifacts={len(execution.artifacts)}"
            )
            if execution.error:
                lines.append(
                    f"plugin_status id={catalog_id} status={execution.status} "
                    f"detail={_line_value(execution.error)}"
                )
            lines.extend(signal_line(signal) for signal in attributed_signals)
            lines.extend(thing_line(thing) for thing in attributed_things)
            signals.extend(attributed_signals)
            things.extend(attributed_things)
            if execution.batch.measurements:
                measurements[catalog_id] = dict(execution.batch.measurements)
            artifacts.update(
                {
                    f"{catalog_id}/{artifact_id}": path
                    for artifact_id, path in execution.artifacts.items()
                }
            )
            limits.extend(plugin.contract.limitations)

        self._execution_runs = tuple(plugin_runs)
        return PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id=self.plugin_id,
            status=_overall_status(plugin_runs),
            lines=tuple(lines),
            signals=tuple(signals),
            things=tuple(things),
            plugin_runs=tuple(plugin_runs),
            measurements=measurements,
            artifacts=artifacts,
            limits=tuple(dict.fromkeys(limits)),
        )

    def prepare_selection(self) -> None:
        """Load and validate the manager selection without resetting or publishing."""

        with self._runtime_lock:
            self._prepare_selection()

    def commit_selection(self, shared_memory=None) -> None:
        """Reset removed plugins and publish the prepared selection."""

        with self._runtime_lock:
            self._commit_selection(shared_memory)

    def discard_selection(self) -> None:
        """Drop a prepared selection without resetting published plugins."""

        with self._runtime_lock:
            self._discard_selection()

    def _apply_selection(
        self,
        shared_memory=None,
    ) -> None:
        self._prepare_selection()
        self._commit_selection(shared_memory)

    def _prepare_selection(self) -> None:
        candidate_provider_specs: dict[str, str] | None = None
        candidate_providers: dict[str, ComponentProvider] | None = None

        def validate(candidate_plugins: tuple[Any, ...]) -> None:
            nonlocal candidate_provider_specs, candidate_providers
            candidate_provider_specs = {}
            candidate_providers = {}

            for plugin in candidate_plugins:
                for item in plugin.contract.inputs:
                    spec = item.provider_spec
                    if spec not in candidate_providers:
                        provider = self._component_providers.get(spec)
                        if provider is None:
                            provider = _load_symbol(spec)
                        if not callable(provider):
                            raise TypeError(f"component provider {spec!r} is not callable")
                        candidate_providers[spec] = provider
                    # Specs that name the same provider (a legacy and a
                    # canonical path) agree.
                    existing = candidate_provider_specs.get(item.component_id)
                    if existing is None:
                        candidate_provider_specs[item.component_id] = spec
                    elif candidate_providers[existing] is not candidate_providers[spec]:
                        raise ValueError(
                            f"component {item.component_id!r} declares conflicting providers: "
                            f"{existing!r} and {spec!r}"
                        )

        self._selection_runtime.prepare(load=self._load_plugin, validate=validate)
        # Retain providers with the prepared instances. Publish happens on commit,
        # and an unchanged selection leaves these unset so commit does not republish.
        self._pending_provider_specs = candidate_provider_specs
        self._pending_providers = candidate_providers

    def _commit_selection(self, shared_memory=None) -> None:
        specs = self._pending_provider_specs
        providers = self._pending_providers
        self._pending_provider_specs = None
        self._pending_providers = None
        applied = self._selection_runtime.commit(
            reset=lambda plugin: _reset_plugin(plugin, shared_memory),
        )
        if specs is None or providers is None:
            return

        # Publish only after prepared plugins and their providers have been validated.
        self.plugin_ids = tuple(definition.plugin_id for definition, _plugin in applied)
        self.plugins = tuple(plugin for _definition, plugin in applied)
        self._component_provider_specs = specs
        self._component_providers = providers

    def _load_plugin(self, definition: PluginDefinition) -> Any:
        if definition.entrypoint == f"{PROVIDED_ENTRYPOINT}:{definition.plugin_id}":
            plugin = self._provided[definition.plugin_id]
        else:
            plugin = instantiate_plugin(definition)
        _validate_plugin(definition.plugin_id, plugin)
        return plugin

    def _discard_selection(self) -> None:
        self._selection_runtime.discard()
        self._pending_provider_specs = None
        self._pending_providers = None

    def _execute_plugin(
        self,
        plugin: Any,
        request: PerceptionRequest,
        catalog_id: str,
    ) -> _PluginExecution:
        started = time.perf_counter()
        diagnostics = PerceptionDiagnosticSink(
            output_dir=request.output_dir,
            plugin_id=catalog_id,
            allowed_artifacts=plugin.contract.diagnostic_artifacts,
        )
        if plugin.contract.diagnostics_required and not diagnostics.enabled:
            return _execution(
                started,
                status="unavailable",
                error="diagnostics are required but recording is disabled",
            )

        try:
            components, missing = self._resolve_inputs(plugin.contract, request)
            if missing:
                if plugin.contract.state_mode != "stateless":
                    _reset_plugin(plugin, request.shared_memory)
                details = "; ".join(
                    f"{name}: {reason}" for name, reason in sorted(missing.items())
                )
                return _execution(
                    started,
                    status="unavailable",
                    error=f"required input unavailable: {details}",
                )
            inputs = PerceptionPluginInputs(
                frame_id=request.snapshot.read_id,
                captured_at_ms=request.snapshot.completed_at_ms,
                components=components,
                diagnostics=diagnostics,
                metadata=request.metadata,
                shared_memory=request.shared_memory,
            )
            batch = plugin.perceive(inputs)
            if not isinstance(batch, PerceptionEvidenceBatch):
                raise TypeError(
                    f"plugin {catalog_id!r} must return PerceptionEvidenceBatch"
                )
            status: PluginResultStatus = (
                "ok"
                if batch.signals or batch.things or diagnostics.artifacts
                else "empty"
            )
            return _execution(
                started,
                status=status,
                batch=batch,
                artifacts=diagnostics.artifacts,
            )
        except PerceptionPluginWarmingUp as exc:
            return _execution(
                started,
                status="warming_up",
                batch=PerceptionEvidenceBatch(measurements=exc.measurements),
                artifacts=diagnostics.artifacts,
                error=exc.reason,
            )
        except Exception as exc:
            return _execution(
                started,
                status="error",
                artifacts=diagnostics.artifacts,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _resolve_inputs(
        self,
        contract: PerceptionPluginContract,
        request: PerceptionRequest,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        components: dict[str, Any] = {}
        missing: dict[str, str] = {}
        for item in contract.inputs:
            provider = self._component_provider(item.provider_spec)
            component = request.resolve_component(
                item.component_id,
                lambda provider=provider, item=item: provider(request, item),
            )
            if component is None:
                missing[item.name] = (
                    request.component_error(item.component_id)
                    or "component provider returned no value"
                )
            else:
                components[item.name] = component
        return components, missing

    def _component_provider(self, spec: str) -> ComponentProvider:
        provider = self._component_providers.get(spec)
        if provider is not None:
            return provider
        provider = _load_symbol(spec)
        if not callable(provider):
            raise TypeError(f"component provider {spec!r} is not callable")
        self._component_providers[spec] = provider
        return provider


def _execution(
    started: float,
    *,
    status: PluginResultStatus,
    batch: PerceptionEvidenceBatch | None = None,
    artifacts: dict[str, str] | None = None,
    error: str | None = None,
) -> _PluginExecution:
    return _PluginExecution(
        status=status,
        batch=batch or PerceptionEvidenceBatch(),
        artifacts=dict(artifacts or {}),
        duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
        error=error,
    )


def _overall_status(plugin_runs: list[PerceptionPluginRun]) -> str:
    if not plugin_runs:
        return "empty"
    statuses = {run.status for run in plugin_runs}
    usable_statuses = {"ok", "empty", "warming_up"}
    if not statuses.intersection(usable_statuses):
        return "error" if "error" in statuses else "unavailable"
    if statuses.intersection({"error", "unavailable"}):
        return "partial"
    if "ok" in statuses:
        return "ok"
    if "warming_up" in statuses:
        return "warming_up"
    return "empty"


def _load_symbol(spec: str) -> Any:
    module_name, separator, name = spec.partition(":")
    if not separator or not module_name or not name:
        raise ValueError(f"import spec must be 'module.path:name', got {spec!r}")
    module = importlib.import_module(module_name)
    return getattr(module, name)


def _validate_plugin(configured_id: str, plugin: Any) -> None:
    plugin_id = getattr(plugin, "plugin_id", None)
    if not isinstance(plugin_id, str) or not plugin_id:
        raise TypeError(f"configured plugin {configured_id!r} must expose a non-empty plugin_id")
    if not isinstance(getattr(plugin, "contract", None), PerceptionPluginContract):
        raise TypeError(f"plugin {plugin_id!r} must expose PerceptionPluginContract as contract")
    if not callable(getattr(plugin, "perceive", None)):
        raise TypeError(f"plugin {plugin_id!r} must implement perceive()")


def _reset_plugin(plugin: Any, shared_memory=None) -> None:
    reset = getattr(plugin, "reset", None)
    if callable(reset):
        if plugin.contract.memory_required:
            reset(shared_memory)
        else:
            reset()


def _line_value(value: str) -> str:
    return value.replace("\n", " ").replace(" ", "_")
