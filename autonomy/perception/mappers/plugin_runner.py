from __future__ import annotations

import importlib
import time
from copy import deepcopy
from dataclasses import dataclass, replace
from threading import RLock
from typing import Any, Callable

from autonomy.plugins import PluginManager, PluginSelectionRuntime
from autonomy.perception.evidence import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionSignal,
)
from autonomy.perception.interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionPluginRun,
    PerceptionRequest,
    PerceptionText,
    PluginResultStatus,
)
from autonomy.perception.plugin import (
    PerceptionDiagnosticSink,
    PerceptionPluginContract,
    PerceptionPluginInput,
    PerceptionPluginInputs,
    PerceptionPluginWarmingUp,
)
from autonomy.perception.rendering import signal_line, thing_line
from autonomy.perception.selection import perception_plugin_manager


ComponentProvider = Callable[[PerceptionRequest, PerceptionPluginInput], Any]


@dataclass(frozen=True)
class _PluginExecution:
    status: PluginResultStatus
    batch: PerceptionEvidenceBatch
    artifacts: dict[str, str]
    duration_ms: float
    error: str | None = None


class PluginPerceptionMapper:
    """Run the manager's selected perception plugins once per sensor frame."""

    plugin_id = "autonomy.perception.plugin-runner-v0"

    def __init__(
        self,
        *,
        plugins: list[str] | tuple[str, ...] | None = None,
        plugin_specs: dict[str, str] | None = None,
        plugin_configs: dict[str, dict[str, Any]] | None = None,
        plugin_manager: PluginManager | None = None,
    ) -> None:
        specs = dict(plugin_specs or {})
        configs = {
            plugin_id: dict(config)
            for plugin_id, config in (plugin_configs or {}).items()
        }
        plugin_ids = tuple(() if plugins is None else plugins)
        if len(plugin_ids) != len(set(plugin_ids)):
            raise ValueError("configured perception plugin ids must be unique")
        if plugin_manager is None:
            plugin_manager = perception_plugin_manager(specs, configs)
            plugin_manager.select(plugin_ids)
        elif plugins is not None:
            raise ValueError(
                "select plugins through plugin_manager when one is provided"
            )
        if not isinstance(plugin_manager, PluginManager):
            raise TypeError("plugin_manager must be a PluginManager")
        if plugin_manager.step != "perception":
            raise ValueError(
                f"plugin_manager is scoped to {plugin_manager.step!r}, not 'perception'"
            )

        self.plugin_manager = plugin_manager
        self._selection_runtime = PluginSelectionRuntime(plugin_manager)
        self._runtime_lock = RLock()
        self.plugin_ids: tuple[str, ...] = ()
        self.plugins: tuple[Any, ...] = ()
        self._component_providers: dict[str, ComponentProvider] = {}
        self._component_provider_specs: dict[str, str] = {}
        self._apply_selection()

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
            for plugin in self.plugins:
                _reset_plugin(plugin, shared_memory)

    def describe_schema(self) -> dict[str, Any]:
        with self._runtime_lock:
            return self._describe_schema()

    def _describe_schema(self) -> dict[str, Any]:
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
            "mapper": f"{self.__class__.__module__}:{self.__class__.__name__}",
            "configuration": {
                "plugins": list(self.plugin_ids),
                "available_plugins": sorted(item.plugin_id for item in available),
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
                        "meaning": "structured boolean or scalar observations",
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

    def _apply_selection(
        self,
        shared_memory=None,
    ) -> None:
        candidate_provider_specs: dict[str, str] | None = None
        candidate_providers: dict[str, ComponentProvider] | None = None

        def validate(candidate_plugins: tuple[Any, ...]) -> None:
            nonlocal candidate_provider_specs, candidate_providers
            candidate_provider_specs = {}
            candidate_providers = {}
            implementation_ids = [plugin.plugin_id for plugin in candidate_plugins]
            if len(implementation_ids) != len(set(implementation_ids)):
                raise ValueError(
                    "perception plugin implementation ids must be unique"
                )

            for plugin in candidate_plugins:
                for item in plugin.contract.inputs:
                    existing = candidate_provider_specs.get(item.component_id)
                    if existing is not None and existing != item.provider_spec:
                        raise ValueError(
                            f"component {item.component_id!r} declares conflicting providers: "
                            f"{existing!r} and {item.provider_spec!r}"
                        )
                    candidate_provider_specs[item.component_id] = item.provider_spec

            for provider_spec in sorted(set(candidate_provider_specs.values())):
                provider = self._component_providers.get(provider_spec)
                if provider is None:
                    provider = _load_symbol(provider_spec)
                if not callable(provider):
                    raise TypeError(
                        f"component provider {provider_spec!r} is not callable"
                    )
                candidate_providers[provider_spec] = provider

        applied = self._selection_runtime.apply(
            load=lambda definition: _instantiate_plugin(
                definition.plugin_id,
                definition.entrypoint,
                dict(definition.config),
            ),
            validate=validate,
            reset=lambda plugin: _reset_plugin(plugin, shared_memory),
        )
        if candidate_provider_specs is None or candidate_providers is None:
            return

        selected = tuple(definition for definition, _plugin in applied)
        candidate_plugins = tuple(plugin for _definition, plugin in applied)

        # Publish only after all newly selected plugins and their providers
        # have been constructed and validated.
        self.plugin_ids = tuple(definition.plugin_id for definition in selected)
        self.plugins = candidate_plugins
        self._component_provider_specs = candidate_provider_specs
        self._component_providers = candidate_providers

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


def _instantiate_plugin(plugin_id: str, spec: str, config: dict[str, Any]) -> Any:
    plugin_cls = _load_symbol(spec)
    plugin = plugin_cls(**config)
    _validate_plugin(plugin_id, plugin)
    return plugin


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
