from __future__ import annotations

from typing import Any
import importlib

from autonomy.perception.interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceivedThing,
    PerceptionRequest,
    PerceptionText,
)
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID


class CurrentDirectoryPerceptionMapper:
    """Perception mapper backed by a small ordered plugin chain."""

    plugin_id = "autonomy.perception.current-directory-v0"

    def __init__(
        self,
        *,
        plugins: list[str] | tuple[str, ...] | None = None,
        plugin_specs: dict[str, str] | None = None,
    ) -> None:
        specs = dict(plugin_specs or {})
        plugin_ids = tuple(() if plugins is None else plugins)
        unknown = [plugin_id for plugin_id in plugin_ids if plugin_id not in specs]
        if unknown:
            available = ", ".join(sorted(specs))
            raise ValueError(f"Unknown perception plugin(s): {unknown}. Available: {available}.")
        self.plugin_specs = specs
        self.plugin_ids = plugin_ids
        self.plugins = tuple(_instantiate_plugin(plugin_id, self.plugin_specs[plugin_id]) for plugin_id in self.plugin_ids)

    def describe_schema(self) -> dict[str, Any]:
        return {
            "schema": "perception_algorithm_schema_v0",
            "plugin_id": self.plugin_id,
            "mapper": f"{self.__class__.__module__}:{self.__class__.__name__}",
            "configuration": {
                "plugins": list(self.plugin_ids),
                "available_plugins": sorted(self.plugin_specs),
                "plugin_specs": dict(self.plugin_specs),
            },
            "inputs": [
                {
                    "sensor_id": FRONT_CAMERA_SENSOR_ID,
                    "sensor_kind": "camera",
                    "required": True,
                    "source": "PerceptionRequest.snapshot.readings[front_camera].path",
                    "missing_behavior": "plugins emit false/missing signals and confidence drops",
                    "plugin_chain": [
                        plugin.describe_schema()
                        for plugin in self.plugins
                        if callable(getattr(plugin, "describe_schema", None))
                    ],
                }
            ],
            "output": {
                "schema": PERCEPTION_TEXT_SCHEMA,
                "format": "line-oriented debug signals plus structured PerceivedThing records",
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

        for plugin in self.plugins:
            result = plugin.perceive(request)
            lines.extend(result.lines)
            things.extend(result.things)
            observations.update(result.observations)
            artifacts.update(result.artifacts)
            limits.extend(result.limits)

        return PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id=self.plugin_id,
            lines=tuple(lines),
            things=tuple(things),
            confidence=_overall_confidence(things),
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


def _instantiate_plugin(plugin_id: str, spec: str):
    module_name, separator, class_name = spec.partition(":")
    if not separator:
        raise ValueError(f"Plugin spec for {plugin_id!r} must be 'module.path:ClassName'")
    module = importlib.import_module(module_name)
    plugin_cls = getattr(module, class_name)
    return plugin_cls()
