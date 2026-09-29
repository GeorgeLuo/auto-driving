"""Sensor-to-evidence contracts.

A perception plugin's ``perceive`` returns an evidence batch. The framework
combines batches into the step result. That result is current evidence.
"""

from .evidence import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionSignal,
    ViewLocation,
)
from .interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionMapper,
    PerceptionPluginRun,
    PerceptionRequest,
    PerceptionText,
)
from .plugin import (
    PerceptionComponentUnavailable,
    PerceptionDiagnosticSink,
    PerceptionPlugin,
    PerceptionPluginContract,
    PerceptionPluginInput,
    PerceptionPluginInputs,
    PerceptionPluginWarmingUp,
)
from .inputs import build_perception_request
from .activation import (
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
