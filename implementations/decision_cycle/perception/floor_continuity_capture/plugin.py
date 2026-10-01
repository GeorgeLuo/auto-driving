from __future__ import annotations

from implementations.decision_cycle.perception.floor_continuity.plugin import FloorContinuityPlugin


class CaptureFloorContinuityPlugin(FloorContinuityPlugin):
    """Identify the manifest-configured capture-calibrated candidate."""

    plugin_id = "floor-continuity-capture-v1"
