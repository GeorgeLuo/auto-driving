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

from autonomy.perception.motion import analyze_scene_motion


def parse_roi(value: str) -> list[int]:
    parts = [int(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("roi must be x0,y0,x1,y1")
    return parts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Track whole-frame features and group motion-consistent scene regions."
    )
    parser.add_argument("image_a")
    parser.add_argument("image_b")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--max-features", type=int, default=240)
    parser.add_argument("--min-distance", type=int, default=5)
    parser.add_argument("--patch-radius", type=int, default=4)
    parser.add_argument("--search-radius", type=int, default=90)
    parser.add_argument("--min-score", type=float, default=0.70)
    parser.add_argument("--max-groups", type=int, default=6)
    parser.add_argument("--min-group-size", type=int, default=8)
    parser.add_argument("--residual-threshold", type=float, default=7.0)
    parser.add_argument("--roi", type=parse_roi, default=None,
                        help="Optional search region as x0,y0,x1,y1. Defaults to the whole frame.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir or Path("lab") / "runs" / "scene_motion" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    result = analyze_scene_motion(
        args.image_a,
        args.image_b,
        out_dir,
        max_features=args.max_features,
        min_distance=args.min_distance,
        patch_radius=args.patch_radius,
        search_radius=args.search_radius,
        min_score=args.min_score,
        max_groups=args.max_groups,
        min_group_size=args.min_group_size,
        residual_threshold=args.residual_threshold,
        roi=args.roi,
    )

    print(f"out_dir: {out_dir}")
    print(f"roi: {result.roi}")
    print(f"keypoints: {result.keypoint_count}")
    print(f"matches: {result.match_count}")
    print(f"grouped: {result.grouped_match_count}")
    print(f"ungrouped: {result.ungrouped_match_count}")
    for group in result.groups:
        print(
            f"g{group.group_id}: matches={group.match_count} "
            f"shift={group.center_shift_px} scale={group.scale} "
            f"kind={group.kind_hint} bbox={group.source_bbox}"
        )
    print(json.dumps(result.output_files, indent=2))
    print(json.dumps(asdict(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
