from __future__ import annotations

from lab.plugins.perception.floor_continuity.src.plugin import FloorContinuityPlugin


class CaptureFloorContinuityPlugin(FloorContinuityPlugin):
    """Identify the manifest-configured capture-calibrated candidate."""

    plugin_id = "floor-continuity-capture-v1"
