"""Perception plugin contract, inputs, and protocol.

``PerceptionPluginContract`` declares what one plugin accepts and what its
evidence means. ``PerceptionPluginInputs`` are the resolved values the
framework passes to ``perceive``, which returns a ``PerceptionEvidenceBatch``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol, TypeVar, runtime_checkable

from autonomy.decision_cycle.perception.feeds.interface import PerceptionPluginInput
from autonomy.decision_cycle.perception.diagnostics.sink import (
    PerceptionDiagnosticSink,
    _safe_name,
)
from autonomy.decision_cycle.perception.evidence.values import PerceptionEvidenceBatch
from autonomy.shared_memory import SharedMemory


PLUGIN_STATE_MODES = ("stateless", "pairwise", "windowed")
PluginStateMode = Literal["stateless", "pairwise", "windowed"]
FeedT = TypeVar("FeedT")


class PerceptionPluginWarmingUp(RuntimeError):
    """A stateful plugin has valid input but not enough history yet."""

    def __init__(self, reason: str, *, measurements: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.measurements = dict(measurements or {})


@dataclass(frozen=True)
class PerceptionPluginContract:
    """What one perception plugin accepts and what its evidence means.

    History that later frames must see lives in the host map, ``shared_memory``.
    Instances may retain configuration and reusable model resources.
    ``state_mode`` is that temporal horizon. ``memory_required`` means the
    plugin needs the host map. Plugins that set it implement
    ``reset(shared_memory)`` and drop only their own keys. They own history
    shape, bounds, and commit policy.
    """

    inputs: tuple[PerceptionPluginInput, ...] = ()
    # Temporal input horizon: stateless, pairwise, or windowed.
    state_mode: PluginStateMode = "stateless"
    # Whether this perception plugin needs the host map.
    memory_required: bool = False
    description: str = ""
    assumptions: tuple[str, ...] = ()
    emits: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    diagnostic_artifacts: tuple[str, ...] = ()
    diagnostics_required: bool = False

    def __post_init__(self) -> None:
        input_names = [item.name for item in self.inputs]
        feed_ids = [item.feed_id for item in self.inputs]
        if len(input_names) != len(set(input_names)):
            raise ValueError("plugin input names must be unique")
        if len(feed_ids) != len(set(feed_ids)):
            raise ValueError("plugin feed ids must be unique")
        if self.state_mode not in PLUGIN_STATE_MODES:
            raise ValueError(f"unsupported plugin state mode: {self.state_mode!r}")
        if len(self.diagnostic_artifacts) != len(set(self.diagnostic_artifacts)):
            raise ValueError("diagnostic artifact ids must be unique")
        if any(_safe_name(item) != item for item in self.diagnostic_artifacts):
            raise ValueError("diagnostic artifact ids must be non-empty safe names")
        if self.diagnostics_required and not self.diagnostic_artifacts:
            raise ValueError("diagnostics_required needs declared diagnostic artifacts")

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputs": [item.to_dict() for item in self.inputs],
            "state_mode": self.state_mode,
            "memory_required": self.memory_required,
            "state_ownership": "host_shared_memory",
            "description": self.description,
            "assumptions": list(self.assumptions),
            "emits": list(self.emits),
            "limitations": list(self.limitations),
            "diagnostic_artifacts": list(self.diagnostic_artifacts),
            "diagnostics_required": self.diagnostics_required,
        }


@dataclass(frozen=True)
class PerceptionPluginInputs:
    """Resolved, typed-by-contract values presented to one plugin."""

    frame_id: str
    captured_at_ms: int
    feeds: Mapping[str, Any]
    diagnostics: PerceptionDiagnosticSink
    metadata: Mapping[str, Any] = field(default_factory=dict)
    shared_memory: SharedMemory | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "feeds", MappingProxyType(dict(self.feeds)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def require(self, name: str, expected_type: type[FeedT]) -> FeedT:
        if name not in self.feeds:
            raise KeyError(f"plugin input {name!r} was not injected")
        value = self.feeds[name]
        if not isinstance(value, expected_type):
            raise TypeError(
                f"plugin input {name!r} is {type(value).__name__}, "
                f"expected {expected_type.__name__}"
            )
        return value


@runtime_checkable
class PerceptionPlugin(Protocol):
    plugin_id: str
    contract: PerceptionPluginContract

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        ...
