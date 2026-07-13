"""Stable contracts and runtime plumbing for component-driven perception."""

from .interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionComponentUnavailable,
    PerceivedThing,
    PerceptionMapper,
    PerceptionPlugin,
    PerceptionPluginContract,
    PerceptionPluginResult,
    PerceptionPluginRun,
    PerceptionRequest,
    PerceptionText,
    ViewLocation,
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
    "PerceptionMapper",
    "PerceptionActivation",
    "PerceptionPlugin",
    "PerceptionPluginContract",
    "PerceptionPluginResult",
    "PerceptionPluginRun",
    "PerceptionRequest",
    "PerceptionText",
    "ViewLocation",
    "build_perception_request",
    "instantiate_perception_mapper",
    "load_perception_mapper",
    "read_perception_activation",
]
