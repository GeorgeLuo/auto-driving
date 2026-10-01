from __future__ import annotations

from implementations.decision_cycle.perception.plugins.floor_continuity.plugin import FloorContinuityPlugin


class CaptureFloorContinuityPlugin(FloorContinuityPlugin):
    """Identify the manifest-configured capture-calibrated candidate."""

    plugin_id = "floor_continuity_capture"
