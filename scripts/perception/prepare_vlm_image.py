#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class VlmPrepConfig:
    clahe_clip_limit: float = 2.5
    clahe_tile_grid: int = 8
    blur_kernel: int = 3
    shadow_clahe_clip: float = 2.4
    shadow_gamma: float = 0.78
    bilateral_passes: int = 3
    bilateral_d: int = 9
    bilateral_sigma_color: float = 85.0
    bilateral_sigma_space: float = 85.0
    stylize: bool = False
    stylization_sigma_s: float = 85.0
    stylization_sigma_r: float = 0.45


def prepare_vlm_artifacts(
    image_bgr: np.ndarray,
    *,
    output_dir: Path,
    config: VlmPrepConfig | None = None,
    filename_prefix: str = "",
) -> dict[str, str]:
    active_config = config or VlmPrepConfig()
    output_dir.mkdir(parents=True, exist_ok=True)
    file_prefix = f"{filename_prefix}_" if filename_prefix else ""
    gray = _preprocess_gray(image_bgr, active_config)
    shadow_lifted = _lift_shadows(image_bgr, active_config)

    artifacts = {
        "preprocessed_gray": str(output_dir / f"{file_prefix}preprocessed_gray.png"),
        "shadow_lifted": str(output_dir / f"{file_prefix}shadow_lifted.png"),
    }
    cv2.imwrite(artifacts["preprocessed_gray"], gray)
    cv2.imwrite(artifacts["shadow_lifted"], shadow_lifted)

    if active_config.stylize:
        stylized = cv2.stylization(
            shadow_lifted,
            sigma_s=active_config.stylization_sigma_s,
            sigma_r=active_config.stylization_sigma_r,
        )
        artifacts["stylized_lifted"] = str(
            output_dir / f"{file_prefix}stylized_lifted.png"
        )
        cv2.imwrite(artifacts["stylized_lifted"], stylized)
    return artifacts


def _odd(value: int) -> int:
    value = max(1, int(value))
    return value if value % 2 == 1 else value + 1


def _preprocess_gray(image_bgr: np.ndarray, config: VlmPrepConfig) -> np.ndarray:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(
        clipLimit=config.clahe_clip_limit,
        tileGridSize=(config.clahe_tile_grid, config.clahe_tile_grid),
    )
    gray = clahe.apply(gray)
    if config.blur_kernel > 1:
        kernel = _odd(config.blur_kernel)
        gray = cv2.GaussianBlur(gray, (kernel, kernel), 0)
    return gray


def _smooth_image(image_bgr: np.ndarray, config: VlmPrepConfig) -> np.ndarray:
    out = image_bgr.copy()
    for _ in range(max(0, config.bilateral_passes)):
        out = cv2.bilateralFilter(
            out,
            d=_odd(config.bilateral_d),
            sigmaColor=config.bilateral_sigma_color,
            sigmaSpace=config.bilateral_sigma_space,
        )
    return out


def _lift_shadows(image_bgr: np.ndarray, config: VlmPrepConfig) -> np.ndarray:
    smoothed = _smooth_image(image_bgr, config)
    lab = cv2.cvtColor(smoothed, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=config.shadow_clahe_clip, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    gamma = max(0.1, float(config.shadow_gamma))
    lookup = np.array([((i / 255.0) ** gamma) * 255.0 for i in range(256)], dtype=np.uint8)
    l_channel = cv2.LUT(l_channel, lookup)
    lifted = cv2.merge([l_channel, a_channel, b_channel])
    return cv2.cvtColor(lifted, cv2.COLOR_LAB2BGR)


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
