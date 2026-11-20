"""Scale-free monocular visual yaw estimation and turn-response fitting.

Uses forward-backward checked Lucas-Kanade optical flow (with an ORB descriptor
fallback for large inter-frame shifts) and Scaramuzza's 1-point circular-arc
epipolar constraint to recover vehicle yaw delta without knowing camera height,
camera pitch, room scale, or vehicle parameters.
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
    """Feature correspondences between two grayscale frames."""

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
    median_px_shift: float = 0.0


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


def track_orb_matches(
    gray_a: np.ndarray,
    gray_b: np.ndarray,
    *,
    max_features: int = 300,
    lowe_ratio: float = 0.78,
) -> FrameFlow:
    """Descriptor-based ORB matching for large rotations and close-range translations."""
    orb = cv2.ORB_create(nfeatures=max_features, fastThreshold=10)  # pyright: ignore[reportAttributeAccessIssue]
    kp_a, des_a = orb.detectAndCompute(gray_a, None)
    kp_b, des_b = orb.detectAndCompute(gray_b, None)
    empty = np.empty((0, 2), dtype=np.float32)
    if des_a is None or des_b is None or len(kp_a) < 2 or len(kp_b) < 2:
        return FrameFlow(empty, empty, len(kp_a), 0, 0.0)

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    raw = matcher.knnMatch(des_a, des_b, k=2)
    pts_a_list: list[tuple[float, float]] = []
    pts_b_list: list[tuple[float, float]] = []
    for pair in raw:
        if len(pair) < 2:
            continue
        first, second = pair[0], pair[1]
        if first.distance < lowe_ratio * second.distance:
            pts_a_list.append(kp_a[first.queryIdx].pt)
            pts_b_list.append(kp_b[first.trainIdx].pt)
    if not pts_a_list:
        return FrameFlow(empty, empty, len(kp_a), 0, 0.0)

    pts_a = np.asarray(pts_a_list, dtype=np.float32)
    pts_b = np.asarray(pts_b_list, dtype=np.float32)
    shift = float(np.median(np.linalg.norm(pts_b - pts_a, axis=1)))
    return FrameFlow(pts_a, pts_b, len(kp_a), len(pts_a), shift)


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
    max_step_yaw_rad: float = 0.90,
    sampson_thresh: float = 0.008,
    mode_label: str = "circular_arc_1pt",
) -> VisualYawEstimate:
    """Estimate vehicle yaw (+right) via 1-point circular-arc epipolar geometry with unknown pitch."""
    if flow.num_tracked < min_tracks:
        return VisualYawEstimate(
            None, None, None, False, "abstain_low_tracks", flow.num_tracked, 0.0, flow.median_px_shift
        )
    if dt_s <= 0.0:
        return VisualYawEstimate(
            None, None, None, False, "abstain_invalid_dt", flow.num_tracked, 0.0, flow.median_px_shift
        )

    na = undistort_pixels(flow.pts_a, intrinsics)
    nb = undistort_pixels(flow.pts_b, intrinsics)
    u1 = np.column_stack([na, np.ones(len(na))])
    u2 = np.column_stack([nb, np.ones(len(nb))])

    phi_hat, theta_init, inlier_ratio, sampson = _search_pitch(u1, u2, sampson_thresh)
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
            None, None, phi_hat, False, "abstain_low_inliers", flow.num_tracked, inlier_ratio, flow.median_px_shift
        )
    return VisualYawEstimate(
        theta_hat, theta_hat / dt_s, phi_hat, True, mode_label, flow.num_tracked, inlier_ratio, flow.median_px_shift
    )


def estimate_visual_yaw(
    gray_a: np.ndarray,
    gray_b: np.ndarray,
    intrinsics: CameraIntrinsics,
    dt_s: float,
    *,
    min_tracks: int = 15,
    min_inlier_ratio: float = 0.50,
    max_step_yaw_rad: float = 0.90,
) -> VisualYawEstimate:
    """Estimate visual yaw using PyrLK first, falling back to ORB matching for large inter-frame shifts."""
    lk_flow = track_pyrlk(gray_a, gray_b)
    lk_est = estimate_circular_arc_yaw(
        lk_flow,
        intrinsics,
        dt_s,
        min_tracks=min_tracks,
        min_inlier_ratio=min_inlier_ratio,
        max_step_yaw_rad=max_step_yaw_rad,
        sampson_thresh=0.008,
        mode_label="circular_arc_1pt",
    )
    if lk_est.confident or lk_flow.num_detected < min_tracks:
        return lk_est

    orb_flow = track_orb_matches(gray_a, gray_b)
    orb_est = estimate_circular_arc_yaw(
        orb_flow,
        intrinsics,
        dt_s,
        min_tracks=min_tracks,
        min_inlier_ratio=max(0.45, min_inlier_ratio - 0.05),
        max_step_yaw_rad=max_step_yaw_rad,
        sampson_thresh=0.012,
        mode_label="circular_arc_orb",
    )
    return orb_est if orb_est.confident else lk_est


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
    """Fit signed omega = gain * signed_throttle * tan(max_angle * steering) robustly against blocked turns."""
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

    sign = _dominant_turn_sign(st, us, omega)
    if sign == 0.0:
        return TurnResponseEstimate("abstain", 0.0, 0.0, "unknown", len(active), 0, None, 0.0)

    # Filter out near-zero / wrong-sign observations, then reject partial contacts within each steering bin.
    linear_gains = (omega / (st * us)) * sign
    keep = linear_gains > 0.05
    if int(np.sum(keep)) < 2:
        return TurnResponseEstimate("abstain", 0.0, 0.0, "unknown", len(active), 0, None, 0.0)
    st, us, omega, linear_gains = st[keep], us[keep], omega[keep], linear_gains[keep]
    st, us, omega, linear_gains = _filter_per_bin_outliers(st, us, omega, linear_gains)

    inlier_mask = _upper_consensus_mask(linear_gains, rel_tol=0.35)
    clean_us = us[inlier_mask]
    distinct_count = int(len(np.unique(np.round(np.abs(clean_us), 3))))
    if not _has_nonlinear_excitation(clean_us):
        return _fit_linear_response(st, us, omega, sign=sign, distinct_count=distinct_count)
    return _fit_ackermann_response(st, us, omega, sign=sign, fallback_distinct=distinct_count)


def _filter_per_bin_outliers(
    st: np.ndarray,
    us: np.ndarray,
    omega: np.ndarray,
    linear_gains: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    abs_bins = np.round(np.abs(us), 2)
    keep = np.ones(len(omega), dtype=bool)
    for steer_bin in np.unique(abs_bins):
        idx = np.flatnonzero(abs_bins == steer_bin)
        if len(idx) >= 3:
            sub_mask = _upper_consensus_mask(linear_gains[idx], rel_tol=0.20)
            keep[idx[~sub_mask]] = False
    if int(np.sum(keep)) < 2:
        return st, us, omega, linear_gains
    return st[keep], us[keep], omega[keep], linear_gains[keep]


def _has_nonlinear_excitation(us: np.ndarray) -> bool:
    if len(us) < 4:
        return False
    abs_steers = np.sort(np.unique(np.round(np.abs(us), 3)))
    if len(abs_steers) < 2:
        return False
    return float(abs_steers[-1] - abs_steers[0]) >= 0.30 and float(abs_steers[-1]) >= 0.65


def _dominant_turn_sign(st: np.ndarray, us: np.ndarray, omega: np.ndarray) -> float:
    signed_gains = omega / (st * us)
    nonzero = signed_gains[np.abs(signed_gains) > 0.05]
    if len(nonzero) == 0:
        return 0.0
    abs_nz = np.abs(nonzero)
    top = nonzero[abs_nz >= float(np.median(abs_nz))]
    med = float(np.median(top))
    return 1.0 if med > 0.0 else -1.0 if med < 0.0 else 0.0


def _upper_consensus_mask(values: np.ndarray, *, rel_tol: float) -> np.ndarray:
    """Select the highest-magnitude supported cluster (contact/drag only slows turns down)."""
    n = len(values)
    if n <= 2:
        return np.ones(n, dtype=bool)
    min_support = max(2, min(4, int(math.ceil(0.25 * n))))
    centers = np.sort(values)[::-1]
    centers = centers[centers > 0.0]
    if len(centers) == 0:
        return np.ones(n, dtype=bool)
    masks = np.abs(values[None, :] - centers[:, None]) <= (rel_tol * centers[:, None])
    valid = np.flatnonzero(masks.sum(axis=1) >= min_support)
    if len(valid) == 0:
        return np.ones(n, dtype=bool)
    return masks[int(valid[0])]


def _fit_linear_response(
    st: np.ndarray,
    us: np.ndarray,
    omega: np.ndarray,
    *,
    sign: float,
    distinct_count: int,
) -> TurnResponseEstimate:
    gains = (omega / (st * us)) * sign
    mask = _upper_consensus_mask(gains, rel_tol=0.22)
    gain = float(np.median(gains[mask])) * sign
    pred = gain * st[mask] * us[mask]
    median_res = float(np.median(np.abs(pred - omega[mask])))
    inlier_count = int(np.sum(mask))
    conf = _response_confidence(inlier_count, distinct_count, median_res, nonlinear=False)
    return TurnResponseEstimate(
        "linear", gain, 0.0, _turn_sign_label(gain), inlier_count, distinct_count, median_res, conf
    )


def _fit_ackermann_response(
    st: np.ndarray,
    us: np.ndarray,
    omega: np.ndarray,
    *,
    sign: float,
    fallback_distinct: int,
) -> TurnResponseEstimate:
    gain0, b0, mask0 = _grid_search_max_angle(st, us, omega, sign=sign, grid=np.linspace(0.15, 1.15, 41))
    if gain0 == 0.0 or not _has_nonlinear_excitation(us[mask0]):
        return _fit_linear_response(st, us, omega, sign=sign, distinct_count=fallback_distinct)
    gain, max_angle, mask = _grid_search_max_angle(
        st, us, omega, sign=sign, grid=np.linspace(max(0.10, b0 - 0.03), min(1.20, b0 + 0.03), 25)
    )
    if gain == 0.0 or not _has_nonlinear_excitation(us[mask]):
        return _fit_linear_response(st, us, omega, sign=sign, distinct_count=fallback_distinct)
    clean_us = us[mask]
    distinct_count = int(len(np.unique(np.round(np.abs(clean_us), 3))))
    pred = gain * st[mask] * np.tan(max_angle * clean_us)
    median_res = float(np.median(np.abs(pred - omega[mask])))
    inlier_count = int(np.sum(mask))
    conf = _response_confidence(inlier_count, distinct_count, median_res, nonlinear=True)
    return TurnResponseEstimate(
        "ackermann_tan",
        gain,
        max_angle,
        _turn_sign_label(gain),
        inlier_count,
        distinct_count,
        median_res,
        conf,
    )


def _small_median(values: np.ndarray) -> float:
    k = len(values)
    if k == 1:
        return float(values[0])
    if k == 2:
        return 0.5 * (float(values[0]) + float(values[1]))
    ordered = sorted(values.tolist())
    mid = k // 2
    return ordered[mid] if k % 2 == 1 else 0.5 * (ordered[mid - 1] + ordered[mid])


def _bin_weighted_gain_and_score(
    implied: np.ndarray,
    mask: np.ndarray,
    bin_steers: list[float],
    bin_indices: list[np.ndarray],
) -> tuple[float, float] | None:
    inlier_count = int(mask.sum())
    if inlier_count < 4:
        return None
    meds: list[float] = []
    steers: list[float] = []
    weights: list[float] = []
    for steer, idx in zip(bin_steers, bin_indices, strict=True):
        sub_vals = implied[idx][mask[idx]]
        count = len(sub_vals)
        if count > 0:
            steers.append(steer)
            weights.append(math.sqrt(min(count, 4)))
            meds.append(_small_median(sub_vals))
    if len(steers) < 2 or (steers[-1] - steers[0]) < 0.30 or steers[-1] < 0.65:
        return None
    w_sum = sum(weights)
    gain_mag = sum(w * v for w, v in zip(weights, meds, strict=True)) / w_sum
    if gain_mag <= 0.0:
        return None
    bin_err = sum(w * abs(v - gain_mag) for w, v in zip(weights, meds, strict=True)) / (w_sum * gain_mag)
    coverage_penalty = 0.05 * (1.0 - inlier_count / len(implied))
    return gain_mag, bin_err + coverage_penalty


def _grid_search_max_angle(
    st: np.ndarray,
    us: np.ndarray,
    omega: np.ndarray,
    *,
    sign: float,
    grid: np.ndarray,
) -> tuple[float, float, np.ndarray]:
    abs_bins = np.round(np.abs(us), 2)
    uniq_bins = np.unique(abs_bins)
    bin_steers = [float(b) for b in uniq_bins]
    bin_indices = [np.flatnonzero(abs_bins == b) for b in uniq_bins]

    tan_mat = np.tan(grid[:, None] * us[None, :])
    implied_mat = (omega[None, :] / (st[None, :] * tan_mat)) * sign

    best_score = float("inf")
    best_gain = 0.0
    best_angle = 0.65
    best_mask = np.zeros(len(omega), dtype=bool)
    for row_idx, angle in enumerate(grid.tolist()):
        implied = implied_mat[row_idx]
        mask = _upper_consensus_mask(implied, rel_tol=0.18)
        evaluated = _bin_weighted_gain_and_score(implied, mask, bin_steers, bin_indices)
        if evaluated is None:
            continue
        gain_mag, score = evaluated
        if score < best_score:
            best_score = score
            best_gain = gain_mag * sign
            best_angle = float(angle)
            best_mask = mask
    return best_gain, best_angle, best_mask


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
