from __future__ import annotations

import io
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Callable

from autonomy.decision import DecisionFrameContext
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.engine import AutonomyControl
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot

ONBOARD_OBSERVATION_SNAPSHOT_SCHEMA = "automa_onboard_observation_snapshot_v0"
OBSERVATION_PUBLICATION_SCHEMA = "automa_physical_observation_publication_v0"
DEFAULT_OBSERVATION_INTERVAL_S = 0.5
LATEST_FRAME_PATH = "/autonomy/observation/latest/frame.jpg"
LATEST_JSON_PATH = "/autonomy/observation/latest"

PUBLICATION_HEALTH_ABSENT = "absent"
PUBLICATION_HEALTH_WARMING = "warming"
PUBLICATION_HEALTH_HEALTHY = "healthy"
PUBLICATION_HEALTH_STALE = "stale"
PUBLICATION_HEALTH_UNAVAILABLE = "unavailable"
PUBLICATION_HEALTH_ERROR = "error"


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


def encode_jpeg(image_array: Any) -> bytes:
    """Encode an RGB/BGR-like array as JPEG bytes without writing to disk."""
    if image_array is None:
        raise ValueError("image_array is required")
    try:
        from PIL import Image
        import numpy as np

        array = np.asarray(image_array)
        if array.ndim == 2:
            image = Image.fromarray(array.astype("uint8"), mode="L")
        elif array.ndim == 3 and array.shape[2] >= 3:
            image = Image.fromarray(array[:, :, :3].astype("uint8"), mode="RGB")
        else:
            raise ValueError(f"unsupported image shape {array.shape}")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()
    except Exception:
        import cv2
        import numpy as np

        array = np.asarray(image_array)
        if array.ndim == 2:
            ok, encoded = cv2.imencode(".jpg", array)
        elif array.ndim == 3 and array.shape[2] >= 3:
            # Camera memory is RGB in this project; OpenCV expects BGR.
            bgr = array[:, :, :3][:, :, ::-1]
            ok, encoded = cv2.imencode(".jpg", bgr)
        else:
            raise ValueError(f"unsupported image shape {array.shape}")
        if not ok:
            raise RuntimeError("cv2.imencode failed")
        return encoded.tobytes()


def stale_after_ms(min_interval_s: float) -> int:
    """Age beyond which a still-present result is marked stale at read time."""
    return max(1000, int(round(float(min_interval_s) * 2000.0)))


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
    algorithm: str | None = None

    def to_status_dict(self) -> dict[str, Any]:
        """Bounded status view without the raw image or full perception payload."""
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
            "algorithm": self.algorithm,
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
        algorithm: str | None = None,
    ) -> None:
        if min_interval_s < 0:
            raise ValueError("min_interval_s must be >= 0")
        self.host = host
        self.min_interval_s = float(min_interval_s)
        self.algorithm = algorithm
        self._monotonic = monotonic or time.monotonic
        self._lock = threading.RLock()
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
        with self._lock:
            latest = (
                None
                if self.latest_snapshot is None
                else self.latest_snapshot.to_status_dict()
            )
            return {
                "min_interval_s": self.min_interval_s,
                "processed_count": self.processed_count,
                "skipped_count": self.skipped_count,
                "algorithm": self.algorithm,
                "latest": latest,
                "latest_json_path": LATEST_JSON_PATH,
                "latest_frame_path": LATEST_FRAME_PATH,
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

    def publish_latest(self, *, now_ms: int | None = None) -> dict[str, Any]:
        """Return one bounded latest frame/result publication computed at read time."""
        read_at_ms = timestamp_ms() if now_ms is None else int(now_ms)
        with self._lock:
            return self._publication_from_locked_state(read_at_ms=read_at_ms)

    def reset_memory(self) -> dict[str, Any]:
        """Reset the live memory stage under the same lock as observation cycles.

        Clears retained memory on the stage and detaches memory from the latest
        published observation so operators see an empty map immediately.
        """
        with self._lock:
            reset = getattr(self.host, "reset_memory", None)
            if not callable(reset):
                return {
                    "ok": False,
                    "status": "unavailable",
                    "error": "cycle host does not support memory reset",
                }
            try:
                snapshot = reset()
            except Exception as exc:  # noqa: BLE001 - operator boundary
                return {
                    "ok": False,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            if snapshot is None:
                return {
                    "ok": False,
                    "status": "absent",
                    "error": "no memory stage is activated",
                }
            # Detach memory from the retained publication until the next cycle.
            if self.latest_snapshot is not None and isinstance(self.latest_snapshot.cycle, dict):
                cycle = dict(self.latest_snapshot.cycle)
                cycle["memory"] = snapshot.to_dict() if hasattr(snapshot, "to_dict") else None
                self.latest_snapshot = replace(self.latest_snapshot, cycle=cycle)
            stage = self.host.cycle.stages.remember
            stage_status = stage.status() if stage is not None and callable(getattr(stage, "status", None)) else None
            return {
                "ok": True,
                "status": "reset",
                "snapshot": snapshot.to_dict() if hasattr(snapshot, "to_dict") else None,
                "memory": stage_status,
            }

    def publish_latest_frame_jpeg(self) -> tuple[bytes | None, dict[str, Any]]:
        """Return the exact processed frame JPEG with matching publication metadata."""
        read_at_ms = timestamp_ms()
        with self._lock:
            snap = self.latest_snapshot
            publication = self._publication_from_locked_state(read_at_ms=read_at_ms)
            image = None if snap is None else snap.image
        if image is None:
            return None, publication
        try:
            jpeg = encode_jpeg(image)
        except Exception as exc:
            publication = dict(publication)
            publication["health"] = PUBLICATION_HEALTH_ERROR
            publication["ok"] = False
            publication["error"] = f"{type(exc).__name__}: {exc}"
            return None, publication
        return jpeg, publication

    def _publication_from_locked_state(self, *, read_at_ms: int) -> dict[str, Any]:
        """Build a publication from the currently locked snapshot/counters."""
        snap = self.latest_snapshot
        processed_count = self.processed_count
        skipped_count = self.skipped_count
        min_interval_s = self.min_interval_s
        algorithm = self.algorithm
        engine = self._last_engine
        threshold_ms = stale_after_ms(min_interval_s)

        if snap is None:
            health = (
                PUBLICATION_HEALTH_WARMING
                if processed_count == 0
                else PUBLICATION_HEALTH_ABSENT
            )
            return {
                "schema": OBSERVATION_PUBLICATION_SCHEMA,
                "ok": False,
                "health": health,
                "read_at_ms": read_at_ms,
                "result_age_ms": None,
                "stale_after_ms": threshold_ms,
                "min_interval_s": min_interval_s,
                "processed_count": processed_count,
                "skipped_count": skipped_count,
                "algorithm": algorithm,
                "engine": engine,
                "frame": None,
                "control": None,
                "mode": None,
                "status": health,
                "error": None,
                "duration_ms": None,
                "perception": None,
                "observation": None,
                "memory": None,
                "latest_json_path": LATEST_JSON_PATH,
                "latest_frame_path": LATEST_FRAME_PATH,
            }

        age_ms = max(0, read_at_ms - int(snap.completed_at_ms))
        if snap.status == "error":
            health = PUBLICATION_HEALTH_ERROR
        elif snap.image is None:
            health = PUBLICATION_HEALTH_UNAVAILABLE
        elif age_ms > threshold_ms:
            health = PUBLICATION_HEALTH_STALE
        else:
            health = PUBLICATION_HEALTH_HEALTHY

        perception = None if snap.cycle is None else deepcopy(snap.cycle.get("perception"))
        observation = None if snap.cycle is None else deepcopy(snap.cycle.get("observation"))
        memory = None if snap.cycle is None else deepcopy(snap.cycle.get("memory"))
        return {
            "schema": OBSERVATION_PUBLICATION_SCHEMA,
            "ok": health in {PUBLICATION_HEALTH_HEALTHY, PUBLICATION_HEALTH_STALE},
            "health": health,
            "read_at_ms": read_at_ms,
            "result_age_ms": age_ms,
            "stale_after_ms": threshold_ms,
            "min_interval_s": min_interval_s,
            "processed_count": processed_count,
            "skipped_count": skipped_count,
            "algorithm": algorithm or snap.algorithm,
            "engine": engine,
            "frame": {
                "frame_id": snap.frame_id,
                "frame_index": snap.frame_index,
                "captured_at_ms": snap.captured_at_ms,
                "completed_at_ms": snap.completed_at_ms,
                "has_image": snap.image is not None,
                "frame_path": LATEST_FRAME_PATH,
            },
            "control": deepcopy(snap.control),
            "mode": snap.mode,
            "status": snap.status,
            "error": snap.error,
            "duration_ms": snap.duration_ms,
            "skipped_since_previous": snap.skipped_since_previous,
            "perception": perception,
            "observation": observation,
            "memory": memory,
            "latest_json_path": LATEST_JSON_PATH,
            "latest_frame_path": LATEST_FRAME_PATH,
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
            with self._lock:
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
        snapshot = LatestObservationSnapshot(
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
            algorithm=self.algorithm,
        )
        with self._lock:
            self.latest_snapshot = snapshot
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
