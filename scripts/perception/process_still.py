#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from implementations.vehicle.picar.defaults import get_default_local_car_base_url
from autonomy.perception.traversability import FloorPlaneConfig, process_still


def main() -> int:
    parser = argparse.ArgumentParser(description="Process one PiRacer camera still into floor-plane debug outputs.")
    parser.add_argument("--url", default=f"{get_default_local_car_base_url()}/frame.jpg",
                        help="JPEG endpoint to fetch when --input is not provided.")
    parser.add_argument("--input", help="Existing image file to process instead of fetching from --url.")
    parser.add_argument("--out-dir", default=None,
                        help="Output directory. Defaults to lab/runs/stills/<timestamp>.")
    parser.add_argument("--horizon", type=float, default=FloorPlaneConfig.horizon_ratio,
                        help="Image y-ratio where floor-plane projection begins.")
    parser.add_argument("--threshold", type=float, default=FloorPlaneConfig.floor_threshold,
                        help="Color distance threshold for likely-floor classification.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir or Path("lab") / "runs" / "stills" / dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.input:
        image_path = Path(args.input)
    else:
        image_path = out_dir / "source.jpg"
        response = requests.get(args.url, timeout=8)
        response.raise_for_status()
        image_path.write_bytes(response.content)

    config = FloorPlaneConfig(horizon_ratio=args.horizon, floor_threshold=args.threshold)
    result = process_still(image_path, out_dir, config)

    print(f"processed: {image_path}")
    print(f"out_dir: {out_dir}")
    print(f"floor_fraction_roi: {result.floor_fraction_roi:.3f}")
    print(f"occupied_fraction_roi: {result.occupied_fraction_roi:.3f}")
    print("outputs:")
    for name, path in result.output_files.items():
        print(f"  {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
