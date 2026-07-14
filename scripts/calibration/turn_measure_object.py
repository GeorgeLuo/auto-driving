#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from implementations.vehicle.picar.defaults import get_default_local_car_base_url
from scripts.calibration.step_measure_object import Observation, observe, post_drive
from implementations.perception.features import analyze_tracked_sequence


def fit_center_shift(observations: list[Observation]) -> dict[str, float | None]:
    points = [(obs.step, obs.center_px[0], obs.width_px) for obs in observations
              if obs.center_px and obs.width_px]
    if len(points) < 2:
        return {
            "center_shift_px_per_turn_step": None,
            "linearized_camera_width_turn_steps": None,
            "center_fit_r2": None,
        }

    steps = np.array([p[0] for p in points], dtype=np.float64)
    centers = np.array([p[1] for p in points], dtype=np.float64)
    slope, intercept = np.polyfit(steps, centers, 1)
    predicted = np.polyval([slope, intercept], steps)
    ss_res = float(((centers - predicted) ** 2).sum())
    ss_tot = float(((centers - centers.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot else 1.0

    image_width = Image.open(observations[0].image).size[0]
    camera_width_turn_steps = None
    if abs(slope) > 1e-6:
        camera_width_turn_steps = float(image_width / abs(slope))

    return {
        "center_shift_px_per_turn_step": float(slope),
        "linearized_camera_width_turn_steps": camera_width_turn_steps,
        "center_fit_r2": r2,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Turn in fixed pulses and measure object center shift.")
    parser.add_argument("--base-url", default=get_default_local_car_base_url())
    parser.add_argument("--steps", type=int, default=3)
    parser.add_argument("--angle", type=float, default=-1.0,
                        help="Steering angle for the turn pulse. Negative is left.")
    parser.add_argument("--throttle", type=float, default=1.0)
    parser.add_argument("--duration", type=float, default=0.25)
    parser.add_argument("--settle", type=float, default=1.0)
    parser.add_argument("--stop-width-ratio", type=float, default=0.52)
    parser.add_argument("--no-feature-tracking", action="store_true",
                        help="Skip pairwise feature tracking in the output summary.")
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()

    out_dir = Path(args.out_dir or Path("lab") / "runs" / "turns" / datetime.now().strftime("%Y%m%d-%H%M%S"))
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

            post_drive(args.base_url, args.angle, args.throttle)
            time.sleep(args.duration)
            post_drive(args.base_url, 0.0, 0.0)
            time.sleep(args.settle)
            observations.append(observe(step, args.base_url, out_dir))

    finally:
        post_drive(args.base_url, 0.0, 0.0)

    result = {
        "base_url": args.base_url,
        "angle": args.angle,
        "throttle": args.throttle,
        "duration_s": args.duration,
        "settle_s": args.settle,
        "observations": [asdict(obs) for obs in observations],
        "fit": fit_center_shift(observations),
    }
    if not args.no_feature_tracking:
        tracked = analyze_tracked_sequence(result["observations"], out_dir / "tracked", search_radius=90)
        result["tracked"] = asdict(tracked)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"out_dir: {out_dir}")
    for obs in observations:
        center_x = None if obs.center_px is None else round(obs.center_px[0], 2)
        print(f"turn {obs.step}: center_x={center_x} width_px={obs.width_px} bbox={obs.bbox}")
    print(json.dumps(result["fit"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
