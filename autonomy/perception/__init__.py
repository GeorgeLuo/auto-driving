"""Sensor-to-evidence contracts.

A perception plugin's ``perceive`` returns an evidence batch. The framework
combines batches into the step result. That result is current evidence.
Defined under ``autonomy.decision_cycle.perception``; names imported here are
those objects.
"""

from autonomy.decision_cycle.perception.evidence.values import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionSignal,
    ViewLocation,
)
from autonomy.decision_cycle.perception.components.context import PerceptionRequest
from autonomy.decision_cycle.perception.components.interface import PerceptionComponentUnavailable
from autonomy.decision_cycle.perception.diagnostics.sink import PerceptionDiagnosticSink
from autonomy.decision_cycle.perception.interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionMapper,
    PerceptionPluginRun,
    PerceptionText,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPlugin,
    PerceptionPluginContract,
    PerceptionPluginInput,
    PerceptionPluginInputs,
    PerceptionPluginWarmingUp,
)
from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.perception.activation import (
    PERCEPTION_ACTIVATION_SCHEMA,
    ActivatedPerceptionStep,
    PerceptionActivation,
    instantiate_perception_mapper,
    load_perception_mapper,
    read_perception_activation,
)

__all__ = [
    "ActivatedPerceptionStep",
    "PERCEPTION_ACTIVATION_SCHEMA",
    "PERCEPTION_TEXT_SCHEMA",
    "PerceivedThing",
    "PerceptionComponentUnavailable",
    "PerceptionDiagnosticSink",
    "PerceptionEvidenceBatch",
    "PerceptionMapper",
    "PerceptionActivation",
    "PerceptionPlugin",
    "PerceptionPluginContract",
    "PerceptionPluginInput",
    "PerceptionPluginInputs",
    "PerceptionPluginRun",
    "PerceptionPluginWarmingUp",
    "PerceptionRequest",
    "PerceptionSignal",
    "PerceptionText",
    "ViewLocation",
    "build_perception_request",
    "instantiate_perception_mapper",
    "load_perception_mapper",
    "read_perception_activation",
]
