from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .feature_tracking import FeatureTrackingResult, track_features


@dataclass
class PairTrackingSummary:
    pair: str
    source_step: int
    target_step: int
    bbox_width_ratio: float | None
    bbox_center_shift_px: list[float] | None
    feature_scale: float | None
    feature_center_shift_px: list[float] | None
    inliers: int
    matches: int
    matches_image: str


@dataclass
class TrackedSequenceSummary:
    pair_count: int
    pairs: list[PairTrackingSummary]
    forward_fit: dict[str, float | None]
    turn_fit: dict[str, float | None]


def analyze_tracked_sequence(
    observations: list[dict[str, Any]],
    out_dir: str | Path,
    *,
    search_radius: int = 80,
    max_features: int = 80,
    min_score: float = 0.72,
) -> TrackedSequenceSummary:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    pairs: list[PairTrackingSummary] = []
    tracking_results: list[FeatureTrackingResult] = []
    usable_observations = [obs for obs in observations if obs.get("image") and obs.get("bbox")]

    for source, target in zip(usable_observations, usable_observations[1:]):
        pair_name = f"{int(source['step']):02d}_{int(target['step']):02d}"
        result = track_features(
            source["image"],
            target["image"],
            source["bbox"],
            out_path / pair_name,
            search_radius=search_radius,
            max_features=max_features,
            min_score=min_score,
        )
        tracking_results.append(result)
        pairs.append(make_pair_summary(source, target, result))

    summary = TrackedSequenceSummary(
        pair_count=len(pairs),
        pairs=pairs,
        forward_fit=fit_forward_from_feature_scales(usable_observations, pairs),
        turn_fit=fit_turn_from_feature_shifts(usable_observations, pairs),
    )
    (out_path / "summary.json").write_text(json.dumps(asdict(summary), indent=2), encoding="utf-8")
    return summary


def make_pair_summary(source: dict[str, Any], target: dict[str, Any],
                      result: FeatureTrackingResult) -> PairTrackingSummary:
    bbox_width_ratio = None
    if source.get("width_px") and target.get("width_px"):
        bbox_width_ratio = float(target["width_px"] / source["width_px"])

    bbox_center_shift = None
    if source.get("center_px") and target.get("center_px"):
        bbox_center_shift = [
            float(target["center_px"][0] - source["center_px"][0]),
            float(target["center_px"][1] - source["center_px"][1]),
        ]

    return PairTrackingSummary(
        pair=f"{source['step']}->{target['step']}",
        source_step=int(source["step"]),
        target_step=int(target["step"]),
        bbox_width_ratio=bbox_width_ratio,
        bbox_center_shift_px=bbox_center_shift,
        feature_scale=result.scale,
        feature_center_shift_px=result.center_shift_px,
        inliers=result.inlier_count,
        matches=result.match_count,
        matches_image=result.output_files["matches"],
    )


def fit_forward_from_feature_scales(
    observations: list[dict[str, Any]],
    pairs: list[PairTrackingSummary],
) -> dict[str, float | None]:
    if len(observations) < 2 or not observations[0].get("width_px"):
        return empty_forward_fit()

    cumulative_scale = 1.0
    steps = [float(observations[0]["step"])]
    width_proxy = [float(observations[0]["width_px"])]

    for pair in pairs:
        if pair.feature_scale is None or pair.feature_scale <= 0:
            return empty_forward_fit()
        cumulative_scale *= pair.feature_scale
        steps.append(float(pair.target_step))
        width_proxy.append(float(observations[0]["width_px"]) * cumulative_scale)

    if len(width_proxy) < 2:
        return empty_forward_fit()

    steps_arr = np.asarray(steps, dtype=np.float64)
    inv_widths = 1.0 / np.asarray(width_proxy, dtype=np.float64)
    slope, intercept = np.polyfit(steps_arr, inv_widths, 1)
    predicted = np.polyval([slope, intercept], steps_arr)
    r2 = coefficient_of_determination(inv_widths, predicted)
    if slope >= 0:
        return empty_forward_fit() | {"tracked_inverse_width_r2": r2}

    return {
        "tracked_initial_distance_steps": float(intercept / -slope),
        "tracked_focal_width_product_px_steps": float(-1.0 / slope),
        "tracked_inverse_width_r2": r2,
        "tracked_final_width_proxy_px": float(width_proxy[-1]),
    }


def fit_turn_from_feature_shifts(
    observations: list[dict[str, Any]],
    pairs: list[PairTrackingSummary],
) -> dict[str, float | None]:
    if len(observations) < 2 or not pairs:
        return empty_turn_fit()

    cumulative_x = [0.0]
    steps = [float(observations[0]["step"])]
    for pair in pairs:
        if not pair.feature_center_shift_px:
            return empty_turn_fit()
        cumulative_x.append(cumulative_x[-1] + float(pair.feature_center_shift_px[0]))
        steps.append(float(pair.target_step))

    steps_arr = np.asarray(steps, dtype=np.float64)
    x_arr = np.asarray(cumulative_x, dtype=np.float64)
    slope, intercept = np.polyfit(steps_arr, x_arr, 1)
    predicted = np.polyval([slope, intercept], steps_arr)
    r2 = coefficient_of_determination(x_arr, predicted)

    image_width = Image.open(observations[0]["image"]).size[0]
    camera_width_turn_steps = None
    if abs(slope) > 1e-6:
        camera_width_turn_steps = float(image_width / abs(slope))

    return {
        "tracked_center_shift_px_per_turn_step": float(slope),
        "tracked_linearized_camera_width_turn_steps": camera_width_turn_steps,
        "tracked_center_shift_r2": r2,
        "tracked_final_center_shift_px": float(cumulative_x[-1]),
    }


def coefficient_of_determination(values: np.ndarray, predicted: np.ndarray) -> float:
    ss_res = float(((values - predicted) ** 2).sum())
    ss_tot = float(((values - values.mean()) ** 2).sum())
    return 1.0 - ss_res / ss_tot if ss_tot else 1.0


def empty_forward_fit() -> dict[str, float | None]:
    return {
        "tracked_initial_distance_steps": None,
        "tracked_focal_width_product_px_steps": None,
        "tracked_inverse_width_r2": None,
        "tracked_final_width_proxy_px": None,
    }


def empty_turn_fit() -> dict[str, float | None]:
    return {
        "tracked_center_shift_px_per_turn_step": None,
        "tracked_linearized_camera_width_turn_steps": None,
        "tracked_center_shift_r2": None,
        "tracked_final_center_shift_px": None,
    }
