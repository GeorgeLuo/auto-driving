#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from implementations.perception.features import analyze_tracked_sequence


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze a captured step/turn sequence with feature tracking.")
    parser.add_argument("summary", help="Path to a run summary.json containing observations.")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--search-radius", type=int, default=80)
    parser.add_argument("--max-features", type=int, default=80)
    parser.add_argument("--min-score", type=float, default=0.72)
    args = parser.parse_args()

    summary_path = Path(args.summary)
    run = json.loads(summary_path.read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir or summary_path.parent / "tracked")

    result = analyze_tracked_sequence(
        run["observations"],
        out_dir,
        search_radius=args.search_radius,
        max_features=args.max_features,
        min_score=args.min_score,
    )

    print(f"out_dir: {out_dir}")
    print(f"pairs: {result.pair_count}")
    for pair in result.pairs:
        print(
            f"{pair.pair}: scale={pair.feature_scale} "
            f"shift={pair.feature_center_shift_px} "
            f"inliers={pair.inliers}/{pair.matches}"
        )
    print("forward_fit:")
    print(json.dumps(result.forward_fit, indent=2))
    print("turn_fit:")
    print(json.dumps(result.turn_fit, indent=2))
    print(json.dumps(asdict(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
