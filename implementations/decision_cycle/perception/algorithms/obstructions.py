"""Box and image helpers shared by the obstruction-tracks perception and memory plugins."""

from __future__ import annotations

import cv2
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


def normalize_gray(rgb: np.ndarray, *, contrast_normalization="none", contrast_clip_limit=2.0, contrast_tile_size=8, contrast_gamma=1.0) -> np.ndarray:
    """Apply a parameterized, capture-agnostic luminance normalization."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if contrast_normalization == "clahe":
        return cv2.createCLAHE(
            clipLimit=contrast_clip_limit,
            tileGridSize=(contrast_tile_size, contrast_tile_size),
        ).apply(gray)
    if contrast_normalization == "stretch":
        return cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    if contrast_normalization == "gamma":
        lut = np.array(
            [((index / 255.0) ** contrast_gamma) * 255.0 for index in range(256)],
            dtype=np.float32,
        ).clip(0.0, 255.0).astype(np.uint8)
        return cv2.LUT(gray, lut)
    return gray
