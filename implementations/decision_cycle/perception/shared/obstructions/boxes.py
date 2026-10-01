"""Box helpers shared by the obstruction-tracks perception and memory plugins."""

from __future__ import annotations


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def zone(bbox: tuple[float, float, float, float]) -> str:
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    # Keep the lateral bands aligned with the proposal plugin's image-space
    # fallback thresholds.  This lets a centered-looking box still contribute
    # when its bbox supplies an unambiguous side cue.
    horizontal = "left" if cx < 0.45 else "right" if cx > 0.55 else "center"
    vertical = "near" if cy > 0.66 else "far" if cy < 0.33 else "mid"
    return f"{vertical}_{horizontal}"
