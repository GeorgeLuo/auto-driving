"""Perception layers for black-box vehicle observations.

The package is organized by dependency direction:

- core: low-level frame observations with no scene interpretation
- features: reusable image-space feature tracking primitives
- motion: relative motion and motion-group evidence
- traversability: floor and traversable-region evidence
- landmarks: experimental landmark/distance estimators
"""

from .interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceivedThing,
    PerceptionMapper,
    PerceptionRequest,
    PerceptionText,
    ViewLocation,
)

__all__ = [
    "PERCEPTION_TEXT_SCHEMA",
    "PerceivedThing",
    "PerceptionMapper",
    "PerceptionRequest",
    "PerceptionText",
    "ViewLocation",
]
