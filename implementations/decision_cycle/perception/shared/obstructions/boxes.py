"""Box helpers shared by the obstruction-tracks perception and memory plugins."""

from __future__ import annotations

import numpy as np


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


def center_distance(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    return float(
        np.hypot(
            ((left[0] + left[2]) / 2.0) - ((right[0] + right[2]) / 2.0),
            ((left[1] + left[3]) / 2.0) - ((right[1] + right[3]) / 2.0),
        )
    )
