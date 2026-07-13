#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from implementations.perception.features import track_features


def parse_bbox(value: str) -> list[int]:
    parts = [int(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must be x0,y0,x1,y1")
    return parts


def main() -> int:
    parser = argparse.ArgumentParser(description="Track stable image features between two stills.")
    parser.add_argument("image_a")
    parser.add_argument("image_b")
    parser.add_argument("--bbox", type=parse_bbox, required=True,
                        help="Source ROI as x0,y0,x1,y1.")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--max-features", type=int, default=80)
    parser.add_argument("--min-distance", type=int, default=7)
    parser.add_argument("--patch-radius", type=int, default=5)
    parser.add_argument("--search-radius", type=int, default=80)
    parser.add_argument("--min-score", type=float, default=0.72)
    args = parser.parse_args()

    image_a = Path(args.image_a)
    image_b = Path(args.image_b)
    out_dir = Path(args.out_dir or Path("lab") / "runs" / "tracks" / datetime.now().strftime("%Y%m%d-%H%M%S"))

    result = track_features(
        image_a,
        image_b,
        args.bbox,
        out_dir,
        max_features=args.max_features,
        min_distance=args.min_distance,
        patch_radius=args.patch_radius,
        search_radius=args.search_radius,
        min_score=args.min_score,
    )

    print(f"out_dir: {out_dir}")
    print(f"bbox: {result.bbox}")
    print(f"keypoints: {result.keypoint_count}")
    print(f"matches: {result.match_count}")
    print(f"inliers: {result.inlier_count}")
    print(f"median_dx_px: {result.median_dx_px}")
    print(f"median_dy_px: {result.median_dy_px}")
    print(f"center_shift_px: {result.center_shift_px}")
    print(f"scale: {result.scale}")
    print(json.dumps(result.output_files, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
