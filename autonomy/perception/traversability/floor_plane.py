from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw


@dataclass
class FloorPlaneConfig:
    horizon_ratio: float = 0.40
    seed_x0_ratio: float = 0.25
    seed_x1_ratio: float = 0.75
    seed_y0_ratio: float = 0.72
    seed_y1_ratio: float = 0.96
    floor_threshold: float = 3.8
    brightness_weight: float = 0.55
    morph_radius: int = 2
    morph_threshold: float = 0.42
    topdown_width: int = 320
    topdown_height: int = 420
    topdown_margin: int = 18
    topdown_near_width_ratio: float = 0.10
    topdown_far_width_ratio: float = 0.96
    obstacle_run_length: int = 12
    near_width_ratio: float = 1.0
    far_width_ratio: float = 0.34


@dataclass
class StillProcessingResult:
    width: int
    height: int
    floor_fraction_roi: float
    occupied_fraction_roi: float
    output_files: dict[str, str]
    config: dict[str, Any]


def process_still(image_path: str | Path, out_dir: str | Path,
                  config: FloorPlaneConfig | None = None) -> StillProcessingResult:
    config = config or FloorPlaneConfig()
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    image = Image.open(image_path).convert("RGB")
    rgb = np.asarray(image).astype(np.float32) / 255.0
    height, width = rgb.shape[:2]

    floor_mask, roi_mask, model_info = estimate_floor_mask(rgb, config)
    occupied_mask = roi_mask & ~floor_mask
    overlay = make_overlay(image, floor_mask, occupied_mask, config)
    topdown_rgb, occupancy = project_topdown(rgb, floor_mask, occupied_mask, config)
    occupancy_img = render_occupancy(occupancy)

    files = {
        "frame": "frame.jpg",
        "floor_mask": "floor_mask.png",
        "overlay": "overlay.png",
        "topdown_rgb": "topdown_rgb.jpg",
        "occupancy": "occupancy.png",
        "occupancy_grid": "occupancy.npy",
        "summary": "summary.json",
    }

    image.save(out_path / files["frame"], quality=92)
    Image.fromarray((floor_mask.astype(np.uint8) * 255), mode="L").save(out_path / files["floor_mask"])
    overlay.save(out_path / files["overlay"])
    Image.fromarray(topdown_rgb).save(out_path / files["topdown_rgb"], quality=92)
    occupancy_img.save(out_path / files["occupancy"])
    np.save(out_path / files["occupancy_grid"], occupancy)

    roi_pixels = max(int(roi_mask.sum()), 1)
    result = StillProcessingResult(
        width=width,
        height=height,
        floor_fraction_roi=float((floor_mask & roi_mask).sum() / roi_pixels),
        occupied_fraction_roi=float((occupied_mask & roi_mask).sum() / roi_pixels),
        output_files={name: str(out_path / rel) for name, rel in files.items()},
        config=asdict(config) | {"floor_model": model_info},
    )

    with open(out_path / files["summary"], "w", encoding="utf-8") as f:
        json.dump(asdict(result), f, indent=2)

    return result


def estimate_floor_mask(rgb: np.ndarray, config: FloorPlaneConfig) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    height, width = rgb.shape[:2]
    yy = np.arange(height)[:, None]
    roi_mask = np.broadcast_to(yy >= int(height * config.horizon_ratio), (height, width))

    x0 = int(width * config.seed_x0_ratio)
    x1 = int(width * config.seed_x1_ratio)
    y0 = int(height * config.seed_y0_ratio)
    y1 = int(height * config.seed_y1_ratio)

    features = color_features(rgb, config.brightness_weight)
    seed = features[y0:y1, x0:x1].reshape(-1, features.shape[-1])
    seed_luma = seed[:, -1]
    low, high = np.quantile(seed_luma, [0.08, 0.92])
    seed = seed[(seed_luma >= low) & (seed_luma <= high)]

    center = np.median(seed, axis=0)
    spread = np.median(np.abs(seed - center), axis=0) * 1.4826
    spread = np.maximum(spread, np.array([0.025, 0.025, 0.025, 0.04], dtype=np.float32))

    dist = np.sqrt(np.sum(((features - center) / spread) ** 2, axis=-1))
    floor_mask = (dist <= config.floor_threshold) & roi_mask
    floor_mask = majority_filter(floor_mask, config.morph_radius, config.morph_threshold) & roi_mask

    model_info = {
        "seed_rect": [x0, y0, x1, y1],
        "feature_center": center.round(5).tolist(),
        "feature_spread": spread.round(5).tolist(),
    }
    return floor_mask, roi_mask, model_info


def color_features(rgb: np.ndarray, brightness_weight: float) -> np.ndarray:
    total = np.maximum(rgb.sum(axis=-1, keepdims=True), 1e-4)
    chroma = rgb / total
    luma = (
        0.299 * rgb[..., 0] +
        0.587 * rgb[..., 1] +
        0.114 * rgb[..., 2]
    )[..., None] * brightness_weight
    return np.concatenate([chroma, luma], axis=-1)


def majority_filter(mask: np.ndarray, radius: int, threshold: float) -> np.ndarray:
    if radius <= 0:
        return mask
    counts = window_sum(mask, radius)
    area = (radius * 2 + 1) ** 2
    return counts >= area * threshold


def window_sum(mask: np.ndarray, radius: int) -> np.ndarray:
    padded = np.pad(mask.astype(np.uint8), radius, mode="constant")
    integral = padded.cumsum(axis=0).cumsum(axis=1)
    integral = np.pad(integral, ((1, 0), (1, 0)), mode="constant")
    size = radius * 2 + 1
    return (
        integral[size:, size:]
        - integral[:-size, size:]
        - integral[size:, :-size]
        + integral[:-size, :-size]
    )


def make_overlay(image: Image.Image, floor_mask: np.ndarray, occupied_mask: np.ndarray,
                 config: FloorPlaneConfig) -> Image.Image:
    base = image.convert("RGBA")
    arr = np.asarray(base).copy()
    floor = floor_mask.astype(bool)
    occupied = occupied_mask.astype(bool)
    arr[floor] = blend(arr[floor], np.array([40, 220, 80, 255], dtype=np.uint8), 0.36)
    arr[occupied] = blend(arr[occupied], np.array([240, 60, 40, 255], dtype=np.uint8), 0.34)

    overlay = Image.fromarray(arr, mode="RGBA")
    draw = ImageDraw.Draw(overlay)
    width, height = image.size
    horizon_y = int(height * config.horizon_ratio)
    src = source_quad(width, height, config)
    draw.line([(0, horizon_y), (width - 1, horizon_y)], fill=(255, 255, 0, 255), width=1)
    draw.polygon([tuple(p) for p in src], outline=(255, 255, 0, 255))
    return overlay.convert("RGB")


def blend(src: np.ndarray, color: np.ndarray, alpha: float) -> np.ndarray:
    return (src * (1.0 - alpha) + color * alpha).astype(np.uint8)


def project_topdown(rgb: np.ndarray, floor_mask: np.ndarray, occupied_mask: np.ndarray,
                    config: FloorPlaneConfig) -> tuple[np.ndarray, np.ndarray]:
    height, width = rgb.shape[:2]
    src = source_quad(width, height, config)
    dst = topdown_quad(config)
    fov_mask = polygon_mask(config.topdown_width, config.topdown_height, dst)
    homography = compute_homography(src, dst)
    inv = np.linalg.inv(homography)

    grid_x, grid_y = np.meshgrid(
        np.arange(config.topdown_width, dtype=np.float64),
        np.arange(config.topdown_height, dtype=np.float64),
    )
    dest = np.stack([grid_x.ravel(), grid_y.ravel(), np.ones(grid_x.size)], axis=0)
    src_h = inv @ dest
    denom = src_h[2:3]
    denom = np.where(np.abs(denom) < 1e-8, np.where(denom < 0, -1e-8, 1e-8), denom)
    src_h /= denom
    sx = np.rint(src_h[0]).astype(np.int32)
    sy = np.rint(src_h[1]).astype(np.int32)
    valid = (sx >= 0) & (sx < width) & (sy >= 0) & (sy < height)
    valid &= fov_mask.ravel()

    topdown_rgb = np.zeros((config.topdown_height, config.topdown_width, 3), dtype=np.uint8)
    flat_rgb = topdown_rgb.reshape(-1, 3)

    valid_idx = np.where(valid)[0]
    sampled_rgb = (rgb[sy[valid], sx[valid]] * 255.0).astype(np.uint8)
    flat_rgb[valid_idx] = sampled_rgb

    occupancy = raycast_source_occupancy(floor_mask, occupied_mask, src, homography, config)
    return topdown_rgb, occupancy


def raycast_source_occupancy(floor_mask: np.ndarray, occupied_mask: np.ndarray,
                             source: np.ndarray, homography: np.ndarray,
                             config: FloorPlaneConfig) -> np.ndarray:
    height, width = floor_mask.shape
    source_mask = polygon_mask(width, height, source)
    min_run = max(int(config.obstacle_run_length), 1)
    line_width = max(2, int(round(config.topdown_width / max(width, 1))))
    hit_width = line_width + 2

    occupancy_img = Image.new("L", (config.topdown_width, config.topdown_height), 0)
    draw = ImageDraw.Draw(occupancy_img)
    hit_points: list[tuple[int, int]] = []
    segments: list[tuple[tuple[int, int], tuple[int, int]] | None] = []
    horizon_y = int(height * config.horizon_ratio)

    for x in range(width):
        floor_points: list[tuple[int, int]] = []
        run_length = 0
        run_start_y = -1

        for y in range(height - 1, horizon_y - 1, -1):
            if not source_mask[y, x]:
                run_length = 0
                continue

            if floor_mask[y, x]:
                point = project_point(homography, x, y)
                if point is not None:
                    floor_points.append(point)
                run_length = 0
            elif occupied_mask[y, x]:
                if run_length == 0:
                    run_start_y = y
                run_length += 1
                if run_length >= min_run:
                    point = project_point(homography, x, run_start_y)
                    if point is not None:
                        hit_points.append(point)
                    break
            else:
                run_length = 0

        if len(floor_points) >= 2:
            segments.append((floor_points[0], floor_points[-1]))
        elif floor_points:
            segments.append((floor_points[0], floor_points[0]))
        else:
            segments.append(None)

    for left, right in zip(segments, segments[1:]):
        if left is None or right is None:
            continue
        draw.polygon([left[0], right[0], right[1], left[1]], fill=1)

    for segment in segments:
        if segment is not None:
            draw.line([segment[0], segment[1]], fill=1, width=line_width)

    for x, y in hit_points:
        draw.ellipse([x - hit_width, y - hit_width, x + hit_width, y + hit_width], fill=2)

    return np.asarray(occupancy_img, dtype=np.uint8)


def project_point(homography: np.ndarray, x: float, y: float) -> tuple[int, int] | None:
    point = homography @ np.array([x, y, 1.0], dtype=np.float64)
    if abs(point[2]) < 1e-8:
        return None
    point = point / point[2]
    return int(round(point[0])), int(round(point[1]))


def topdown_quad(config: FloorPlaneConfig) -> np.ndarray:
    image_width = config.topdown_width - 1
    image_height = config.topdown_height - 1
    cx = image_width / 2
    near_half = image_width * config.topdown_near_width_ratio / 2
    far_half = image_width * config.topdown_far_width_ratio / 2
    return np.array([
        [cx - near_half, image_height],
        [cx + near_half, image_height],
        [cx + far_half, 0],
        [cx - far_half, 0],
    ], dtype=np.float64)


def polygon_mask(width: int, height: int, polygon: np.ndarray) -> np.ndarray:
    mask = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon([tuple(point) for point in polygon], fill=1)
    return np.asarray(mask, dtype=bool)


def source_quad(width: int, height: int, config: FloorPlaneConfig) -> np.ndarray:
    horizon_y = int(height * config.horizon_ratio)
    image_width = width - 1
    near_half = image_width * config.near_width_ratio / 2
    far_half = image_width * config.far_width_ratio / 2
    cx = image_width / 2
    return np.array([
        [cx - near_half, height - 1],
        [cx + near_half, height - 1],
        [cx + far_half, horizon_y],
        [cx - far_half, horizon_y],
    ], dtype=np.float64)


def compute_homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    rows = []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        rows.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _, _, vh = np.linalg.svd(np.asarray(rows, dtype=np.float64))
    h = vh[-1].reshape(3, 3)
    return h / h[2, 2]


def render_occupancy(occupancy: np.ndarray) -> Image.Image:
    colors = np.array([
        [28, 28, 32],
        [58, 178, 96],
        [218, 74, 58],
    ], dtype=np.uint8)
    return Image.fromarray(colors[occupancy], mode="RGB")
