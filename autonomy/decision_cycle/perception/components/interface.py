"""Component declarations and the provider contract.

A plugin declares each input as a ``PerceptionPluginInput``: the name it reads,
the shared component ID, and the provider spec. A provider receives the
perception request and that declaration. It raises
``PerceptionComponentUnavailable`` when the component cannot be derived from
the sensor snapshot.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from autonomy.decision_cycle.perception.components.context import PerceptionRequest


class PerceptionComponentUnavailable(RuntimeError):
    """A declared plugin input cannot be derived from the sensor snapshot."""


@dataclass(frozen=True)
class PerceptionPluginInput:
    """One named component injected into a plugin by the framework."""

    name: str
    component_id: str
    provider_spec: str

    def __post_init__(self) -> None:
        if not self.name or not self.component_id or not self.provider_spec:
            raise ValueError("plugin inputs require name, component_id, and provider_spec")
        if ":" not in self.provider_spec:
            raise ValueError("component provider spec must be 'module.path:callable'")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


ComponentProvider = Callable[["PerceptionRequest", PerceptionPluginInput], Any]
