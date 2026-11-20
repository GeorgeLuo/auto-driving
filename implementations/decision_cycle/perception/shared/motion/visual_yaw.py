"""Scale-free monocular visual yaw estimation and turn-response fitting.

Uses forward-backward checked Lucas-Kanade optical flow and Scaramuzza's
1-point circular-arc epipolar constraint to recover vehicle yaw delta without
knowing camera height, camera pitch, room scale, or vehicle parameters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class CameraIntrinsics:
    """Pinhole + native-simulator radial distortion intrinsics (no extrinsics)."""

    width: int
    height: int
    vertical_fov_degrees: float = 70.0
    barrel_distortion: float = 0.12


@dataclass(frozen=True)
class FrameFlow:
    """Subpixel feature tracks between two grayscale frames."""

    pts_a: np.ndarray
    pts_b: np.ndarray
    num_detected: int
    num_tracked: int
    median_px_shift: float


@dataclass(frozen=True)
class VisualYawEstimate:
    """Scale-free angular displacement and rate estimated from one image pair."""

    yaw_delta_rad: float | None
    yaw_rate_rad_s: float | None
    pitch_down_rad: float | None
    confident: bool
    mode: str
    num_tracks: int
    inlier_ratio: float


@dataclass(frozen=True)
class TurnObservation:
    """One constant-control turning observation in Automa sign conventions (+right, +forward)."""

    signed_throttle: float
    steering: float
    yaw_rate_rad_s: float


@dataclass(frozen=True)
class TurnResponseEstimate:
    """Identified turn-rate response: omega = gain_per_s * signed_throttle * tan(max_angle * steering)."""

    kind: str
    gain_per_s: float
    max_angle_rad: float
    turn_sign: str
    sample_count: int
    distinct_steering_count: int
    median_residual_rad_s: float | None
    confidence: float

    def predict_yaw_rate(self, steering: float, signed_throttle: float) -> float:
        if self.kind == "ackermann_tan":
            return self.gain_per_s * signed_throttle * math.tan(self.max_angle_rad * steering)
        if self.kind == "linear":
            return self.gain_per_s * signed_throttle * steering
        return 0.0


def rgb_to_working_gray(rgb: np.ndarray, *, working_width: int = 320) -> np.ndarray:
    """Convert uint8 RGB image to contiguous uint8 grayscale resized to working_width."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape[:2]
    needs_downscale = working_width > 0 and width > working_width
    if needs_downscale:
        scale = working_width / float(width)
        target_height = max(1, int(round(height * scale)))
        gray = cv2.resize(gray, (working_width, target_height), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(gray, dtype=np.uint8)


def undistort_pixels(pts: np.ndarray, intrinsics: CameraIntrinsics) -> np.ndarray:
    """Map distorted pixel coordinates (x, y) to OpenCV normalized pinhole plane (+X right, +Y down)."""
    if len(pts) == 0:
        return np.empty((0, 2), dtype=np.float64)
    coords = np.asarray(pts, dtype=np.float64)
    width = float(intrinsics.width)
    height = float(intrinsics.height)
    tan_half = math.tan(math.radians(intrinsics.vertical_fov_degrees) / 2.0)
    nx = 2.0 * (coords[:, 0] + 0.5) / width - 1.0
    ny = 2.0 * (coords[:, 1] + 0.5) / height - 1.0
    factor = 1.0 + intrinsics.barrel_distortion * (nx * nx + ny * ny)
    px = nx * factor * (width / height) * tan_half
    py_down = ny * factor * tan_half
    return np.column_stack([px, py_down])


def track_pyrlk(
    gray_a: np.ndarray,
    gray_b: np.ndarray,
    *,
    max_corners: int = 240,
    quality_level: float = 0.008,
    min_distance: int = 8,
    fb_threshold_px: float = 1.0,
) -> FrameFlow:
    """Track subpixel keypoints with forward-backward Lucas-Kanade consistency."""
    corners = cv2.goodFeaturesToTrack(
        gray_a,
        maxCorners=max_corners,
        qualityLevel=quality_level,
        minDistance=min_distance,
        blockSize=7,
    )
    if corners is None or len(corners) == 0:  # pyright: ignore[reportUnnecessaryComparison]  # OpenCV returns None when 0 corners are found.
        empty = np.empty((0, 2), dtype=np.float32)
        return FrameFlow(empty, empty, 0, 0, 0.0)

    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)
    pts_b, st_fwd, _ = cv2.calcOpticalFlowPyrLK(
        gray_a, gray_b, corners, None, winSize=(21, 21), maxLevel=4, criteria=criteria
    )
    pts_back, st_bwd, _ = cv2.calcOpticalFlowPyrLK(
        gray_b, gray_a, pts_b, None, winSize=(21, 21), maxLevel=4, criteria=criteria
    )
    return _filter_flow(
        gray_a.shape,
        corners,
        pts_b,
        pts_back,
        st_fwd=st_fwd,
        st_bwd=st_bwd,
        fb_threshold_px=fb_threshold_px,
    )


def _filter_flow(
    shape: tuple[int, ...],
    corners: np.ndarray,
    pts_b: np.ndarray,
    pts_back: np.ndarray,
    *,
    st_fwd: np.ndarray,
    st_bwd: np.ndarray,
    fb_threshold_px: float,
) -> FrameFlow:
    height, width = shape[0], shape[1]
    pa = corners.reshape(-1, 2)
    pb = pts_b.reshape(-1, 2)
    fb_err = np.linalg.norm(pa - pts_back.reshape(-1, 2), axis=1)
    in_bounds = (pb[:, 0] >= 1) & (pb[:, 0] < width - 1) & (pb[:, 1] >= 1) & (pb[:, 1] < height - 1)
    keep = (st_fwd.ravel() == 1) & (st_bwd.ravel() == 1) & (fb_err <= fb_threshold_px) & in_bounds
    kept_a = pa[keep].astype(np.float32)
    kept_b = pb[keep].astype(np.float32)
    shift = float(np.median(np.linalg.norm(kept_b - kept_a, axis=1))) if len(kept_a) > 0 else 0.0
    return FrameFlow(kept_a, kept_b, int(len(pa)), int(len(kept_a)), shift)


def _rotate_pitch(u1: np.ndarray, u2: np.ndarray, phi: float) -> tuple[np.ndarray, np.ndarray]:
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    w1 = np.column_stack([u1[:, 0], cos_p * u1[:, 1] + sin_p * u1[:, 2], -sin_p * u1[:, 1] + cos_p * u1[:, 2]])
    w2 = np.column_stack([u2[:, 0], cos_p * u2[:, 1] + sin_p * u2[:, 2], -sin_p * u2[:, 1] + cos_p * u2[:, 2]])
    return w1, w2


def _score_pitch_hypothesis(
    u1: np.ndarray,
    u2: np.ndarray,
    phi: float,
    sampson_thresh: float,
) -> tuple[float, float, float, np.ndarray]:
    w1, w2 = _rotate_pitch(u1, u2, phi)
    a_term = w2[:, 0] * w1[:, 1] - w2[:, 1] * w1[:, 0]
    b_term = w2[:, 1] * w1[:, 2] + w2[:, 2] * w1[:, 1]
    good = (np.abs(w1[:, 1]) > 0.03) & (np.abs(w2[:, 1]) > 0.03) & (np.hypot(a_term, b_term) > 0.03)
    if int(np.sum(good)) < 8:
        theta = -float(np.median(np.arctan2(w2[:, 0], w2[:, 2]) - np.arctan2(w1[:, 0], w1[:, 2])))
    else:
        thetas = 2.0 * np.arctan2(-a_term[good], b_term[good])
        theta = float(np.median(np.arctan2(np.sin(thetas), np.cos(thetas))))

    cos_h, sin_h = math.cos(theta / 2.0), math.sin(theta / 2.0)
    residual = cos_h * a_term + sin_h * b_term
    grad_sq = (
        (cos_h * w1[:, 1]) ** 2
        + (-cos_h * w1[:, 0] + sin_h * w1[:, 2]) ** 2
        + (-cos_h * w2[:, 1]) ** 2
        + (cos_h * w2[:, 0] + sin_h * w2[:, 2]) ** 2
    )
    sampson = np.abs(residual) / np.sqrt(grad_sq + 1e-12)
    loss = float(np.mean(np.minimum(sampson, sampson_thresh) ** 2))
    inlier_ratio = float(np.mean(sampson <= sampson_thresh))
    return loss, theta, inlier_ratio, sampson


def _refine_theta_svd(
    u1: np.ndarray,
    u2: np.ndarray,
    phi: float,
    *,
    theta_init: float,
    sampson: np.ndarray,
    sampson_thresh: float,
) -> float:
    w1, w2 = _rotate_pitch(u1, u2, phi)
    inliers = (sampson <= sampson_thresh) & (np.abs(w1[:, 1]) > 0.03) & (np.abs(w2[:, 1]) > 0.03)
    if int(np.sum(inliers)) < 8:
        return theta_init
    a_term = w2[inliers, 0] * w1[inliers, 1] - w2[inliers, 1] * w1[inliers, 0]
    b_term = w2[inliers, 1] * w1[inliers, 2] + w2[inliers, 2] * w1[inliers, 1]
    _, _, vt = np.linalg.svd(np.column_stack([a_term, b_term]), full_matrices=False)
    vec = vt[-1]
    cand = _wrap_angle(2.0 * math.atan2(float(vec[1]), float(vec[0])))
    if abs(cand - theta_init) > 0.25:
        cand = _wrap_angle(2.0 * math.atan2(-float(vec[1]), -float(vec[0])))
    return cand if abs(cand - theta_init) <= 0.15 else theta_init


def _wrap_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def estimate_circular_arc_yaw(
    flow: FrameFlow,
    intrinsics: CameraIntrinsics,
    dt_s: float,
    *,
    min_tracks: int = 15,
    min_inlier_ratio: float = 0.50,
    max_step_yaw_rad: float = 0.65,
    sampson_thresh: float = 0.008,
) -> VisualYawEstimate:
    """Estimate vehicle yaw (+right) via 1-point circular-arc epipolar geometry with unknown pitch."""
    if flow.num_tracked < min_tracks:
        return VisualYawEstimate(None, None, None, False, "abstain_low_tracks", flow.num_tracked, 0.0)
    if dt_s <= 0.0:
        return VisualYawEstimate(None, None, None, False, "abstain_invalid_dt", flow.num_tracked, 0.0)

    na = undistort_pixels(flow.pts_a, intrinsics)
    nb = undistort_pixels(flow.pts_b, intrinsics)
    u1 = np.column_stack([na, np.ones(len(na))])
    u2 = np.column_stack([nb, np.ones(len(nb))])

    best = _search_pitch(u1, u2, sampson_thresh)
    phi_hat, theta_init, inlier_ratio, sampson = best
    theta_hat = _refine_theta_svd(
        u1, u2, phi_hat, theta_init=theta_init, sampson=sampson, sampson_thresh=sampson_thresh
    )
    if theta_hat == 0.0:
        theta_hat = 0.0

    inlier_count = int(round(inlier_ratio * len(na)))
    confident = _is_confident_arc_estimate(
        inlier_ratio=inlier_ratio,
        inlier_count=inlier_count,
        theta_hat=theta_hat,
        min_inlier_ratio=min_inlier_ratio,
        max_step_yaw_rad=max_step_yaw_rad,
    )
    if not confident:
        return VisualYawEstimate(
            None, None, phi_hat, False, "abstain_low_inliers", flow.num_tracked, inlier_ratio
        )
    return VisualYawEstimate(
        theta_hat, theta_hat / dt_s, phi_hat, True, "circular_arc_1pt", flow.num_tracked, inlier_ratio
    )


def _is_confident_arc_estimate(
    *,
    inlier_ratio: float,
    inlier_count: int,
    theta_hat: float,
    min_inlier_ratio: float,
    max_step_yaw_rad: float,
) -> bool:
    return (
        inlier_ratio >= min_inlier_ratio
        and inlier_count >= 12
        and abs(theta_hat) <= max_step_yaw_rad
    )


def _search_pitch(
    u1: np.ndarray,
    u2: np.ndarray,
    sampson_thresh: float,
) -> tuple[float, float, float, np.ndarray]:
    best_loss = float("inf")
    best_result = (0.0, 0.0, 0.0, np.empty(0, dtype=np.float64))
    for phi in np.linspace(-0.05, 0.35, 21):
        loss, theta, inliers, sampson = _score_pitch_hypothesis(u1, u2, float(phi), sampson_thresh)
        if loss < best_loss:
            best_loss = loss
            best_result = (float(phi), theta, inliers, sampson)

    phi0 = best_result[0]
    for phi in np.linspace(max(-0.08, phi0 - 0.025), min(0.40, phi0 + 0.025), 11):
        loss, theta, inliers, sampson = _score_pitch_hypothesis(u1, u2, float(phi), sampson_thresh)
        if loss < best_loss:
            best_loss = loss
            best_result = (float(phi), theta, inliers, sampson)
    return best_result


def fit_turn_response(observations: tuple[TurnObservation, ...] | list[TurnObservation]) -> TurnResponseEstimate:
    """Fit signed omega = gain * signed_throttle * tan(max_angle * steering) in Automa sign convention."""
    active = [
        obs
        for obs in observations
        if abs(obs.signed_throttle) > 1e-4 and abs(obs.steering) > 1e-4
    ]
    if len(active) < 2:
        return TurnResponseEstimate("abstain", 0.0, 0.0, "unknown", len(active), 0, None, 0.0)

    st = np.array([obs.signed_throttle for obs in active], dtype=np.float64)
    us = np.array([obs.steering for obs in active], dtype=np.float64)
    omega = np.array([obs.yaw_rate_rad_s for obs in active], dtype=np.float64)
    abs_steers = np.sort(np.unique(np.round(np.abs(us), 3)))
    distinct_count = int(len(abs_steers))
    spread = float(abs_steers[-1] - abs_steers[0]) if distinct_count >= 2 else 0.0
    max_steer = float(abs_steers[-1]) if distinct_count >= 1 else 0.0
    can_fit_nonlinear = distinct_count >= 2 and spread >= 0.30 and max_steer >= 0.65

    if not can_fit_nonlinear:
        return _fit_linear_response(st, us, omega, distinct_count)
    return _fit_ackermann_response(st, us, omega, distinct_count)


def _fit_linear_response(
    st: np.ndarray,
    us: np.ndarray,
    omega: np.ndarray,
    distinct_count: int,
) -> TurnResponseEstimate:
    basis = st * us
    denom = float(np.dot(basis, basis))
    if denom <= 1e-9:
        return TurnResponseEstimate("abstain", 0.0, 0.0, "unknown", len(omega), distinct_count, None, 0.0)
    gain = float(np.dot(basis, omega) / denom)
    median_res = float(np.median(np.abs(gain * basis - omega)))
    conf = _response_confidence(len(omega), distinct_count, median_res, nonlinear=False)
    return TurnResponseEstimate(
        "linear", gain, 0.0, _turn_sign_label(gain), len(omega), distinct_count, median_res, conf
    )


def _fit_ackermann_response(
    st: np.ndarray,
    us: np.ndarray,
    omega: np.ndarray,
    distinct_count: int,
) -> TurnResponseEstimate:
    _, b0, _ = _grid_search_max_angle(st, us, omega, np.linspace(0.15, 1.22, 108))
    gain, max_angle, _ = _grid_search_max_angle(
        st, us, omega, np.linspace(max(0.10, b0 - 0.03), min(1.25, b0 + 0.03), 121)
    )
    pred = gain * st * np.tan(max_angle * us)
    median_res = float(np.median(np.abs(pred - omega)))
    conf = _response_confidence(len(omega), distinct_count, median_res, nonlinear=True)
    return TurnResponseEstimate(
        "ackermann_tan",
        gain,
        max_angle,
        _turn_sign_label(gain),
        len(omega),
        distinct_count,
        median_res,
        conf,
    )


def _grid_search_max_angle(
    st: np.ndarray,
    us: np.ndarray,
    omega: np.ndarray,
    grid: np.ndarray,
) -> tuple[float, float, float]:
    best_err = float("inf")
    best_gain = 0.0
    best_angle = 0.65
    for angle in grid:
        basis = st * np.tan(float(angle) * us)
        denom = float(np.dot(basis, basis))
        if denom < 1e-12:
            continue
        gain = float(np.dot(basis, omega) / denom)
        diff = np.abs(gain * basis - omega)
        err = float(np.mean(np.where(diff < 0.25, 0.5 * diff * diff, 0.25 * (diff - 0.125))))
        if err < best_err:
            best_err = err
            best_gain = gain
            best_angle = float(angle)
    return best_gain, best_angle, best_err


def _turn_sign_label(gain: float) -> str:
    if abs(gain) <= 1e-4:
        return "unknown"
    return "normal" if gain > 0.0 else "inverted"


def _response_confidence(
    sample_count: int,
    distinct_count: int,
    median_residual: float,
    *,
    nonlinear: bool,
) -> float:
    sample_term = min(1.0, sample_count / 12.0)
    excitation_term = 1.0 if nonlinear and distinct_count >= 2 else 0.65
    fit_term = 1.0 / (1.0 + median_residual / 0.25)
    score = 0.45 * sample_term + 0.20 * excitation_term + 0.35 * fit_term
    return round(max(0.0, min(1.0, score)), 5)
