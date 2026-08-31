from __future__ import annotations

from typing import Any

from lab.plugins.perception.floor_continuity.src.plugin import FloorContinuityPlugin


class CaptureFloorContinuityPlugin(FloorContinuityPlugin):
    """Run the floor-continuity detector with capture-calibrated defaults."""

    plugin_id = "floor-continuity-capture-v1"

    _DEFAULTS = {
        "working_width": 320,
        "horizon_ratio": 0.4,
        "edge_margin_ratio": 0.03,
        "seed_x0_ratio": 0.3,
        "seed_x1_ratio": 0.7,
        "seed_y0_ratio": 0.78,
        "seed_y1_ratio": 0.96,
        "color_distance_limit": 4.5,
        "texture_distance_limit": 4.0,
        "edge_quantile": 0.92,
        "minimum_edge_strength": 0.24,
        "minimum_floor_fraction": 0.08,
        "minimum_floor_support_px": 8,
        "minimum_interruption_run_px": 6,
        "minimum_boundary_width_ratio": 0.03,
        "minimum_boundary_confidence": 0.7,
        "max_boundaries": 8,
    }

    def __init__(self, **config: Any) -> None:
        resolved = dict(self._DEFAULTS)
        resolved.update(config)
        super().__init__(**resolved)
