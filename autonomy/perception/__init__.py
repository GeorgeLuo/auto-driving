"""Stable contracts and runtime plumbing for component-driven perception."""

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
    ActivatedPerceptionStage,
    PerceptionActivation,
    instantiate_perception_mapper,
    load_perception_mapper,
    read_perception_activation,
)

__all__ = [
    "ActivatedPerceptionStage",
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
