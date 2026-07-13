#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from implementations.vehicle.picar.defaults import get_default_local_car_base_url
from implementations.perception.features import analyze_tracked_sequence


@dataclass
class Observation:
    step: int
    image: str
    annotated: str
    bbox: list[int] | None
    width_px: int | None
    height_px: int | None
    center_px: list[float] | None


def capture_frame(base_url: str, path: Path) -> None:
    response = requests.get(f"{base_url}/frame.jpg", timeout=8)
    response.raise_for_status()
    path.write_bytes(response.content)


def post_drive(base_url: str, angle: float, throttle: float) -> None:
    payload = {
        "angle": angle,
        "throttle": throttle,
        "drive_mode": "user",
        "recording": False,
    }
    response = requests.post(f"{base_url}/drive", json=payload, timeout=2)
    response.raise_for_status()


def find_warm_foreground(image_path: Path) -> tuple[list[int] | None, Image.Image]:
    image = Image.open(image_path).convert("RGB")
    arr = np.asarray(image).astype(np.float32)
    height, width = arr.shape[:2]
    gray = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]

    component = find_vertical_edge_pair(arr, gray)
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    if component is None:
        return None, annotated

    x0, y0, x1, y1 = component
    draw.rectangle([x0, y0, x1, y1], outline=(255, 40, 20), width=3)
    draw.text((x0 + 3, max(0, y0 - 14)), f"{x1 - x0 + 1}px", fill=(255, 40, 20))
    return [x0, y0, x1, y1], annotated


def find_vertical_edge_pair(rgb: np.ndarray, gray: np.ndarray) -> list[int] | None:
    height, width = gray.shape
    y0 = int(height * 0.24)
    y1 = int(height * 0.68)
    x_min = int(width * 0.06)
    x_max = int(width * 0.94)

    edge = np.abs(np.diff(gray[y0:y1, :], axis=1))
    col = edge.sum(axis=0)
    col = np.convolve(col, np.ones(5) / 5.0, mode="same")

    peaks: list[tuple[float, int]] = []
    for x in range(max(2, x_min), min(width - 3, x_max)):
        if col[x] > col[x - 1] and col[x] >= col[x + 1]:
            peaks.append((float(col[x]), x))
    peaks = sorted(peaks, reverse=True)[:28]

    best: tuple[float, list[int]] | None = None
    min_width = int(width * 0.10)
    max_width = int(width * 0.78)
    for left_strength, left in peaks:
        for right_strength, right in peaks:
            if right <= left:
                continue
            object_width = right - left + 1
            if object_width < min_width or object_width > max_width:
                continue

            cx = (left + right) / 2.0
            center_score = 1.0 - min(abs(cx - width / 2.0) / (width / 2.0), 0.95)
            if center_score < 0.35:
                continue

            interior = rgb[y0:y1, left:right + 1]
            r, g, b = interior[..., 0], interior[..., 1], interior[..., 2]
            luma = (r + g + b) / 3.0
            warm = (r > b * 1.12) & (g > b * 0.72) & (luma > 30) & (luma < 200)
            warm_ratio = float(warm.mean()) if warm.size else 0.0
            horizontal_edge = np.abs(np.diff(gray[:, left:right + 1], axis=0))
            row_strength = horizontal_edge[y0:y1].sum(axis=1) / max(object_width, 1)
            horizontal_score = min(float(row_strength.max()) / 28.0, 1.0) if row_strength.size else 0.0
            width_score = 1.0 - min(abs(object_width / width - 0.28) / 0.55, 0.7)
            score = (
                (left_strength + right_strength) *
                (0.40 + 0.60 * center_score) *
                (0.55 + 0.45 * warm_ratio) *
                (0.35 + 0.65 * horizontal_score) *
                width_score
            )
            if best is None or score > best[0]:
                best = (score, [left, y0, right, y1])

    return None if best is None else best[1]


def observe(step: int, base_url: str, out_dir: Path) -> Observation:
    image_path = out_dir / f"step_{step:02d}.jpg"
    annotated_path = out_dir / f"step_{step:02d}_annotated.jpg"
    capture_frame(base_url, image_path)
    bbox, annotated = find_warm_foreground(image_path)
    annotated.save(annotated_path, quality=92)

    if bbox is None:
        return Observation(step, str(image_path), str(annotated_path), None, None, None, None)

    x0, y0, x1, y1 = bbox
    return Observation(
        step=step,
        image=str(image_path),
        annotated=str(annotated_path),
        bbox=bbox,
        width_px=x1 - x0 + 1,
        height_px=y1 - y0 + 1,
        center_px=[(x0 + x1) / 2, (y0 + y1) / 2],
    )


def fit_step_scale(observations: list[Observation], hfov_deg: float | None) -> dict[str, float | None]:
    points = [(obs.step, obs.width_px) for obs in observations if obs.width_px and obs.width_px > 0]
    if len(points) < 2:
        return {
            "initial_distance_steps": None,
            "focal_width_product_px_steps": None,
            "object_width_steps": None,
        }

    steps = np.array([p[0] for p in points], dtype=np.float64)
    inv_widths = np.array([1.0 / p[1] for p in points], dtype=np.float64)
    slope, intercept = np.polyfit(steps, inv_widths, 1)
    if slope >= 0:
        return {
            "initial_distance_steps": None,
            "focal_width_product_px_steps": None,
            "object_width_steps": None,
        }

    initial_distance_steps = float(intercept / -slope)
    focal_width_product = float(-1.0 / slope)
    object_width_steps = None
    if hfov_deg:
        image_width = Image.open(observations[0].image).size[0]
        focal_px = (image_width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
        object_width_steps = float(focal_width_product / focal_px)

    return {
        "initial_distance_steps": initial_distance_steps,
        "focal_width_product_px_steps": focal_width_product,
        "object_width_steps": object_width_steps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive fixed steps toward the front object and measure apparent width.")
    parser.add_argument("--base-url", default=get_default_local_car_base_url())
    parser.add_argument("--steps", type=int, default=2)
    parser.add_argument("--throttle", type=float, default=0.18)
    parser.add_argument("--duration", type=float, default=0.22)
    parser.add_argument("--settle", type=float, default=0.7)
    parser.add_argument("--stop-width-ratio", type=float, default=0.78)
    parser.add_argument("--hfov-deg", type=float, default=None,
                        help="Optional camera horizontal FOV; needed to convert width into step units.")
    parser.add_argument("--no-feature-tracking", action="store_true",
                        help="Skip pairwise feature tracking in the output summary.")
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()

    out_dir = Path(args.out_dir or Path("lab") / "runs" / "steps" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)

    observations: list[Observation] = []
    try:
        post_drive(args.base_url, 0.0, 0.0)
        time.sleep(args.settle)
        observations.append(observe(0, args.base_url, out_dir))

        image_width = Image.open(observations[0].image).size[0]
        for step in range(1, args.steps + 1):
            previous = observations[-1]
            if previous.width_px and previous.width_px / image_width >= args.stop_width_ratio:
                break

            post_drive(args.base_url, 0.0, args.throttle)
            time.sleep(args.duration)
            post_drive(args.base_url, 0.0, 0.0)
            time.sleep(args.settle)
            observations.append(observe(step, args.base_url, out_dir))

    finally:
        post_drive(args.base_url, 0.0, 0.0)

    result = {
        "base_url": args.base_url,
        "throttle": args.throttle,
        "duration_s": args.duration,
        "settle_s": args.settle,
        "observations": [asdict(obs) for obs in observations],
        "fit": fit_step_scale(observations, args.hfov_deg),
    }
    if not args.no_feature_tracking:
        tracked = analyze_tracked_sequence(result["observations"], out_dir / "tracked", search_radius=80)
        result["tracked"] = asdict(tracked)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"out_dir: {out_dir}")
    for obs in observations:
        print(f"step {obs.step}: width_px={obs.width_px} bbox={obs.bbox}")
    print(json.dumps(result["fit"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
