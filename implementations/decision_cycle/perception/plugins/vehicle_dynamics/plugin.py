"""Windowed scale-free visual motion and vehicle response perception plugin."""

from __future__ import annotations

from copy import copy
from dataclasses import asdict, dataclass

import numpy as np

from autonomy.decision_cycle.action.publication import control_applications_over
from autonomy.decision_cycle.perception.evidence.values import (
    PerceptionEvidenceBatch,
    PerceptionSignal,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginContract,
    PerceptionPluginInputs,
    PerceptionPluginWarmingUp,
)
from autonomy.runtime.execution import ControlApplication
from autonomy.shared_memory import SharedMemory
from implementations.decision_cycle.perception.feeds.camera import (
    FRONT_CAMERA_RGB_INPUT,
    CameraFrame,
)
from implementations.decision_cycle.perception.shared.image.stored import StoredImage
from implementations.decision_cycle.perception.shared.motion.visual_yaw import (
    MIN_EXPANSION_RATE_PER_S,
    CameraIntrinsics,
    ThrottleResponseEstimate,
    TurnObservation,
    TurnResponseEstimate,
    VisualYawEstimate,
    estimate_visual_yaw,
    fit_throttle_response,
    fit_turn_response,
    rgb_to_working_gray,
)
from implementations.decision_cycle.perception.shared.serialization.history import (
    history_float,
    history_int,
)


@dataclass(frozen=True)
class MotionDiagnostic:
    motion_impeded: bool
    expected_yaw_rate_rad_s: float | None = None
    motion_ratio: float | None = None


class VehicleDynamicsPlugin:
    """Estimate visual motion and learn the vehicle's response to applied controls."""

    plugin_id = "vehicle_dynamics"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        state_mode="windowed",
        memory_required=True,
        description=(
            "Estimate scale-free yaw and longitudinal motion from consecutive front-camera frames "
            "and learn the vehicle's response to applied autonomous controls."
        ),
        assumptions=(
            "consecutive camera frames have sufficient overlap and scene texture for PyrLK or ORB matching",
            "motion is approximately planar on a flat floor",
            "applied autonomous controls are published in shared memory before perception runs",
        ),
        emits=(
            "signal visual_yaw_rate_rad_s",
            "signal turn_response_available",
            "signal visual_expansion_rate_per_s",
        ),
        limitations=(
            "scale-free motion identification without metric speed or turning radius",
            "turn-response updates require smooth applied autonomous controls and non-stalled visual motion",
            "radians-per-second scale depends on camera vertical FOV and barrel distortion configuration",
        ),
    )

    def __init__(
        self,
        *,
        working_width: int = 320,
        vertical_fov_degrees: float = 70.0,
        barrel_distortion: float = 0.12,
        min_dt_ms: int = 20,
        max_dt_ms: int = 500,
        command_settle_ms: int = 45,
        min_moving_px_shift: float = 1.5,
        max_observations: int = 40,
        min_tracks: int = 15,
        min_inlier_ratio: float = 0.50,
        max_step_yaw_radians: float = 0.90,
    ) -> None:
        self.working_width = max(80, int(working_width))
        self.vertical_fov_degrees = float(vertical_fov_degrees)
        self.barrel_distortion = float(barrel_distortion)
        self.min_dt_ms = max(1, int(min_dt_ms))
        self.max_dt_ms = max(self.min_dt_ms, int(max_dt_ms))
        self.command_settle_ms = max(0, int(command_settle_ms))
        self.min_moving_px_shift = max(0.0, float(min_moving_px_shift))
        self.max_observations = max(2, int(max_observations))
        self.min_tracks = max(8, int(min_tracks))
        self.min_inlier_ratio = max(0.1, min(0.95, float(min_inlier_ratio)))
        self.max_step_yaw_radians = max(0.05, float(max_step_yaw_radians))

    @property
    def _memory_key(self) -> str:
        return f"perception.{self.plugin_id}.history"

    def _initialize_history(self) -> None:
        self._previous_gray: np.ndarray | None = None
        self._previous_captured_at_ms: int | None = None
        self._observations: list[TurnObservation] = []
        self._cached_response: TurnResponseEstimate | None = None
        self._throttle_observations: list[tuple[float, float]] = []
        self._cached_throttle_response: ThrottleResponseEstimate | None = None

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        if shared_memory is not None:
            shared_memory.pop(self._memory_key, None)

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        if inputs.shared_memory is None:
            raise ValueError(f"{self.plugin_id} requires host shared memory")
        step = copy(self)
        step._restore_history(inputs.shared_memory.get(self._memory_key))
        try:
            batch = step._perceive_frame(inputs)
        except PerceptionPluginWarmingUp:
            inputs.shared_memory[self._memory_key] = step._history()
            raise
        inputs.shared_memory[self._memory_key] = step._history()
        return batch

    def _restore_history(self, history: object) -> None:
        self._initialize_history()
        if not isinstance(history, dict):
            return
        previous = history.get("previous_gray")
        if isinstance(previous, StoredImage) and previous.pixels.ndim == 2:
            self._previous_gray = np.array(previous.pixels, dtype=np.uint8, copy=True)
        self._previous_captured_at_ms = history_int(history.get("previous_captured_at_ms"))
        self._observations = _restore_observations(history.get("observations"), self.max_observations)
        self._cached_response = _restore_response(history.get("cached_response"))
        self._throttle_observations = _restore_throttle_observations(
            history.get("throttle_observations"), self.max_observations
        )
        self._cached_throttle_response = _restore_throttle_response(history.get("cached_throttle_response"))

    def _history(self) -> dict[str, object]:
        return {
            "previous_gray": None if self._previous_gray is None else StoredImage(self._previous_gray),
            "previous_captured_at_ms": self._previous_captured_at_ms,
            "observations": [
                [obs.signed_throttle, obs.steering, obs.yaw_rate_rad_s] for obs in self._observations
            ],
            "cached_response": _serialize_response(self._cached_response),
            "throttle_observations": [list(obs) for obs in self._throttle_observations],
            "cached_throttle_response": (
                None if self._cached_throttle_response is None else asdict(self._cached_throttle_response)
            ),
        }

    def _perceive_frame(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        current_gray = rgb_to_working_gray(frame.rgb, working_width=self.working_width)
        previous_gray = self._previous_gray
        previous_ms = self._previous_captured_at_ms
        self._previous_gray = current_gray
        self._previous_captured_at_ms = int(frame.captured_at_ms)

        if not _has_matching_previous(previous_gray, previous_ms, current_gray):
            raise PerceptionPluginWarmingUp(
                "no_previous_frame",
                measurements={"has_previous_frame": False, "observation_count": len(self._observations)},
            )

        assert previous_gray is not None and previous_ms is not None
        dt_ms = int(frame.captured_at_ms) - previous_ms
        estimate = self._estimate_pair(previous_gray, current_gray, dt_ms)
        applied_control = _interval_autonomous_control(
            inputs.shared_memory,
            previous_ms,
            int(frame.captured_at_ms),
            settle_ms=self.command_settle_ms,
        )
        blocked = _is_motion_blocked(estimate, applied_control, self.min_moving_px_shift)
        # Compare against prior evidence. Only the dead-stall guard gates turn learning.
        diagnostic = _motion_diagnostic(
            estimate, applied_control, self._cached_response, self._cached_throttle_response, blocked=blocked
        )
        recorded = self._maybe_record_observation(estimate, applied_control, blocked=blocked)
        response = self._current_response(refit=recorded)
        throttle_response = self._update_throttle_response(estimate, applied_control)
        return _build_batch(
            estimate,
            response,
            throttle_response,
            diagnostic,
            dt_ms=dt_ms,
            control=applied_control,
            recorded=recorded,
            blocked=blocked,
        )

    def _current_response(self, *, refit: bool) -> TurnResponseEstimate:
        if refit or self._cached_response is None:
            self._cached_response = fit_turn_response(self._observations)
        return self._cached_response

    def _update_throttle_response(
        self, estimate: VisualYawEstimate, control: tuple[float, float] | None,
    ) -> ThrottleResponseEstimate:
        expansion = estimate.expansion_rate_per_s
        if (
            estimate.confident and expansion is not None and abs(expansion) > MIN_EXPANSION_RATE_PER_S
            and control is not None and abs(control[0]) > 0.1
        ):
            self._throttle_observations.append((control[0], expansion))
            self._throttle_observations = self._throttle_observations[-self.max_observations :]
            self._cached_throttle_response = None
        if self._cached_throttle_response is None:
            self._cached_throttle_response = fit_throttle_response(self._throttle_observations)
        return self._cached_throttle_response

    def _estimate_pair(
        self,
        previous_gray: np.ndarray,
        current_gray: np.ndarray,
        dt_ms: int,
    ) -> VisualYawEstimate:
        if not (self.min_dt_ms <= dt_ms <= self.max_dt_ms):
            return VisualYawEstimate(None, None, None, False, "abstain_dt_out_of_range", 0, 0.0, 0.0)
        height, width = current_gray.shape[:2]
        intrinsics = CameraIntrinsics(
            width=width,
            height=height,
            vertical_fov_degrees=self.vertical_fov_degrees,
            barrel_distortion=self.barrel_distortion,
        )
        return estimate_visual_yaw(
            previous_gray,
            current_gray,
            intrinsics,
            dt_ms / 1000.0,
            min_tracks=self.min_tracks,
            min_inlier_ratio=self.min_inlier_ratio,
            max_step_yaw_rad=self.max_step_yaw_radians,
        )

    def _maybe_record_observation(
        self,
        estimate: VisualYawEstimate,
        control: tuple[float, float] | None,
        *,
        blocked: bool,
    ) -> bool:
        if blocked or not estimate.confident or estimate.yaw_rate_rad_s is None:
            return False
        if control is None or not _is_active_turn_control(control):
            return False
        signed_throttle, steering = control
        self._observations.append(TurnObservation(signed_throttle, steering, estimate.yaw_rate_rad_s))
        self._observations = self._observations[-self.max_observations :]
        return True


def _has_matching_previous(
    previous_gray: np.ndarray | None,
    previous_ms: int | None,
    current_gray: np.ndarray,
) -> bool:
    if previous_gray is None or previous_ms is None:
        return False
    return previous_gray.shape == current_gray.shape


def _is_active_turn_control(control: tuple[float, float]) -> bool:
    signed_throttle, steering = control
    return abs(signed_throttle) > 1e-4 and abs(steering) > 1e-4


def _is_motion_blocked(
    estimate: VisualYawEstimate,
    control: tuple[float, float] | None,
    min_moving_px_shift: float,
) -> bool:
    if control is None or not estimate.confident or estimate.yaw_delta_rad is None:
        return False
    signed_throttle, _steering = control
    if abs(signed_throttle) <= 0.1:
        return False
    expansion = estimate.expansion_rate_per_s
    return (
        estimate.median_px_shift < min_moving_px_shift and abs(estimate.yaw_delta_rad) < 0.015
        and (expansion is None or abs(expansion) <= MIN_EXPANSION_RATE_PER_S)
    )


def _motion_diagnostic(
    estimate: VisualYawEstimate,
    control: tuple[float, float] | None,
    response: TurnResponseEstimate | None,
    throttle_response: ThrottleResponseEstimate | None,
    *,
    blocked: bool,
) -> MotionDiagnostic:
    if control is None or not estimate.confident:
        return MotionDiagnostic(blocked)
    throttle, steering = control
    if abs(steering) <= 0.12:
        return MotionDiagnostic(blocked or _is_straight_motion_impeded(estimate, throttle, throttle_response))
    yaw_rate = estimate.yaw_rate_rad_s
    if response is None or response.confidence < 0.5 or yaw_rate is None or abs(throttle) <= 0.1:
        return MotionDiagnostic(blocked)
    # Turn response was fitted against commanded throttle, including any polarity inversion.
    expected = response.predict_yaw_rate(steering, throttle)
    if abs(expected) < 0.40:
        return MotionDiagnostic(blocked, expected)
    ratio = yaw_rate / expected
    return MotionDiagnostic(blocked or ratio < 0.55, expected, ratio)


def _is_straight_motion_impeded(
    estimate: VisualYawEstimate, throttle: float, response: ThrottleResponseEstimate | None,
) -> bool:
    expansion = estimate.expansion_rate_per_s
    if expansion is None or abs(throttle) <= 0.2 or response is None or response.throttle_sign == "unknown":
        return False
    expects_forward = (throttle > 0.0) == (response.throttle_sign == "normal")
    aligned_expansion = expansion if expects_forward else -expansion
    return aligned_expansion <= MIN_EXPANSION_RATE_PER_S


def _parse_observation(item: object) -> TurnObservation | None:
    if not isinstance(item, (list, tuple)) or len(item) != 3:
        return None
    throttle = history_float(item[0])
    steering = history_float(item[1])
    yaw_rate = history_float(item[2])
    if throttle is None or steering is None or yaw_rate is None:
        return None
    return TurnObservation(throttle, steering, yaw_rate)


def _restore_observations(raw: object, limit: int) -> list[TurnObservation]:
    if not isinstance(raw, list):
        return []
    restored = [obs for item in raw if (obs := _parse_observation(item)) is not None]
    return restored[-limit:]


def _restore_throttle_observations(raw: object, limit: int) -> list[tuple[float, float]]:
    if not isinstance(raw, list):
        return []
    restored: list[tuple[float, float]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            throttle, expansion = history_float(item[0]), history_float(item[1])
            if throttle is not None and expansion is not None:
                restored.append((throttle, expansion))
    return restored[-limit:]


def _restore_throttle_response(raw: object) -> ThrottleResponseEstimate | None:
    if not isinstance(raw, dict):
        return None
    sign = raw.get("throttle_sign")
    if not isinstance(sign, str) or sign not in ("normal", "inverted", "unknown"):
        return None
    samples = history_int(raw.get("sample_count"))
    consistency = history_float(raw.get("consistency"))
    confidence = history_float(raw.get("confidence"))
    if samples is None or consistency is None or confidence is None:
        return None
    return ThrottleResponseEstimate(sign, samples, consistency, confidence)


def _serialize_response(response: TurnResponseEstimate | None) -> dict[str, object] | None:
    if response is None:
        return None
    return {
        "kind": response.kind,
        "gain_per_s": response.gain_per_s,
        "max_angle_rad": response.max_angle_rad,
        "turn_sign": response.turn_sign,
        "sample_count": response.sample_count,
        "distinct_steering_count": response.distinct_steering_count,
        "median_residual_rad_s": response.median_residual_rad_s,
        "confidence": response.confidence,
    }


def _restore_response(raw: object) -> TurnResponseEstimate | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    turn_sign = raw.get("turn_sign")
    gain = history_float(raw.get("gain_per_s"))
    max_angle = history_float(raw.get("max_angle_rad"))
    samples = history_int(raw.get("sample_count"))
    distinct = history_int(raw.get("distinct_steering_count"))
    confidence = history_float(raw.get("confidence"))
    if not isinstance(kind, str) or not isinstance(turn_sign, str):
        return None
    if gain is None or max_angle is None or samples is None or distinct is None or confidence is None:
        return None
    residual = history_float(raw.get("median_residual_rad_s"))
    return TurnResponseEstimate(kind, gain, max_angle, turn_sign, samples, distinct, residual, confidence)


def _interval_autonomous_control(
    shared_memory: SharedMemory | None,
    start_ms: int,
    end_ms: int,
    *,
    settle_ms: int,
) -> tuple[float, float] | None:
    if end_ms <= start_ms:
        return None
    window_start = start_ms - settle_ms
    applications = control_applications_over(shared_memory, window_start, end_ms)
    if applications and _controls_vary_smoothly(applications):
        return _average_control(applications, start_ms, end_ms)
    return None


def _controls_vary_smoothly(applications: tuple[ControlApplication, ...]) -> bool:
    throttles = [app.control.throttle for app in applications]
    low_throttle, high_throttle = min(throttles), max(throttles)
    spans_zero = low_throttle <= 0.0 <= high_throttle
    if spans_zero and low_throttle != high_throttle:
        return False
    steers = [app.control.steering for app in applications]
    return high_throttle <= low_throttle + 0.15 and max(steers) <= min(steers) + 0.12


def _average_control(
    applications: tuple[ControlApplication, ...], start_ms: int, end_ms: int,
) -> tuple[float, float]:
    # The lookback guards alignment. Only time inside the image pair contributes to the mean.
    ends = [app.delivered_at_ms for app in applications[1:]] + [end_ms]
    throttle_sum = steering_sum = 0.0
    cursor = start_ms
    for app, next_ms in zip(applications, ends, strict=True):
        assert next_ms is not None  # control_applications_over guarantees timed writes.
        span = max(0, next_ms - cursor)
        throttle_sum += span * app.control.throttle
        steering_sum += span * app.control.steering
        cursor += span
    duration = end_ms - start_ms
    return throttle_sum / duration, steering_sum / duration


def _expansion_signal(estimate: VisualYawEstimate, response: ThrottleResponseEstimate) -> PerceptionSignal:
    expansion = estimate.expansion_rate_per_s
    direction = "unknown"
    if estimate.confident and expansion is not None:
        direction = "stationary"
        if abs(expansion) > MIN_EXPANSION_RATE_PER_S:
            direction = "forward" if expansion > 0.0 else "reverse"
    return PerceptionSignal(
        "visual_expansion_rate_per_s",
        None if expansion is None else round(expansion, 6),
        0.0 if direction == "unknown" else round(estimate.inlier_ratio, 5),
        {
            "confident": direction != "unknown",
            "motion_direction": direction,
            "throttle_sign": response.throttle_sign,
            "throttle_sample_count": response.sample_count,
            "throttle_confidence": response.confidence,
        },
    )


def _build_batch(
    estimate: VisualYawEstimate,
    response: TurnResponseEstimate,
    throttle_response: ThrottleResponseEstimate,
    diagnostic: MotionDiagnostic,
    *,
    dt_ms: int,
    control: tuple[float, float] | None,
    recorded: bool,
    blocked: bool,
) -> PerceptionEvidenceBatch:
    yaw_conf = round(estimate.inlier_ratio, 5) if estimate.confident else 0.0
    motion = asdict(diagnostic)
    yaw_signal = PerceptionSignal(
        "visual_yaw_rate_rad_s",
        None if estimate.yaw_rate_rad_s is None else round(estimate.yaw_rate_rad_s, 6),
        yaw_conf,
        {
            "confident": estimate.confident,
            "mode": estimate.mode,
            "yaw_delta_rad": None if estimate.yaw_delta_rad is None else round(estimate.yaw_delta_rad, 6),
            "estimated_pitch_down_rad": (
                None if estimate.pitch_down_rad is None else round(estimate.pitch_down_rad, 5)
            ),
            "dt_ms": dt_ms,
            "num_tracks": estimate.num_tracks,
            "inlier_ratio": round(estimate.inlier_ratio, 5),
            "median_px_shift": round(estimate.median_px_shift, 4),
            "motion_blocked": blocked,
            **motion,
        },
    )
    available = response.kind != "abstain"
    turn_signal = PerceptionSignal(
        "turn_response_available",
        available,
        response.confidence,
        {
            "kind": response.kind,
            "gain_per_s": round(response.gain_per_s, 5),
            "max_angle_rad": round(response.max_angle_rad, 5),
            "turn_sign": response.turn_sign,
            "sample_count": response.sample_count,
            "distinct_steering_count": response.distinct_steering_count,
            "median_residual_rad_s": (
                None if response.median_residual_rad_s is None else round(response.median_residual_rad_s, 5)
            ),
            "predicted_full_right_rad_s": round(response.predict_yaw_rate(1.0, 1.0), 5),
        },
    )
    expansion_signal = _expansion_signal(estimate, throttle_response)
    return PerceptionEvidenceBatch(
        signals=(yaw_signal, turn_signal, expansion_signal),
        measurements={
            "visual_yaw": dict(yaw_signal.properties),
            "turn_response": dict(turn_signal.properties),
            "control_recorded": recorded,
            "motion_blocked": blocked,
            **motion,
            "expansion_rate_per_s": expansion_signal.value,
            "throttle_sign": throttle_response.throttle_sign,
            "throttle_sample_count": throttle_response.sample_count,
            "throttle_confidence": throttle_response.confidence,
            "interval_control": None if control is None else [control[0], control[1]],
        },
    )
