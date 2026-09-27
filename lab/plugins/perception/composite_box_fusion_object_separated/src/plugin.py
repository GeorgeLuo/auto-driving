"""Composite fusion variant with recursive separation of spatial modes."""

from typing import Any

from lab.plugins.perception.composite_box_fusion.src.plugin import *  # noqa: F401,F403
from lab.plugins.perception.composite_box_fusion.src.plugin import (
    CompositeBoxFusionPlugin as _CompositeBoxFusionPlugin,
    _canonical_json,
    _center_distance,
    _json_safe,
)


class CompositeBoxFusionPlugin(_CompositeBoxFusionPlugin):
    """Keep the variant entrypoint and its object separation default."""

    _emit_object_separated_geometry = True

    def __init__(self, *, object_separated_geometry: bool = True, **config: Any) -> None:
        super().__init__(object_separated_geometry=object_separated_geometry, **config)
