#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from implementations.perception import VlmPrepConfig, prepare_vlm_artifacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare camera frames for VLM/CV inspection.")
    parser.add_argument("input", help="Input image path.")
    parser.add_argument("--out-dir", default=None, help="Output directory. Defaults to <input parent>/vlm-prep.")
    parser.add_argument("--prefix", default=None, help="Output filename prefix. Defaults to input stem.")

    parser.add_argument("--clahe-clip-limit", type=float, default=2.5)
    parser.add_argument("--clahe-tile-grid", type=int, default=8)
    parser.add_argument("--blur-kernel", type=int, default=3)

    parser.add_argument("--shadow-clahe-clip", type=float, default=2.4)
    parser.add_argument("--shadow-gamma", type=float, default=0.78)

    parser.add_argument("--bilateral-passes", type=int, default=3)
    parser.add_argument("--bilateral-d", type=int, default=9)
    parser.add_argument("--bilateral-sigma-color", type=float, default=85.0)
    parser.add_argument("--bilateral-sigma-space", type=float, default=85.0)

    parser.add_argument("--stylize", action="store_true", help="Also emit a stylized shadow-lifted rendering.")
    parser.add_argument("--stylization-sigma-s", type=float, default=85.0)
    parser.add_argument("--stylization-sigma-r", type=float, default=0.45)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).resolve()
    image = cv2.imread(str(input_path))
    if image is None:
        raise SystemExit(f"could not read image: {input_path}")

    out_dir = Path(args.out_dir).resolve() if args.out_dir else input_path.parent / "vlm-prep"
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix or input_path.stem

    config = VlmPrepConfig(
        clahe_clip_limit=args.clahe_clip_limit,
        clahe_tile_grid=args.clahe_tile_grid,
        blur_kernel=args.blur_kernel,
        shadow_clahe_clip=args.shadow_clahe_clip,
        shadow_gamma=args.shadow_gamma,
        bilateral_passes=args.bilateral_passes,
        bilateral_d=args.bilateral_d,
        bilateral_sigma_color=args.bilateral_sigma_color,
        bilateral_sigma_space=args.bilateral_sigma_space,
        stylize=args.stylize,
        stylization_sigma_s=args.stylization_sigma_s,
        stylization_sigma_r=args.stylization_sigma_r,
    )
    outputs = prepare_vlm_artifacts(
        image,
        output_dir=out_dir,
        config=config,
        filename_prefix=prefix,
    )

    summary = {
        "input": str(input_path),
        "out_dir": str(out_dir),
        "config": asdict(config),
        "outputs": outputs,
    }
    summary_path = out_dir / f"{prefix}_prep_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary["summary"] = str(summary_path)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
