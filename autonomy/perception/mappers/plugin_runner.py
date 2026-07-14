from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

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


ComponentProvider = Callable[[PerceptionRequest, PerceptionPluginInput], Any]


@dataclass(frozen=True)
class _PluginExecution:
    status: PluginResultStatus
    batch: PerceptionEvidenceBatch
    artifacts: dict[str, str]
    duration_ms: float
    error: str | None = None


class PluginPerceptionMapper:
    """Generic runner that injects inputs and owns plugin execution mechanics."""

    plugin_id = "autonomy.perception.plugin-runner-v0"

    def __init__(
        self,
        *,
        plugins: list[str] | tuple[str, ...] | None = None,
        plugin_specs: dict[str, str] | None = None,
        plugin_configs: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        specs = dict(plugin_specs or {})
        configs = {
            plugin_id: dict(config)
            for plugin_id, config in (plugin_configs or {}).items()
        }
        plugin_ids = tuple(() if plugins is None else plugins)
        if len(plugin_ids) != len(set(plugin_ids)):
            raise ValueError("configured perception plugin ids must be unique")
        unknown = [plugin_id for plugin_id in plugin_ids if plugin_id not in specs]
        if unknown:
            available = ", ".join(sorted(specs))
            raise ValueError(f"Unknown perception plugin(s): {unknown}. Available: {available}.")
        self.plugin_specs = specs
        self.plugin_configs = configs
        self.plugin_ids = plugin_ids
        self.plugins = tuple(
            _instantiate_plugin(
                plugin_id,
                self.plugin_specs[plugin_id],
                self.plugin_configs.get(plugin_id, {}),
            )
            for plugin_id in self.plugin_ids
        )
        runtime_ids = [plugin.plugin_id for plugin in self.plugins]
        if len(runtime_ids) != len(set(runtime_ids)):
            raise ValueError("perception plugin runtime ids must be unique")
        self._component_providers: dict[str, ComponentProvider] = {}
        self._component_provider_specs: dict[str, str] = {}
        for plugin in self.plugins:
            for item in plugin.contract.inputs:
                existing = self._component_provider_specs.get(item.component_id)
                if existing is not None and existing != item.provider_spec:
                    raise ValueError(
                        f"component {item.component_id!r} declares conflicting providers: "
                        f"{existing!r} and {item.provider_spec!r}"
                    )
                self._component_provider_specs[item.component_id] = item.provider_spec
        for provider_spec in sorted(set(self._component_provider_specs.values())):
            self._component_provider(provider_spec)

    def reset(self) -> None:
        for plugin in self.plugins:
            _reset_plugin(plugin)

    def describe_schema(self) -> dict[str, Any]:
        component_consumers: dict[str, list[str]] = {}
        component_providers: dict[str, str] = {}
        plugin_schemas = []
        for configured_id, plugin in zip(self.plugin_ids, self.plugins, strict=True):
            contract = plugin.contract
            for item in contract.inputs:
                component_consumers.setdefault(item.component_id, []).append(plugin.plugin_id)
                component_providers[item.component_id] = item.provider_spec
            plugin_schemas.append(
                {
                    "plugin_id": plugin.plugin_id,
                    "spec": self.plugin_specs[configured_id],
                    "config": dict(self.plugin_configs.get(configured_id, {})),
                    "contract": contract.to_dict(),
                }
            )
        return {
            "schema": "perception_algorithm_schema_v2",
            "plugin_id": self.plugin_id,
            "mapper": f"{self.__class__.__module__}:{self.__class__.__name__}",
            "configuration": {
                "plugins": list(self.plugin_ids),
                "available_plugins": sorted(self.plugin_specs),
                "plugin_specs": dict(self.plugin_specs),
                "plugin_configs": dict(self.plugin_configs),
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

        for plugin in self.plugins:
            execution = self._execute_plugin(plugin, request)
            attributed_signals = tuple(
                replace(signal, source_plugin_id=plugin.plugin_id)
                for signal in execution.batch.signals
            )
            attributed_things = tuple(
                replace(thing, source_plugin_id=plugin.plugin_id)
                for thing in execution.batch.things
            )
            plugin_runs.append(
                PerceptionPluginRun(
                    plugin_id=plugin.plugin_id,
                    status=execution.status,
                    duration_ms=execution.duration_ms,
                    signal_count=len(attributed_signals),
                    thing_count=len(attributed_things),
                    artifact_count=len(execution.artifacts),
                    error=execution.error,
                )
            )
            lines.append(
                f"plugin_run id={plugin.plugin_id} status={execution.status} "
                f"duration_ms={execution.duration_ms:.3f} "
                f"signals={len(attributed_signals)} things={len(attributed_things)} "
                f"artifacts={len(execution.artifacts)}"
            )
            if execution.error:
                lines.append(
                    f"plugin_status id={plugin.plugin_id} status={execution.status} "
                    f"detail={_line_value(execution.error)}"
                )
            lines.extend(signal_line(signal) for signal in attributed_signals)
            lines.extend(thing_line(thing) for thing in attributed_things)
            signals.extend(attributed_signals)
            things.extend(attributed_things)
            if execution.batch.measurements:
                measurements[plugin.plugin_id] = dict(execution.batch.measurements)
            artifacts.update(
                {
                    f"{plugin.plugin_id}/{artifact_id}": path
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

    def _execute_plugin(self, plugin: Any, request: PerceptionRequest) -> _PluginExecution:
        started = time.perf_counter()
        diagnostics = PerceptionDiagnosticSink(
            output_dir=request.output_dir,
            plugin_id=plugin.plugin_id,
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
                    _reset_plugin(plugin)
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
            )
            batch = plugin.perceive(inputs)
            if not isinstance(batch, PerceptionEvidenceBatch):
                raise TypeError(
                    f"plugin {plugin.plugin_id!r} must return PerceptionEvidenceBatch"
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


def _reset_plugin(plugin: Any) -> None:
    reset = getattr(plugin, "reset", None)
    if callable(reset):
        reset()


def _line_value(value: str) -> str:
    return value.replace("\n", " ").replace(" ", "_")
