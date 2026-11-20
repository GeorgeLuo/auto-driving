from __future__ import annotations

import json
import math
import unittest
from unittest import mock

import cv2
import numpy as np

from autonomy.decision_cycle.action.publication import write_host_inputs
from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.perception.plugin import PerceptionPluginWarmingUp
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlApplication
from autonomy.shared_memory import shared_memory_from_document, shared_memory_document
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from implementations.decision_cycle.catalog import selection_activation
from implementations.decision_cycle.perception.plugins.vehicle_dynamics.plugin import (
    VehicleDynamicsPlugin,
)
from implementations.decision_cycle.perception.shared.motion.visual_yaw import (
    CameraIntrinsics,
    FrameFlow,
    TurnObservation,
    VisualYawEstimate,
    estimate_circular_arc_yaw,
    estimate_visual_yaw,
    fit_throttle_response,
    fit_turn_response,
    rgb_to_working_gray,
    undistort_pixels,
)


def _rotation_y_down(angle_rad: float) -> np.ndarray:
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]], dtype=np.float64)


def _rotation_pitch_down(pitch_rad: float) -> np.ndarray:
    c, s = math.cos(pitch_rad), math.sin(pitch_rad)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]], dtype=np.float64)


def _distort_pinhole(pinhole: np.ndarray, intrinsics: CameraIntrinsics) -> np.ndarray:
    """Invert undistort_pixels numerically for test projection (Newton step)."""
    w, h = float(intrinsics.width), float(intrinsics.height)
    tan_half = math.tan(math.radians(intrinsics.vertical_fov_degrees) / 2.0)
    ux = pinhole[:, 0] / ((w / h) * tan_half)
    uy = pinhole[:, 1] / tan_half
    nx, ny = ux.copy(), uy.copy()
    k = intrinsics.barrel_distortion
    for _ in range(8):
        r2 = nx * nx + ny * ny
        f = 1.0 + k * r2
        nx = ux / f
        ny = uy / f
    return np.column_stack([(nx + 1.0) * 0.5 * w - 0.5, (ny + 1.0) * 0.5 * h - 0.5]).astype(np.float32)


def _project_planar_arc(
    pts_world: np.ndarray,
    *,
    yaw_right_rad: float,
    signed_distance: float,
    pitch_rad: float,
    intrinsics: CameraIntrinsics,
) -> FrameFlow:
    rp = _rotation_pitch_down(pitch_rad)
    r_w = _rotation_y_down(-yaw_right_rad)
    chord = signed_distance * np.array([math.sin(yaw_right_rad / 2.0), 0.0, math.cos(yaw_right_rad / 2.0)])
    x1 = (rp @ pts_world.T).T
    x2 = (rp @ (r_w @ (pts_world - chord).T)).T
    p1 = _distort_pinhole(x1[:, :2] / x1[:, 2:3], intrinsics)
    p2 = _distort_pinhole(x2[:, :2] / x2[:, 2:3], intrinsics)
    shift = float(np.median(np.linalg.norm(p2 - p1, axis=1)))
    return FrameFlow(p1, p2, len(p1), len(p1), shift)


def _textured_scene(width: int = 320, height: int = 240) -> np.ndarray:
    """Deterministic richly textured synthetic image."""
    rng = np.random.default_rng(7)
    base = rng.integers(35, 220, size=(height // 4, width // 4, 3), dtype=np.uint8)
    image = cv2.resize(base, (width, height), interpolation=cv2.INTER_LINEAR)
    for idx in range(0, width, 24):
        cv2.rectangle(image, (idx, 20 + (idx % 60)), (idx + 12, height - 20), (240, 210, 140), 2)
    return image


def _warp_pure_yaw(image: np.ndarray, yaw_right_rad: float, intrinsics: CameraIntrinsics) -> np.ndarray:
    """Homography-warp an image by a pure camera yaw (+right) under pinhole intrinsics."""
    w, h = float(intrinsics.width), float(intrinsics.height)
    f = (h / 2.0) / math.tan(math.radians(intrinsics.vertical_fov_degrees) / 2.0)
    K = np.array([[f, 0.0, (w - 1.0) / 2.0], [0.0, f, (h - 1.0) / 2.0], [0.0, 0.0, 1.0]], dtype=np.float64)
    H = K @ _rotation_y_down(-yaw_right_rad) @ np.linalg.inv(K)
    return cv2.warpPerspective(image, H, (intrinsics.width, intrinsics.height), borderMode=cv2.BORDER_REFLECT)


def _sensor_frame(rgb: np.ndarray, *, read_id: str, captured_at_ms: int) -> SensorFrame:
    return SensorFrame(
        read_id=read_id,
        readings={
            FRONT_CAMERA_SENSOR_ID: SensorReading(
                sensor_id=FRONT_CAMERA_SENSOR_ID,
                sensor_kind="camera",
                captured_at_ms=captured_at_ms,
                value=rgb,
                metadata={"color_space": "RGB"},
            )
        },
        started_at_ms=captured_at_ms,
        completed_at_ms=captured_at_ms,
    )


def _control_app(
    *,
    delivered_at_ms: int,
    throttle: float,
    steering: float,
    mode: str = "autonomy",
    applied: bool = True,
) -> ControlApplication:
    return ControlApplication(
        frame_id=f"f-{delivered_at_ms}",
        mode=mode,
        applied=applied,
        reason="test",
        control=AutonomyControl(steering=steering, throttle=throttle, confidence=1.0, reason="test"),
        delivered_at_ms=delivered_at_ms,
    )


class VisualYawTests(unittest.TestCase):
    def test_circular_arc_yaw_exact_on_flat_floor_with_unknown_height_and_pitch(self) -> None:
        intrinsics = CameraIntrinsics(320, 240, vertical_fov_degrees=78.0, barrel_distortion=0.18)
        rng = np.random.default_rng(19)
        # Strictly coplanar flat-floor points at two different camera heights (0.10 m and 0.35 m).
        for height in (0.10, 0.35):
            floor_pts = np.column_stack([
                rng.uniform(-1.2, 1.2, 70),
                np.full(70, height),
                rng.uniform(1.0, 4.5, 70),
            ])
            for yaw_true, dist, pitch in (
                (0.14, 0.25, 0.05),
                (-0.18, 0.20, 0.22),
                (0.11, -0.22, 0.12),   # reverse
                (0.15, 0.0, 0.16),     # pure in-place turn
                (0.0, 0.25, 0.10),     # straight
            ):
                flow = _project_planar_arc(
                    floor_pts,
                    yaw_right_rad=yaw_true,
                    signed_distance=dist,
                    pitch_rad=pitch,
                    intrinsics=intrinsics,
                )
                est = estimate_circular_arc_yaw(flow, intrinsics, dt_s=0.1)
                self.assertTrue(est.confident, f"height={height} yaw={yaw_true} dist={dist} pitch={pitch}")
                self.assertIsNotNone(est.yaw_delta_rad)
                self.assertAlmostEqual(est.yaw_delta_rad or 0.0, yaw_true, delta=3e-3)

    def test_mid_arc_expansion_matches_chord_motion_at_both_fovs(self) -> None:
        dt_s = 0.10
        depth = 1.20
        x, y = np.meshgrid(np.linspace(-0.36, 0.36, 10), np.linspace(0.20, 0.36, 6))
        chord_points = np.column_stack([x.ravel(), y.ravel(), np.full(x.size, depth)])
        for yaw, distance in ((0.0, 0.16), (0.42, 0.16), (-0.42, -0.16), (0.42, 0.0)):
            with self.subTest(yaw=yaw, distance=distance):
                points = chord_points @ _rotation_y_down(yaw / 2.0).T
                for fov in (70.0, 82.0):
                    intrinsics = CameraIntrinsics(320, 240, fov, 0.0)
                    flow = _project_planar_arc(
                        points, yaw_right_rad=yaw, signed_distance=distance, pitch_rad=0.10, intrinsics=intrinsics
                    )
                    estimate = estimate_circular_arc_yaw(flow, intrinsics, dt_s)
                    expected = math.log(depth / (depth - distance)) / dt_s
                    assert estimate.expansion_rate_per_s is not None
                    self.assertAlmostEqual(estimate.expansion_rate_per_s, expected, delta=1e-5)

    def test_straight_approach_expansion_increases_with_wrong_focal_scale(self) -> None:
        dt_s, distance = 0.10, 0.20
        true_intrinsics = CameraIntrinsics(320, 240, 82.0, 0.12)
        wrong_intrinsics = CameraIntrinsics(320, 240, 70.0, 0.12)
        x, y = np.meshgrid(np.linspace(-0.28, 0.28, 10), np.linspace(0.26, 0.34, 6))
        previous_expansion = 0.0
        for depth in (1.6, 1.4, 1.2, 1.0, 0.8):
            points = np.column_stack((x.ravel(), y.ravel(), np.full(x.size, depth)))
            flow = _project_planar_arc(
                points, yaw_right_rad=0.0, signed_distance=distance, pitch_rad=0.0, intrinsics=true_intrinsics
            )
            correct = estimate_circular_arc_yaw(flow, true_intrinsics, dt_s)
            wrong = estimate_circular_arc_yaw(flow, wrong_intrinsics, dt_s)
            assert correct.expansion_rate_per_s is not None and wrong.expansion_rate_per_s is not None
            self.assertAlmostEqual(correct.expansion_rate_per_s, wrong.expansion_rate_per_s, delta=1e-6)
            self.assertAlmostEqual(wrong.expansion_rate_per_s, math.log(depth / (depth - distance)) / dt_s, delta=1e-5)
            self.assertGreater(wrong.expansion_rate_per_s, previous_expansion)
            previous_expansion = wrong.expansion_rate_per_s

    def test_curved_approach_expansion_preserves_the_median_in_mixed_depths(self) -> None:
        rng = np.random.default_rng(51)
        yaw, distance, dt_s = 0.16, 0.14, 0.10
        rays_at_depth = []
        for depth, x_bounds, count in (
            (2.20, (-0.28, 0.28), 36),  # background wall
            (0.90, (-0.28, 0.28), 16),  # foreground in the corridor
            (0.70, (0.53, 0.65), 12),  # closer furniture beside the corridor
        ):
            rays_at_depth.append(
                depth * np.column_stack((rng.uniform(*x_bounds, count), rng.uniform(0.18, 0.34, count), np.ones(count)))
            )
        points = np.concatenate(rays_at_depth) @ _rotation_y_down(yaw / 2.0).T
        flow = _project_planar_arc(
            points, yaw_right_rad=yaw, signed_distance=distance, pitch_rad=0.15,
            intrinsics=CameraIntrinsics(320, 240, 82.0, 0.0),
        )
        estimate = estimate_circular_arc_yaw(flow, CameraIntrinsics(320, 240, 82.0, 0.0), dt_s)
        assert estimate.expansion_rate_per_s is not None
        self.assertAlmostEqual(estimate.expansion_rate_per_s, math.log(2.20 / (2.20 - distance)) / dt_s, delta=1e-5)

    def test_expansion_abstains_near_the_focus_of_expansion(self) -> None:
        intrinsics = CameraIntrinsics(320, 240, 82.0, 0.0)
        for center_x, has_expansion in ((0.0, False), (0.62, True)):
            x, y = np.meshgrid(np.linspace(center_x - 0.03, center_x + 0.03, 6), np.linspace(0.035, 0.065, 5))
            points = np.column_stack((x.ravel(), y.ravel(), np.ones(y.size)))
            flow = _project_planar_arc(
                points, yaw_right_rad=0.0, signed_distance=0.10, pitch_rad=0.0, intrinsics=intrinsics
            )
            estimate = estimate_circular_arc_yaw(flow, intrinsics, dt_s=0.10)
            self.assertTrue(estimate.confident)
            self.assertEqual(estimate.expansion_rate_per_s is not None, has_expansion)

    def test_throttle_response_abstains_with_insufficient_or_conflicting_votes(self) -> None:
        fit = fit_throttle_response([(1.0, 0.8), (-1.0, 0.8)] * 2 + [(1.0, 0.01), (0.0, 1.0)])
        self.assertEqual(fit.throttle_sign, "unknown")
        self.assertEqual(fit.sample_count, 4)
        self.assertEqual(fit.consistency, 0.5)
        self.assertEqual(fit.confidence, 0.0)
        self.assertEqual(fit_throttle_response([(1.0, 0.8)] * 3).throttle_sign, "unknown")

    def test_undistort_center_is_origin(self) -> None:
        intr = CameraIntrinsics(320, 240, 70.0, 0.25)
        origin = undistort_pixels(np.array([[159.5, 119.5]]), intr)
        self.assertAlmostEqual(float(origin[0, 0]), 0.0, places=12)
        self.assertAlmostEqual(float(origin[0, 1]), 0.0, places=12)

    def test_fit_turn_response_handles_normal_inverted_and_linear_regimes(self) -> None:
        # Normal Ackermann (+steering -> +yaw)
        normal_obs = [
            TurnObservation(1.0, 0.25, 4.0 * math.tan(0.8 * 0.25)),
            TurnObservation(1.0, 0.70, 4.0 * math.tan(0.8 * 0.70)),
            TurnObservation(-0.8, 0.50, -3.2 * math.tan(0.8 * 0.50)),
            TurnObservation(1.0, -0.85, -4.0 * math.tan(0.8 * 0.85)),
        ]
        fit = fit_turn_response(normal_obs)
        self.assertEqual(fit.kind, "ackermann_tan")
        self.assertEqual(fit.turn_sign, "normal")
        self.assertAlmostEqual(fit.gain_per_s, 4.0, delta=0.05)
        self.assertAlmostEqual(fit.max_angle_rad, 0.8, delta=0.02)

        # Inverted steering wiring (+steering -> -yaw)
        inv_obs = [TurnObservation(o.signed_throttle, o.steering, -o.yaw_rate_rad_s) for o in normal_obs]
        inv_fit = fit_turn_response(inv_obs)
        self.assertEqual(inv_fit.kind, "ackermann_tan")
        self.assertEqual(inv_fit.turn_sign, "inverted")
        self.assertAlmostEqual(inv_fit.gain_per_s, -4.0, delta=0.05)

        # Single steering magnitude falls back cleanly to linear gain
        lin_obs = [TurnObservation(1.0, 0.5, 1.5), TurnObservation(1.0, -0.5, -1.5)]
        lin_fit = fit_turn_response(lin_obs)
        self.assertEqual(lin_fit.kind, "linear")
        self.assertAlmostEqual(lin_fit.predict_yaw_rate(0.5, 1.0), 1.5, places=4)

    def test_fit_turn_response_resists_half_blocked_observations(self) -> None:
        # Reproduce the cramped-room failure mode: 50% of observations are blocked or partial-contact.
        gain = 4.826
        angle = 0.6458
        true_full = gain * math.tan(angle)
        observations: list[TurnObservation] = []
        for steer, count in ((0.25, 5), (0.50, 7), (0.75, 5)):
            w_free = gain * math.tan(angle * steer)
            for idx in range(count):
                direction = 1.0 if idx % 2 == 0 else -1.0
                observations.append(TurnObservation(direction, steer, direction * w_free))
        # Inject 19 blocked / partial-contact observations including pinned full-lock turns.
        for steer, frac in (
            (0.25, 0.0), (0.25, 0.0), (0.25, 0.44),
            (0.50, 0.0), (0.50, 0.47), (0.50, 0.0), (0.50, 0.58),
            (0.75, 0.08), (0.75, 0.0), (0.75, 0.09), (0.75, 0.0), (0.75, 0.52),
            (1.00, 0.06), (1.00, 0.0), (1.00, 0.17), (1.00, 0.0), (1.00, 0.0), (1.00, 0.19), (1.00, 0.29),
        ):
            w_blocked = frac * gain * math.tan(angle * steer)
            observations.append(TurnObservation(1.0, steer, w_blocked))

        fit = fit_turn_response(observations)
        self.assertEqual(fit.kind, "ackermann_tan")
        self.assertEqual(fit.turn_sign, "normal")
        self.assertAlmostEqual(fit.predict_yaw_rate(1.0, 1.0), true_full, delta=0.18)

    def test_fit_turn_response_resists_capture_clock_jitter_and_partial_contacts(self) -> None:
        gain, angle = 4.826, 0.6458
        observations: list[TurnObservation] = []
        for steering in (0.25, 0.50, 0.75, 1.0):
            free_rate = gain * math.tan(angle * steering)
            # A frame with a long measured interval reads low and short intervals read high.
            for wall_dt_ms in (75, 61, 60, 51, 50):
                observations.append(TurnObservation(1.0, steering, free_rate * 60 / wall_dt_ms))
            for contact_fraction in (0.0, 0.28, 0.39, 0.56, 0.60):
                observations.append(TurnObservation(1.0, steering, free_rate * contact_fraction))

        fit = fit_turn_response(observations)
        self.assertEqual(fit.kind, "ackermann_tan")
        self.assertEqual(fit.turn_sign, "normal")
        self.assertAlmostEqual(fit.predict_yaw_rate(1.0, 1.0), gain * math.tan(angle), delta=0.18)

    def test_fit_turn_response_falls_back_to_linear_without_four_supported_inliers(self) -> None:
        # Two steering levels with only 2 points total must not fit a 2-parameter nonlinear curve.
        sparse = [
            TurnObservation(1.0, 0.25, 0.80),
            TurnObservation(1.0, 0.75, 2.30),
        ]
        fit = fit_turn_response(sparse)
        self.assertEqual(fit.kind, "linear")
        self.assertEqual(fit.turn_sign, "normal")
        self.assertGreater(fit.predict_yaw_rate(1.0, 1.0), 2.5)

    def test_fit_turn_response_avoids_extrapolation_blowup_on_imbalanced_bins(self) -> None:
        # Reproduce v2-slow-cal: 5 samples at 0.25, 3 slightly high (+3%) at 0.50, 2 slightly low at 0.75.
        imbalanced = [
            TurnObservation(-1.0, 0.25, -0.722),
            TurnObservation(1.0, -0.25, -0.758),
            TurnObservation(1.0, -0.25, -0.798),
            TurnObservation(-1.0, -0.25, 0.803),
            TurnObservation(-1.0, -0.25, 0.751),
            TurnObservation(-1.0, -0.50, 1.664),
            TurnObservation(1.0, 0.50, 1.633),
            TurnObservation(-1.0, 0.50, -1.684),
            TurnObservation(-1.0, 0.75, -2.365),
            TurnObservation(-1.0, -0.75, 2.299),
            TurnObservation(-1.0, 0.50, -0.606),
            TurnObservation(-1.0, 1.00, -2.208),
        ]
        fit = fit_turn_response(imbalanced)
        self.assertLess(fit.predict_yaw_rate(1.0, 1.0), 3.70)
        self.assertGreater(fit.predict_yaw_rate(1.0, 1.0), 2.90)

    def test_orb_fallback_recovers_large_turn_beyond_pyrlk_range(self) -> None:
        intr = CameraIntrinsics(320, 240, vertical_fov_degrees=82.0, barrel_distortion=0.0)
        img_a = _textured_scene(320, 240)
        # 0.72 rad (~41 deg) turn exceeds PyrLK patch range at 320px width.
        img_b = _warp_pure_yaw(img_a, 0.72, intr)
        est = estimate_visual_yaw(
            rgb_to_working_gray(img_a),
            rgb_to_working_gray(img_b),
            intr,
            dt_s=0.2,
        )
        self.assertTrue(est.confident)
        self.assertIsNotNone(est.yaw_delta_rad)
        self.assertAlmostEqual(est.yaw_delta_rad or 0.0, 0.72, delta=0.04)


class VehicleDynamicsPluginTests(unittest.TestCase):
    def test_plugin_loads_via_catalog_warms_up_and_identifies_turn_response(self) -> None:
        activation = selection_activation("perception", plugins=["vehicle_dynamics"])
        runner = PerceptionRunner.from_activation(
            activation,
        )
        # Override distortion=0 and settle_ms=0 in direct plugin check for exact pinhole homography warp.
        plugin = VehicleDynamicsPlugin(vertical_fov_degrees=70.0, barrel_distortion=0.0, command_settle_ms=0)
        intr = CameraIntrinsics(320, 240, 70.0, 0.0)
        base = _textured_scene()
        memory: dict[str, object] = {}

        # Frame 0: warms up and stores grayscale frame in shared memory.
        req0 = build_perception_request(_sensor_frame(base, read_id="f0", captured_at_ms=1000), shared_memory=memory)
        text0 = runner.perceive(req0)
        self.assertEqual(text0.status, "warming_up")
        # Round-trip shared memory through JSON serialization.
        round_tripped = shared_memory_from_document(json.loads(json.dumps(shared_memory_document(memory))))
        self.assertIn("perception.vehicle_dynamics.history", round_tripped)

        # Drive 4 frames across two steering magnitudes with uniform autonomous control.
        memory = {}
        apps: list[ControlApplication] = []
        current_img = base
        t_ms = 1000
        # Seed frame 0 directly on our pinhole plugin.
        with self.assertRaises(PerceptionPluginWarmingUp):
            plugin.perceive(
                self._inputs(current_img, "f0", t_ms, memory),
            )

        steps = [
            (0.30, 1.0, 0.045),  # steering=0.30 -> +0.045 rad over 100ms (+0.45 rad/s)
            (0.30, 1.0, 0.045),
            (0.75, 1.0, 0.135),  # steering=0.75 -> +0.135 rad over 100ms (+1.35 rad/s)
            (0.75, 1.0, 0.135),
        ]
        last_batch = None
        for idx, (steer, throttle, yaw_step) in enumerate(steps, start=1):
            apps.append(_control_app(delivered_at_ms=t_ms, throttle=throttle, steering=steer))
            write_host_inputs(
                memory, mode="autonomy", user_steering=None, user_throttle=None, control_applications=tuple(apps)
            )
            t_ms += 100
            current_img = _warp_pure_yaw(current_img, yaw_step, intr)
            last_batch = plugin.perceive(self._inputs(current_img, f"f{idx}", t_ms, memory))

        assert last_batch is not None
        signals = {s.signal_id: s for s in last_batch.signals}
        self.assertTrue(signals["visual_yaw_rate_rad_s"].properties["confident"])
        self.assertAlmostEqual(float(signals["visual_yaw_rate_rad_s"].value), 1.35, delta=0.12)
        self.assertTrue(signals["turn_response_available"].value)
        self.assertEqual(signals["turn_response_available"].properties["turn_sign"], "normal")
        self.assertEqual(signals["turn_response_available"].properties["sample_count"], 4)

    def test_passive_straight_throttle_learning_and_stall_after_json_round_trip(self) -> None:
        base = _textured_scene()
        for wiring, direction, label in ((1, "forward", "normal"), (-1, "reverse", "inverted")):
            with self.subTest(wiring=label):
                plugin = VehicleDynamicsPlugin(barrel_distortion=0.0, max_observations=5)
                memory: dict[str, object] = {}
                apps = (_control_app(delivered_at_ms=900, throttle=1.0, steering=0.0),)
                write_host_inputs(
                    memory, mode="autonomy", user_steering=None, user_throttle=None, control_applications=apps
                )
                with self.assertRaises(PerceptionPluginWarmingUp):
                    plugin.perceive(self._inputs(base, "f0", 1000, memory))
                current = base
                for index in range(1, 7):
                    scale = 1.0 + wiring * index * 0.025
                    warp = cv2.getRotationMatrix2D((159.5, 119.5), 0.0, scale)
                    current = cv2.warpAffine(base, warp, (320, 240), borderMode=cv2.BORDER_REFLECT)
                    batch = plugin.perceive(self._inputs(current, f"f{index}", 1000 + index * 100, memory))
                    self.assertFalse(batch.measurements["control_recorded"])
                    self.assertFalse(batch.measurements["motion_blocked"])
                    self.assertFalse(batch.measurements["motion_impeded"])
                    signal = next(s for s in batch.signals if s.signal_id == "visual_expansion_rate_per_s")
                    self.assertEqual(signal.properties["motion_direction"], direction)
                    self.assertEqual(signal.properties["throttle_sign"], label if index >= 4 else "unknown")
                    self.assertEqual(signal.properties["throttle_sample_count"], min(index, 5))
                    self.assertGreater(wiring * float(signal.value), 0.0)

                memory = shared_memory_from_document(json.loads(json.dumps(shared_memory_document(memory))))
                with mock.patch(
                    "implementations.decision_cycle.perception.plugins.vehicle_dynamics.plugin.fit_throttle_response",
                    side_effect=AssertionError("a stall should reuse the fitted throttle response"),
                ):
                    stalled = plugin.perceive(self._inputs(current, "stalled", 1700, memory))
                self.assertTrue(stalled.measurements["motion_blocked"])
                self.assertTrue(stalled.measurements["motion_impeded"])
                self.assertEqual(stalled.measurements["throttle_sign"], label)
                self.assertEqual(stalled.measurements["expansion_rate_per_s"], 0.0)
                self.assertEqual(stalled.measurements["throttle_sample_count"], 5)

                opposed = VisualYawEstimate(0.0, 0.0, 0.0, True, "test", 40, 1.0, 2.0, -0.5 * wiring)
                with mock.patch.object(plugin, "_estimate_pair", return_value=opposed):
                    batch = plugin.perceive(self._inputs(current, "opposed", 1800, memory))
                self.assertFalse(batch.measurements["motion_blocked"])
                self.assertTrue(batch.measurements["motion_impeded"])

    def test_motion_impeded_compares_prior_turn_fit_and_keeps_learning(self) -> None:
        plugin = VehicleDynamicsPlugin(max_observations=6)
        base = _textured_scene()
        memory: dict[str, object] = {}
        apps = (_control_app(delivered_at_ms=900, throttle=1.0, steering=0.5),)
        write_host_inputs(memory, mode="autonomy", user_steering=None, user_throttle=None, control_applications=apps)
        with self.assertRaises(PerceptionPluginWarmingUp):
            plugin.perceive(self._inputs(base, "f0", 1000, memory))

        free_yaw, partial_yaw = 1.4, 0.35 * 1.4
        for index, yaw in enumerate([free_yaw] * 6 + [partial_yaw] * 8, start=1):
            estimate = VisualYawEstimate(yaw * 0.1, yaw, 0.0, True, "test", 40, 1.0, 3.0, 0.2)
            with mock.patch.object(plugin, "_estimate_pair", return_value=estimate):
                batch = plugin.perceive(self._inputs(base, f"f{index}", 1000 + index * 100, memory))
            measurements = batch.measurements
            self.assertTrue(measurements["control_recorded"])
            self.assertFalse(measurements["motion_blocked"])
            if index in (6, 7, 14):
                expected = partial_yaw if index == 14 else free_yaw
                self.assertEqual(measurements["motion_impeded"], index == 7)
                self.assertAlmostEqual(measurements["expected_yaw_rate_rad_s"], expected)
                self.assertAlmostEqual(measurements["motion_ratio"], yaw / expected)
                yaw_signal = next(s for s in batch.signals if s.signal_id == "visual_yaw_rate_rad_s")
                self.assertEqual(yaw_signal.properties["motion_impeded"], measurements["motion_impeded"])

    def test_visible_expansion_prevents_a_false_dead_stall_at_small_pixel_shift(self) -> None:
        plugin = VehicleDynamicsPlugin()
        base = _textured_scene()
        memory: dict[str, object] = {}
        apps = (_control_app(delivered_at_ms=900, throttle=1.0, steering=0.0),)
        write_host_inputs(memory, mode="autonomy", user_steering=None, user_throttle=None, control_applications=apps)
        with self.assertRaises(PerceptionPluginWarmingUp):
            plugin.perceive(self._inputs(base, "f0", 1000, memory))
        estimate = VisualYawEstimate(0.0, 0.0, 0.0, True, "test", 40, 1.0, 0.8, 0.2)
        with mock.patch.object(plugin, "_estimate_pair", return_value=estimate):
            batch = plugin.perceive(self._inputs(base, "f1", 1100, memory))
        self.assertFalse(batch.measurements["motion_blocked"])
        self.assertFalse(batch.measurements["motion_impeded"])

    def test_smooth_controls_are_recorded_with_time_weighted_interval_averages(self) -> None:
        plugin = VehicleDynamicsPlugin(barrel_distortion=0.0)
        intr = CameraIntrinsics(320, 240, 70.0, 0.0)
        base = _textured_scene()
        memory: dict[str, object] = {}
        with self.assertRaises(PerceptionPluginWarmingUp):
            plugin.perceive(self._inputs(base, "f0", 1000, memory))
        apps = (
            _control_app(delivered_at_ms=930, throttle=0.70, steering=0.30),
            _control_app(delivered_at_ms=980, throttle=0.72, steering=0.32),
            _control_app(delivered_at_ms=1010, throttle=0.76, steering=0.35),
            _control_app(delivered_at_ms=1040, throttle=0.80, steering=0.39),
            _control_app(delivered_at_ms=1060, throttle=0.82, steering=0.41),
            _control_app(delivered_at_ms=1080, throttle=0.83, steering=0.43),
            _control_app(delivered_at_ms=1110, throttle=0.85, steering=0.44),
        )
        write_host_inputs(memory, mode="autonomy", user_steering=None, user_throttle=None, control_applications=apps)

        first = _warp_pure_yaw(base, 0.06, intr)
        second = _warp_pure_yaw(first, 0.07, intr)
        for batch, expected_throttle, expected_steering in (
            (plugin.perceive(self._inputs(first, "f1", 1060, memory)), 0.7666666667, 0.3583333333),
            (plugin.perceive(self._inputs(second, "f2", 1110, memory)), 0.826, 0.422),
        ):
            self.assertTrue(batch.measurements["control_recorded"])
            control = batch.measurements["interval_control"]
            assert isinstance(control, list)
            self.assertAlmostEqual(control[0], expected_throttle)
            self.assertAlmostEqual(control[1], expected_steering)

    def test_rejects_large_command_changes_or_reversals_inside_interval(self) -> None:
        plugin = VehicleDynamicsPlugin(barrel_distortion=0.0)
        intr = CameraIntrinsics(320, 240, 70.0, 0.0)
        base = _textured_scene()
        warped = _warp_pure_yaw(base, 0.08, intr)
        for label, throttle, steering in (
            ("steering jump", 0.07, 0.80),
            ("throttle jump", 0.35, 0.40),
            ("throttle reversal within magnitude tolerance", -0.06, 0.40),
        ):
            with self.subTest(change=label):
                memory: dict[str, object] = {}
                apps = (
                    _control_app(delivered_at_ms=930, throttle=0.07, steering=0.40),
                    _control_app(delivered_at_ms=1025, throttle=throttle, steering=steering),
                )
                write_host_inputs(
                    memory, mode="autonomy", user_steering=None, user_throttle=None, control_applications=apps
                )
                with self.assertRaises(PerceptionPluginWarmingUp):
                    plugin.perceive(self._inputs(base, "f0", 1000, memory))
                batch = plugin.perceive(self._inputs(warped, "f1", 1060, memory))
                self.assertTrue(batch.measurements["visual_yaw"]["confident"])
                self.assertFalse(batch.measurements["control_recorded"])
                self.assertIsNone(batch.measurements["interval_control"])

    def test_stall_and_settle_guard(self) -> None:
        plugin = VehicleDynamicsPlugin(barrel_distortion=0.0)
        intr = CameraIntrinsics(320, 240, 70.0, 0.0)
        base = _textured_scene()
        memory: dict[str, object] = {}
        with self.assertRaises(PerceptionPluginWarmingUp):
            plugin.perceive(self._inputs(base, "f0", 1000, memory))

        # The command changed 35 ms before prev_ms, inside the default settle window.
        apps = (
            _control_app(delivered_at_ms=700, throttle=0.0, steering=0.0),
            _control_app(delivered_at_ms=965, throttle=1.0, steering=0.5),
        )
        write_host_inputs(memory, mode="autonomy", user_steering=None, user_throttle=None, control_applications=apps)
        warped = _warp_pure_yaw(base, 0.08, intr)
        batch_unsettled = plugin.perceive(self._inputs(warped, "f1", 1100, memory))
        self.assertFalse(batch_unsettled.measurements["control_recorded"])
        self.assertTrue(batch_unsettled.measurements["visual_yaw"]["confident"])

        # The settled command drives against an obstacle, leaving the next frame identical.
        settled_apps = (
            _control_app(delivered_at_ms=800, throttle=1.0, steering=0.5),
        )
        write_host_inputs(
            memory, mode="autonomy", user_steering=None, user_throttle=None, control_applications=settled_apps
        )
        batch_stalled = plugin.perceive(self._inputs(warped, "f2", 1200, memory))
        self.assertTrue(batch_stalled.measurements["motion_blocked"])
        self.assertFalse(batch_stalled.measurements["control_recorded"])

        # Blank frame -> abstains cleanly without invoking ORB fallback.
        blank = np.full_like(base, 128)
        batch_blank = plugin.perceive(self._inputs(blank, "f3", 1300, memory))
        self.assertFalse(batch_blank.measurements["visual_yaw"]["confident"])

    def test_cached_turn_response_skips_refit_when_no_observation_recorded(self) -> None:
        from unittest import mock

        plugin = VehicleDynamicsPlugin(vertical_fov_degrees=70.0, barrel_distortion=0.0, command_settle_ms=0)
        intr = CameraIntrinsics(320, 240, 70.0, 0.0)
        base = _textured_scene()
        memory: dict[str, object] = {}
        with self.assertRaises(PerceptionPluginWarmingUp):
            plugin.perceive(self._inputs(base, "f0", 1000, memory))

        # Seed one recorded observation so cached_response is populated.
        apps = (_control_app(delivered_at_ms=1000, throttle=1.0, steering=0.5),)
        write_host_inputs(memory, mode="autonomy", user_steering=None, user_throttle=None, control_applications=apps)
        warped = _warp_pure_yaw(base, 0.08, intr)
        plugin.perceive(self._inputs(warped, "f1", 1100, memory))

        # Round-trip shared memory through JSON serialization.
        memory = shared_memory_from_document(json.loads(json.dumps(shared_memory_document(memory))))

        # Next frame has dt out of range (recorded=False): fit_turn_response must NOT be called.
        with mock.patch(
            "implementations.decision_cycle.perception.plugins.vehicle_dynamics.plugin.fit_turn_response",
            side_effect=AssertionError("fit_turn_response should not run when no observation is recorded"),
        ):
            batch = plugin.perceive(self._inputs(warped, "f2", 2000, memory))
        self.assertFalse(batch.measurements["control_recorded"])
        self.assertEqual(batch.measurements["visual_yaw"]["mode"], "abstain_dt_out_of_range")

    @staticmethod
    def _inputs(rgb: np.ndarray, frame_id: str, captured_at_ms: int, memory: dict[str, object]):
        from autonomy.decision_cycle.perception.plugin import PerceptionPluginInputs
        from implementations.decision_cycle.perception.feeds.camera import CameraFrame

        return PerceptionPluginInputs(
            frame_id=frame_id,
            captured_at_ms=captured_at_ms,
            feeds={"frame": CameraFrame(FRONT_CAMERA_SENSOR_ID, captured_at_ms, rgb)},
            shared_memory=memory,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
