"""Windowed scale-free visual yaw-rate and turn-response perception plugin."""

from __future__ import annotations

from copy import copy

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
    CameraIntrinsics,
    TurnObservation,
    TurnResponseEstimate,
    VisualYawEstimate,
    estimate_circular_arc_yaw,
    fit_turn_response,
    rgb_to_working_gray,
    track_pyrlk,
)
from implementations.decision_cycle.perception.shared.serialization.history import (
    history_float,
    history_int,
)


class VehicleDynamicsPlugin:
    """Estimate live angular yaw rate and identify the vehicle's steering-response curve."""

    plugin_id = "vehicle_dynamics"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        state_mode="windowed",
        memory_required=True,
        description=(
            "Estimate scale-free vehicle yaw rate from consecutive front-camera frames and "
            "fit steering-response dynamics against applied autonomous controls."
        ),
        assumptions=(
            "consecutive camera frames have sufficient overlap and scene texture for Lucas-Kanade flow",
            "motion is approximately planar on a flat floor",
            "applied autonomous controls are published in shared memory before perception runs",
        ),
        emits=(
            "signal visual_yaw_rate_rad_s",
            "signal turn_response_available",
        ),
        limitations=(
            "scale-free angular identification only; does not estimate metric speed or turning radius",
            "turn-response updates require autonomous control history with constant control over the frame interval",
            "severe motion blur, untextured walls, or excessive inter-frame yaw trigger abstention",
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
        max_observations: int = 40,
        min_tracks: int = 15,
        min_inlier_ratio: float = 0.50,
        max_step_yaw_radians: float = 0.65,
    ) -> None:
        self.working_width = max(80, int(working_width))
        self.vertical_fov_degrees = float(vertical_fov_degrees)
        self.barrel_distortion = float(barrel_distortion)
        self.min_dt_ms = max(1, int(min_dt_ms))
        self.max_dt_ms = max(self.min_dt_ms, int(max_dt_ms))
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

    def _history(self) -> dict[str, object]:
        return {
            "previous_gray": None if self._previous_gray is None else StoredImage(self._previous_gray),
            "previous_captured_at_ms": self._previous_captured_at_ms,
            "observations": [
                [obs.signed_throttle, obs.steering, obs.yaw_rate_rad_s] for obs in self._observations
            ],
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
        applied_control = _constant_autonomous_control(inputs.shared_memory, previous_ms, int(frame.captured_at_ms))
        recorded = self._maybe_record_observation(estimate, applied_control)
        response = fit_turn_response(self._observations)
        return _build_batch(estimate, response, dt_ms, applied_control, recorded)

    def _estimate_pair(
        self,
        previous_gray: np.ndarray,
        current_gray: np.ndarray,
        dt_ms: int,
    ) -> VisualYawEstimate:
        if not (self.min_dt_ms <= dt_ms <= self.max_dt_ms):
            return VisualYawEstimate(None, None, None, False, "abstain_dt_out_of_range", 0, 0.0)
        flow = track_pyrlk(previous_gray, current_gray)
        height, width = current_gray.shape[:2]
        intrinsics = CameraIntrinsics(
            width=width,
            height=height,
            vertical_fov_degrees=self.vertical_fov_degrees,
            barrel_distortion=self.barrel_distortion,
        )
        return estimate_circular_arc_yaw(
            flow,
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
    ) -> bool:
        if not estimate.confident or estimate.yaw_rate_rad_s is None:
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


def _constant_autonomous_control(
    shared_memory: SharedMemory | None,
    start_ms: int,
    end_ms: int,
) -> tuple[float, float] | None:
    applications = control_applications_over(shared_memory, start_ms, end_ms)
    if not applications:
        return None
    return _uniform_command(applications)


def _uniform_command(applications: tuple[ControlApplication, ...]) -> tuple[float, float] | None:
    first = applications[0].control
    for app in applications[1:]:
        if (
            abs(app.control.throttle - first.throttle) > 1e-6
            or abs(app.control.steering - first.steering) > 1e-6
        ):
            return None
    return (float(first.throttle), float(first.steering))


def _build_batch(
    estimate: VisualYawEstimate,
    response: TurnResponseEstimate,
    dt_ms: int,
    control: tuple[float, float] | None,
    recorded: bool,
) -> PerceptionEvidenceBatch:
    yaw_conf = round(estimate.inlier_ratio, 5) if estimate.confident else 0.0
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
    return PerceptionEvidenceBatch(
        signals=(yaw_signal, turn_signal),
        measurements={
            "visual_yaw": dict(yaw_signal.properties),
            "turn_response": dict(turn_signal.properties),
            "control_recorded": recorded,
            "interval_control": None if control is None else [control[0], control[1]],
        },
    )
