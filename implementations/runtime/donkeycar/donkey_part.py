from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from autonomy.decision import DecisionFrameContext
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.engine import AutonomyControl
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot

ONBOARD_OBSERVATION_SNAPSHOT_SCHEMA = "automa_onboard_observation_snapshot_v0"
DEFAULT_OBSERVATION_INTERVAL_S = 0.5


def timestamp_ms() -> int:
    return int(time.time() * 1000)


def detach_image(image_array: Any) -> Any:
    """Copy array-like camera memory so later Donkey writes cannot mutate it."""
    if image_array is None:
        return None
    copy = getattr(image_array, "copy", None)
    if callable(copy):
        try:
            return copy()
        except Exception:
            return image_array
    return image_array


@dataclass
class LatestObservationSnapshot:
    """One detached onboard observation retained in memory for inspection."""

    frame_id: str
    frame_index: int
    captured_at_ms: int
    completed_at_ms: int
    mode: str
    status: str
    image: Any
    control: dict[str, Any]
    cycle: dict[str, Any] | None
    error: str | None = None
    duration_ms: int = 0
    skipped_since_previous: int = 0

    def to_status_dict(self) -> dict[str, Any]:
        """Bounded status view without the raw image payload."""
        perception = None if self.cycle is None else self.cycle.get("perception")
        return {
            "schema": ONBOARD_OBSERVATION_SNAPSHOT_SCHEMA,
            "frame_id": self.frame_id,
            "frame_index": self.frame_index,
            "captured_at_ms": self.captured_at_ms,
            "completed_at_ms": self.completed_at_ms,
            "mode": self.mode,
            "status": self.status,
            "error": self.error,
            "control": deepcopy(self.control),
            "has_image": self.image is not None,
            "duration_ms": self.duration_ms,
            "skipped_since_previous": self.skipped_since_previous,
            "cycle_schema": None if self.cycle is None else self.cycle.get("schema"),
            "perception_status": None if not isinstance(perception, dict) else perception.get("status"),
        }


class AutonomyPilotPart:
    """Adapt Donkey image memory to the shared cycle with always-on observation.

    Intended to run on every Donkey loop tick. Full decision cycles execute only
    at a bounded cadence and always consume the newest available frame. Manual
    ``user`` mode remains the movement authority: this part emits zero pilot
    outputs while mode is manual, regardless of engine output.
    """

    def __init__(
        self,
        *,
        host: AutonomyCycleHost,
        min_interval_s: float = DEFAULT_OBSERVATION_INTERVAL_S,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if min_interval_s < 0:
            raise ValueError("min_interval_s must be >= 0")
        self.host = host
        self.min_interval_s = float(min_interval_s)
        self._monotonic = monotonic or time.monotonic
        self.frame_index = 0
        self.processed_count = 0
        self.skipped_count = 0
        self._skips_since_previous = 0
        self._last_run_monotonic: float | None = None
        self.latest_snapshot: LatestObservationSnapshot | None = None
        self._last_pilot_steering = 0.0
        self._last_pilot_throttle = 0.0
        self._last_control = AutonomyControl(reason="observation-warming").to_dict()
        manager = getattr(self.host, "manager", None)
        self._last_engine = getattr(manager, "engine_spec", None)

        self._last_cycle: dict[str, Any] | None = None
        self.last_status: dict[str, Any] = self.status()

    def observation_status(self) -> dict[str, Any]:
        """Bounded observation counters for AutonomyManager status providers.

        Must not call back into ``AutonomyManager.status`` or
        ``AutonomyCycleHost.status``; those re-enter registered providers and
        hang the Donkey HTTP status path.
        """
        return {
            "min_interval_s": self.min_interval_s,
            "processed_count": self.processed_count,
            "skipped_count": self.skipped_count,
            "latest": (
                None
                if self.latest_snapshot is None
                else self.latest_snapshot.to_status_dict()
            ),
        }

    def status(self) -> dict[str, Any]:
        manager = getattr(self.host, "manager", None)
        return {
            "engine": getattr(manager, "engine_spec", self._last_engine),
            "observation": self.observation_status(),
            "latest_cycle_schema": (
                None
                if self._last_cycle is None
                else self._last_cycle.get("schema")
            ),
        }

    def run(
        self,
        image_array=None,
        mode: str = "user",
        user_steering: float = 0.0,
        user_throttle: float = 0.0,
    ):
        mode_name = mode or "user"
        now = self._monotonic()
        if (
            self._last_run_monotonic is not None
            and (now - self._last_run_monotonic) < self.min_interval_s
        ):
            self.skipped_count += 1
            self._skips_since_previous += 1
            self.last_status = self.status()
            return self._held_outputs(mode_name)

        self._last_run_monotonic = now
        captured_at_ms = timestamp_ms()
        frame_id = f"donkey_frame_{self.frame_index:06d}"
        detached = detach_image(image_array)
        sensor_snapshot = SensorSnapshot(
            read_id=frame_id,
            readings={
                FRONT_CAMERA_SENSOR_ID: SensorReading(
                    sensor_id=FRONT_CAMERA_SENSOR_ID,
                    sensor_kind="camera",
                    captured_at_ms=captured_at_ms,
                    value=detached,
                    metadata={"source": "donkeycar_vehicle_memory"},
                )
            },
            started_at_ms=captured_at_ms,
            completed_at_ms=captured_at_ms,
            metadata={"runtime": "donkeycar"},
        )

        try:
            cycle_result = self.host.run(
                DecisionFrameContext(
                    frame_id=frame_id,
                    frame_index=self.frame_index,
                    timestamp_ms=captured_at_ms,
                    sensor_snapshot=sensor_snapshot,
                    mode=mode_name,
                    user_steering=float(user_steering or 0.0),
                    user_throttle=float(user_throttle or 0.0),
                    metadata={
                        "runtime": "donkeycar",
                        "control_application": "donkey_drive_mode",
                        "observation_cadence_s": self.min_interval_s,
                    },
                )
            )
            control = cycle_result.control
            cycle_dict = cycle_result.to_dict()
            completed_at_ms = cycle_result.completed_at_ms
            duration_ms = cycle_result.duration_ms
            status = "ok"
            error = None
        except Exception as exc:
            control = AutonomyControl(reason="observation-cycle-error")
            cycle_dict = None
            completed_at_ms = timestamp_ms()
            duration_ms = max(0, completed_at_ms - captured_at_ms)
            status = "error"
            error = f"{type(exc).__name__}: {exc}"

        pilot_steering, pilot_throttle = self._pilot_outputs(mode_name, control)
        self._last_pilot_steering = pilot_steering
        self._last_pilot_throttle = pilot_throttle
        self._last_control = control.to_dict()
        manager = getattr(self.host, "manager", None)
        self._last_engine = getattr(manager, "engine_spec", self._last_engine)
        if self._last_engine is None:
            host_status = getattr(self.host, "status", None)
            if callable(host_status):
                engine_info = host_status().get("engine")
                if isinstance(engine_info, dict):
                    self._last_engine = engine_info.get("engine")
                elif isinstance(engine_info, str):
                    self._last_engine = engine_info
        self._last_cycle = cycle_dict
        self.latest_snapshot = LatestObservationSnapshot(
            frame_id=frame_id,
            frame_index=self.frame_index,
            captured_at_ms=captured_at_ms,
            completed_at_ms=completed_at_ms,
            mode=mode_name,
            status=status,
            image=detached,
            control=deepcopy(self._last_control),
            cycle=cycle_dict,
            error=error,
            duration_ms=duration_ms,
            skipped_since_previous=self._skips_since_previous,
        )
        self._skips_since_previous = 0
        self.frame_index += 1
        self.processed_count += 1
        self.last_status = self.status()
        return (
            pilot_steering,
            pilot_throttle,
            self._last_control,
            self._last_engine,
            cycle_dict,
        )

    def _pilot_outputs(self, mode_name: str, control: AutonomyControl) -> tuple[float, float]:
        # Manual mode keeps movement authority. Pilot memory stays zero so a
        # mode flip cannot inherit a stale non-zero autonomy command from an
        # observation-only cycle.
        if mode_name == "user":
            return 0.0, 0.0
        return float(control.steering), float(control.throttle)

    def _held_outputs(self, mode_name: str):
        if mode_name == "user":
            steering = 0.0
            throttle = 0.0
        else:
            steering = self._last_pilot_steering
            throttle = self._last_pilot_throttle
        return (
            steering,
            throttle,
            self._last_control,
            self._last_engine,
            self._last_cycle,
        )
