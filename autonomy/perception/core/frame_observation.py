from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat


def _channel_means(stat: ImageStat.Stat) -> list[float]:
    return [round(float(value), 3) for value in stat.mean]


def _channel_stds(stat: ImageStat.Stat) -> list[float]:
    return [round(float(value), 3) for value in stat.stddev]


def _thumbnail_gray(path: Path, size: tuple[int, int] = (160, 120)) -> Image.Image:
    image = Image.open(path).convert("L")
    image.thumbnail(size)
    canvas = Image.new("L", size, 0)
    canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def estimate_frame_change(previous_path: Path | None, image_path: Path) -> float | None:
    if previous_path is None or not previous_path.exists():
        return None
    previous = _thumbnail_gray(previous_path)
    current = _thumbnail_gray(image_path)
    diff = ImageChops.difference(previous, current)
    mean_diff = float(ImageStat.Stat(diff).mean[0])
    return round(mean_diff / 255.0, 5)


def compare_frame_pair(
    before_path: Path,
    after_path: Path,
    *,
    size: tuple[int, int] = (160, 120),
    pixel_threshold: int = 18,
) -> dict[str, Any]:
    """Return cheap, vehicle-agnostic visual change metrics for two frames."""
    before = _thumbnail_gray(before_path, size=size)
    after = _thumbnail_gray(after_path, size=size)
    diff = ImageChops.difference(before, after)
    stat = ImageStat.Stat(diff)
    total_pixels = max(1, diff.width * diff.height)
    threshold = max(0, min(255, int(pixel_threshold)))
    histogram = diff.histogram()
    changed_pixels = sum(histogram[threshold + 1:])
    diff_extrema = diff.getextrema()

    return {
        "before_path": str(before_path),
        "after_path": str(after_path),
        "thumbnail_width_px": diff.width,
        "thumbnail_height_px": diff.height,
        "pixel_threshold": threshold,
        "mean_abs_diff": round(float(stat.mean[0]), 3),
        "mean_abs_diff_norm": round(float(stat.mean[0]) / 255.0, 5),
        "rms_abs_diff": round(float(stat.rms[0]), 3),
        "rms_abs_diff_norm": round(float(stat.rms[0]) / 255.0, 5),
        "changed_pixel_ratio": round(changed_pixels / total_pixels, 5),
        "max_abs_diff": int(diff_extrema[1] if isinstance(diff_extrema, tuple) else 0),
    }


def observe_frame(image_path: Path, previous_path: Path | None = None) -> dict[str, Any]:
    """Extract cheap image facts without deciding what action to take."""
    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        gray = image.convert("L")
        rgb_stat = ImageStat.Stat(rgb)
        gray_stat = ImageStat.Stat(gray)
        width, height = image.size

    return {
        "image_width_px": width,
        "image_height_px": height,
        "brightness_mean": round(float(gray_stat.mean[0]), 3),
        "contrast_std": round(float(gray_stat.stddev[0]), 3),
        "rgb_mean": _channel_means(rgb_stat),
        "rgb_std": _channel_stds(rgb_stat),
        "change_from_previous": estimate_frame_change(previous_path, image_path),
    }
