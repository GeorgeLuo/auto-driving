from __future__ import annotations

import importlib
import time
from typing import Any

from autonomy.perception.interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceivedThing,
    PerceptionPluginContract,
    PerceptionPluginResult,
    PerceptionPluginRun,
    PerceptionRequest,
    PerceptionText,
)


class PluginChainPerceptionMapper:
    """Generic runner for independently configured perception plugins."""

    plugin_id = "autonomy.perception.plugin-chain-v0"

    def __init__(
        self,
        *,
        plugins: list[str] | tuple[str, ...] | None = None,
        plugin_specs: dict[str, str] | None = None,
        plugin_configs: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        specs = dict(plugin_specs or {})
        configs = {plugin_id: dict(config) for plugin_id, config in (plugin_configs or {}).items()}
        plugin_ids = tuple(() if plugins is None else plugins)
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

    def reset(self) -> None:
        for plugin in self.plugins:
            plugin.reset()

    def describe_schema(self) -> dict[str, Any]:
        component_consumers: dict[str, list[str]] = {}
        plugin_schemas = []
        for plugin in self.plugins:
            for component_id in plugin.contract.required_components:
                component_consumers.setdefault(component_id, []).append(plugin.plugin_id)
            plugin_schemas.append({
                **plugin.describe_schema(),
                "contract": plugin.contract.to_dict(),
            })
        return {
            "schema": "perception_algorithm_schema_v0",
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
                    "required": True,
                    "required_by": plugin_ids,
                    "source": "resolved by the requesting plugin from PerceptionRequest.snapshot",
                    "missing_behavior": "requesting plugin reports unavailable or reduced confidence",
                }
                for component_id, plugin_ids in sorted(component_consumers.items())
            ],
            "plugin_chain": plugin_schemas,
            "output": {
                "schema": PERCEPTION_TEXT_SCHEMA,
                "format": "line-oriented debug signals, structured PerceivedThing records, and plugin run status",
                "records": [
                    {
                        "record": "signal id=*",
                        "meaning": "plugin-specific boolean or scalar observation",
                    },
                    {
                        "record": "thing id=*",
                        "meaning": "structured thing/surface/motion evidence with normalized image or topdown_fov location",
                    },
                    {
                        "record": "artifact id=*",
                        "meaning": "plugin-produced diagnostic or downstream-processing file path",
                    },
                ],
                "limits": [
                    "plugin outputs are evidence, not final world facts",
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
        things: list[PerceivedThing] = []
        observations: dict[str, Any] = {
            "sensor_snapshot": request.snapshot.to_dict(),
            "plugin_chain": list(self.plugin_ids),
        }
        artifacts: dict[str, str] = {}
        limits: list[str] = []
        plugin_runs: list[PerceptionPluginRun] = []

        for plugin in self.plugins:
            started = time.perf_counter()
            try:
                result = plugin.perceive(request)
                if not isinstance(result, PerceptionPluginResult):
                    raise TypeError(
                        f"plugin {plugin.plugin_id!r} must return PerceptionPluginResult"
                    )
            except Exception as exc:
                result = PerceptionPluginResult(
                    status="error",
                    lines=(
                        f"signal id=plugin_error plugin={plugin.plugin_id} "
                        f"error={type(exc).__name__} confidence=1.000",
                    ),
                    observations={
                        plugin.plugin_id: {
                            "status": "error",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    },
                    limits=(f"plugin {plugin.plugin_id} failed",),
                    error=f"{type(exc).__name__}: {exc}",
                )
            duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
            plugin_runs.append(
                PerceptionPluginRun(
                    plugin_id=plugin.plugin_id,
                    status=result.status,
                    duration_ms=duration_ms,
                    thing_count=len(result.things),
                    artifact_count=len(result.artifacts),
                    error=result.error,
                )
            )
            lines.append(
                f"plugin_run id={plugin.plugin_id} status={result.status} "
                f"duration_ms={duration_ms:.3f} things={len(result.things)} "
                f"artifacts={len(result.artifacts)}"
            )
            lines.extend(result.lines)
            things.extend(result.things)
            observations.update(result.observations)
            artifacts.update(result.artifacts)
            limits.extend(result.limits)

        observations["component_access"] = request.component_summary()

        return PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id=self.plugin_id,
            status=_overall_status(plugin_runs),
            lines=tuple(lines),
            things=tuple(things),
            confidence=_overall_confidence(things),
            plugin_runs=tuple(plugin_runs),
            observations=observations,
            artifacts=artifacts,
            limits=tuple(dict.fromkeys(limits)),
        )


def _overall_confidence(things: list[PerceivedThing]) -> float:
    signal_things = [
        thing
        for thing in things
        if thing.kind not in {"sensor_frame", "prepared_sensor_frame"}
    ]
    if signal_things:
        return round(float(sum(thing.confidence for thing in signal_things) / len(signal_things)), 5)
    return 0.0


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


def _instantiate_plugin(plugin_id: str, spec: str, config: dict[str, Any]):
    module_name, separator, class_name = spec.partition(":")
    if not separator:
        raise ValueError(f"Plugin spec for {plugin_id!r} must be 'module.path:ClassName'")
    module = importlib.import_module(module_name)
    plugin_cls = getattr(module, class_name)
    plugin = plugin_cls(**config)
    _validate_plugin(plugin_id, plugin)
    return plugin


def _validate_plugin(configured_id: str, plugin: Any) -> None:
    plugin_id = getattr(plugin, "plugin_id", None)
    if not isinstance(plugin_id, str) or not plugin_id:
        raise TypeError(f"configured plugin {configured_id!r} must expose a non-empty plugin_id")
    if not isinstance(getattr(plugin, "contract", None), PerceptionPluginContract):
        raise TypeError(f"plugin {plugin_id!r} must expose PerceptionPluginContract as contract")
    for method_name in ("reset", "describe_schema", "perceive"):
        if not callable(getattr(plugin, method_name, None)):
            raise TypeError(f"plugin {plugin_id!r} must implement {method_name}()")
