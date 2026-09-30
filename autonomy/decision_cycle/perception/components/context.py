"""Request context that resolves shared plugin components.

A ``PerceptionRequest`` carries one sensor snapshot. Each component is resolved
once per request and shared by every plugin that declares it; a provider that
raises ``PerceptionComponentUnavailable`` or returns nothing records an error
for that component.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from autonomy.decision_cycle.perception.components.interface import PerceptionComponentUnavailable
from autonomy.shared_memory import SharedMemory
from autonomy.vehicle import SensorReading, SensorSnapshot


ComponentT = TypeVar("ComponentT")


@dataclass
class PerceptionRequest:
    """Framework request used to resolve shared components for plugins."""

    snapshot: SensorSnapshot
    output_dir: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _components: dict[str, Any] = field(default_factory=dict, repr=False)
    _component_errors: dict[str, str] = field(default_factory=dict, repr=False)
    shared_memory: SharedMemory | None = field(default=None, repr=False, compare=False)

    def sensor(self, sensor_id: str) -> SensorReading | None:
        return self.snapshot.readings.get(sensor_id)

    def resolve_component(
        self,
        component_id: str,
        provider: Callable[[], ComponentT],
    ) -> ComponentT | None:
        """Resolve one derived input once and share it across interested plugins."""

        if component_id in self._components:
            return self._components[component_id]
        if component_id in self._component_errors:
            return None
        try:
            component = provider()
        except PerceptionComponentUnavailable as exc:
            self._component_errors[component_id] = str(exc)
            return None
        if component is None:
            self._component_errors[component_id] = "component provider returned no value"
            return None
        self._components[component_id] = component
        return component

    def component(self, component_id: str) -> Any | None:
        return self._components.get(component_id)

    def component_error(self, component_id: str) -> str | None:
        return self._component_errors.get(component_id)

    def component_summary(self) -> dict[str, Any]:
        return {
            "available": {
                component_id: type(component).__name__
                for component_id, component in sorted(self._components.items())
            },
            "errors": dict(sorted(self._component_errors.items())),
        }
