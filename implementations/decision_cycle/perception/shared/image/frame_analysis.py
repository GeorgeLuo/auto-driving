"""Camera-frame brightness and color measurements."""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageStat


def _channel_means(stat: ImageStat.Stat) -> list[float]:
    return [round(float(value), 3) for value in stat.mean]


def _channel_stds(stat: ImageStat.Stat) -> list[float]:
    return [round(float(value), 3) for value in stat.stddev]


def observe_rgb_frame(
    rgb: np.ndarray,
    previous_rgb: np.ndarray | None = None,
) -> dict[str, Any]:
    """Extract the same cheap facts from a normalized RGB array."""

    image = Image.fromarray(rgb, mode="RGB")
    gray = image.convert("L")
    rgb_stat = ImageStat.Stat(image)
    gray_stat = ImageStat.Stat(gray)
    change = None
    if previous_rgb is not None:
        previous = Image.fromarray(previous_rgb, mode="RGB")
        previous.thumbnail((160, 120))
        current = image.copy()
        current.thumbnail((160, 120))
        canvas_previous = Image.new("L", (160, 120), 0)
        canvas_current = Image.new("L", (160, 120), 0)
        previous_gray = previous.convert("L")
        current_gray = current.convert("L")
        canvas_previous.paste(previous_gray, ((160 - previous_gray.width) // 2, (120 - previous_gray.height) // 2))
        canvas_current.paste(current_gray, ((160 - current_gray.width) // 2, (120 - current_gray.height) // 2))
        mean_diff = float(ImageStat.Stat(ImageChops.difference(canvas_previous, canvas_current)).mean[0])
        change = round(mean_diff / 255.0, 5)

    return {
        "image_width_px": image.width,
        "image_height_px": image.height,
        "brightness_mean": round(float(gray_stat.mean[0]), 3),
        "contrast_std": round(float(gray_stat.stddev[0]), 3),
        "rgb_mean": _channel_means(rgb_stat),
        "rgb_std": _channel_stds(rgb_stat),
        "change_from_previous": change,
    }
