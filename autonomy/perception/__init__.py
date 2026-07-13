"""Perception layers for black-box vehicle observations.

The package is organized by dependency direction:

- core: low-level frame observations with no scene interpretation
- features: reusable image-space feature tracking primitives
- motion: relative motion and motion-group evidence
- traversability: floor and traversable-region evidence
- landmarks: experimental landmark/distance estimators
"""

from .interface import (
    CameraFrame,
    PERCEPTION_TEXT_SCHEMA,
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
    "CameraFrame",
    "ActivatedPerceptionStage",
    "PERCEPTION_ACTIVATION_SCHEMA",
    "PERCEPTION_TEXT_SCHEMA",
    "PerceivedThing",
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
