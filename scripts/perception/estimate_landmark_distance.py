#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autonomy.perception.landmarks import estimate_landmark_distance


def load_images_and_steps(inputs: list[str]) -> tuple[list[str], list[float]]:
    if len(inputs) == 1 and inputs[0].endswith(".json"):
        run = json.loads(Path(inputs[0]).read_text(encoding="utf-8"))
        observations = [obs for obs in run["observations"] if obs.get("image")]
        return [obs["image"] for obs in observations], [float(obs["step"]) for obs in observations]
    return inputs, [float(index) for index in range(len(inputs))]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover an expanding visual landmark and estimate distance to it in step units."
    )
    parser.add_argument("inputs", nargs="+",
                        help="A run summary.json or two or more image paths.")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--scene-max-features", type=int, default=600)
    parser.add_argument("--scene-search-radius", type=int, default=260)
    parser.add_argument("--scene-min-score", type=float, default=0.66)
    parser.add_argument("--scene-min-group-size", type=int, default=10)
    parser.add_argument("--track-max-features", type=int, default=140)
    parser.add_argument("--track-search-radius", type=int, default=260)
    parser.add_argument("--track-min-score", type=float, default=0.66)
    args = parser.parse_args()

    images, steps = load_images_and_steps(args.inputs)
    out_dir = Path(args.out_dir or Path("lab") / "runs" / "landmarks" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    result = estimate_landmark_distance(
        images,
        out_dir,
        steps=steps,
        scene_max_features=args.scene_max_features,
        scene_search_radius=args.scene_search_radius,
        scene_min_score=args.scene_min_score,
        scene_min_group_size=args.scene_min_group_size,
        track_max_features=args.track_max_features,
        track_search_radius=args.track_search_radius,
        track_min_score=args.track_min_score,
    )

    print(f"out_dir: {out_dir}")
    if result.landmark is None:
        print("landmark: none")
        print(json.dumps(asdict(result), indent=2))
        return 2

    print(
        "landmark: "
        f"group={result.landmark.group_id} "
        f"score={result.landmark.score:.4f} "
        f"bbox={result.landmark.source_bbox} "
        f"scale={result.landmark.scale:.4f} "
        f"distance_before_first_step={result.landmark.distance_before_first_step:.2f}"
    )
    for estimate in result.estimates:
        remaining = None if estimate.distance_remaining_steps is None else round(estimate.distance_remaining_steps, 2)
        cumulative_scale = None if estimate.cumulative_scale is None else round(estimate.cumulative_scale, 4)
        print(
            f"step {estimate.step:g}: remaining={remaining} "
            f"cum_scale={cumulative_scale} "
            f"inliers={estimate.inliers}/{estimate.matches} "
            f"bbox={estimate.bbox}"
        )
    print(json.dumps(asdict(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
