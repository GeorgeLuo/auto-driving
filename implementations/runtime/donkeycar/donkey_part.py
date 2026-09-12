from __future__ import annotations

import io
import secrets
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Callable

from autonomy.decision import DecisionFrameContext
from autonomy.decision.shadow_ids import require_ascii_id, require_safe_int
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.engine import AutonomyControl
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot

ONBOARD_OBSERVATION_SNAPSHOT_SCHEMA = "automa_onboard_observation_snapshot_v0"
OBSERVATION_PUBLICATION_SCHEMA = "automa_physical_observation_publication_v0"
DECISION_PUBLICATION_SCHEMA = "automa_physical_decision_publication_v0"
DEFAULT_OBSERVATION_INTERVAL_S = 0.5
LATEST_FRAME_PATH = "/autonomy/observation/latest/frame.jpg"
LATEST_JSON_PATH = "/autonomy/observation/latest"
DECISION_LATEST_PATH = "/autonomy/decision/latest"

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
    decision_publication: dict[str, Any] | None = None
    decision_error: str | None = None

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
        vehicle_id: str | None = None,
        source_id: str | None = None,
        activation_engine_id: str | None = None,
        activation_activated_at_ms: int | None = None,
        activation_engine_config: dict[str, Any] | None = None,
        generation_id: str | None = None,
        run_id: str | None = None,
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
        manager_config = getattr(manager, "engine_config", {})
        if activation_engine_config is None and isinstance(manager_config, dict):
            activation_engine_config = manager_config
        self.vehicle_id = vehicle_id
        self.source_id = source_id or (
            f"donkeycar:{vehicle_id}"
            if isinstance(vehicle_id, str) and vehicle_id
            else None
        )
        self.activation_engine_id = activation_engine_id
        self.activation_activated_at_ms = activation_activated_at_ms
        self.activation_engine_config = (
            deepcopy(activation_engine_config)
            if isinstance(activation_engine_config, dict)
            else None
        )
        self.generation_id = generation_id or (
            f"{activation_engine_id}:{activation_activated_at_ms}"
            if isinstance(activation_engine_id, str)
            and activation_engine_id
            and type(activation_activated_at_ms) is int
            else None
        )
        self.run_id = run_id or f"donkey-run-{secrets.token_hex(12)}"
        self._decision_identity_error = self._validate_decision_identity()
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

    def publish_decision_latest(self, *, now_ms: int | None = None) -> dict[str, Any]:
        """Return the publisher-owned current shadow result at read time.

        The decision record is retained inside the same locked snapshot as the
        camera frame. Read-time freshness is calculated from the producer's
        completion timestamp, so polling a stopped runtime cannot refresh an
        old result merely by reading it.
        """

        read_at_ms = timestamp_ms() if now_ms is None else int(now_ms)
        with self._lock:
            return self._decision_publication_from_locked_state(read_at_ms=read_at_ms)

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

    def _validate_decision_identity(self) -> str | None:
        """Validate runtime-owned identity supplied to the publication owner."""

        try:
            if self.vehicle_id is None:
                return "vehicle_id_missing"
            require_ascii_id(self.vehicle_id, field_name="vehicle_id")
            if self.source_id is None:
                return "source_id_missing"
            require_ascii_id(self.source_id, field_name="source_id")
            if self.activation_engine_id is None:
                return "activation_engine_id_missing"
            require_ascii_id(
                self.activation_engine_id,
                field_name="activation_engine_id",
            )
            if self.activation_activated_at_ms is None:
                return "activation_activated_at_ms_missing"
            require_safe_int(
                self.activation_activated_at_ms,
                field_name="activation_activated_at_ms",
            )
            if self.activation_engine_config is None:
                return "activation_engine_config_missing"
            if not isinstance(self.activation_engine_config, dict):
                return "activation_engine_config_invalid"
            if self.generation_id is None:
                return "generation_id_missing"
            require_ascii_id(self.generation_id, field_name="generation_id")
            require_ascii_id(self.run_id, field_name="run_id")
        except (TypeError, ValueError):
            return "decision_identity_invalid"
        return None

    def _current_decision_result(self) -> Any | None:
        """Read the current result from the active engine without inventing one."""

        manager = getattr(self.host, "manager", None)
        engine = getattr(manager, "engine", None)
        getter = getattr(engine, "get_current_cycle_result", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                return None
        # Keep compatibility with same-owner test/fallback engines that expose
        # only the adapter's legacy diagnostic attribute.
        return getattr(engine, "last_cycle_result", None)

    def _capture_decision_publication(
        self,
        *,
        frame_id: str,
        frame_index: int,
        timestamp_ms_value: int,
        published_at_ms: int,
        status: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Capture a detached, identity-decorated typed shadow cycle."""

        if status != "ok":
            return None, "failed_step"
        if self._decision_identity_error is not None:
            return None, self._decision_identity_error
        result = self._current_decision_result()
        if result is None:
            manager = getattr(self.host, "manager", None)
            engine = getattr(manager, "engine", None)
            if getattr(engine, "last_cycle_error_reason", None):
                return None, "failed_step"
            return None, "missing_result"
        if getattr(result, "status", "ok") != "ok":
            return None, "failed_step"
        result_frame_id = getattr(result, "frame_id", None)
        if result_frame_id != frame_id:
            return None, "mismatched_frame"
        try:
            cycle = result.to_dict()
        except Exception:
            return None, "incomplete_result"
        if not isinstance(cycle, dict) or cycle.get("status") != "ok":
            return None, "incomplete_result"
        source = cycle.get("source")
        if not isinstance(source, dict):
            return None, "incomplete_result"
        if (
            source.get("frame_id") != frame_id
            or source.get("frame_index") != frame_index
            or source.get("timestamp_ms") != timestamp_ms_value
        ):
            return None, "mismatched_frame"
        return {
            "vehicle_id": self.vehicle_id,
            "source_id": self.source_id,
            "run_id": self.run_id,
            "activation_engine_id": self.activation_engine_id,
            "activation_activated_at_ms": self.activation_activated_at_ms,
            "generation_id": self.generation_id,
            "frame_id": frame_id,
            "frame_index": frame_index,
            "timestamp_ms": timestamp_ms_value,
            "published_at_ms": published_at_ms,
            "activation": {
                "engine_id": self.activation_engine_id,
                "activated_at_ms": self.activation_activated_at_ms,
                "generation_id": self.generation_id,
                "engine_config": deepcopy(self.activation_engine_config),
            },
            "cycle": cycle,
        }, None

    def _decision_unavailable(
        self,
        *,
        reason: str,
        read_at_ms: int,
        result_age_ms: int | None = None,
    ) -> dict[str, Any]:
        threshold_ms = stale_after_ms(self.min_interval_s)
        return {
            "schema": DECISION_PUBLICATION_SCHEMA,
            "ok": False,
            "status": "unavailable",
            "reason": reason,
            "read_at_ms": read_at_ms,
            "result_age_ms": result_age_ms,
            "stale_after_ms": threshold_ms,
            "decision": None,
        }

    def _decision_publication_from_locked_state(self, *, read_at_ms: int) -> dict[str, Any]:
        """Build the physical decision wire payload, fail-closed at read time."""

        snap = self.latest_snapshot
        threshold_ms = stale_after_ms(self.min_interval_s)
        if snap is None:
            return self._decision_unavailable(reason="missing", read_at_ms=read_at_ms)
        decision = snap.decision_publication
        if decision is None:
            return self._decision_unavailable(
                reason=snap.decision_error or "unavailable",
                read_at_ms=read_at_ms,
            )
        # A reset/reload clears the adapter's current result. Never replay the
        # detached result retained by a prior camera snapshot.
        current = self._current_decision_result()
        if current is None or getattr(current, "status", "ok") != "ok":
            manager = getattr(self.host, "manager", None)
            engine = getattr(manager, "engine", None)
            reason = (
                "failed_step"
                if getattr(engine, "last_cycle_error_reason", None) == "failed_step"
                or current is not None
                else "reset"
            )
            return self._decision_unavailable(reason=reason, read_at_ms=read_at_ms)
        if getattr(current, "frame_id", None) != decision.get("frame_id"):
            return self._decision_unavailable(
                reason="mismatched_frame",
                read_at_ms=read_at_ms,
            )
        published_at_ms = decision.get("published_at_ms")
        if type(published_at_ms) is not int:
            return self._decision_unavailable(
                reason="incomplete",
                read_at_ms=read_at_ms,
            )
        age_ms = read_at_ms - published_at_ms
        if age_ms < 0:
            reason = "future_dated"
        elif age_ms > threshold_ms:
            reason = "expired"
        else:
            reason = None
        if reason is not None:
            return self._decision_unavailable(
                reason=reason,
                read_at_ms=read_at_ms,
                result_age_ms=age_ms,
            )
        return {
            "schema": DECISION_PUBLICATION_SCHEMA,
            "ok": True,
            "status": "ready",
            "reason": "",
            "read_at_ms": read_at_ms,
            "result_age_ms": age_ms,
            "stale_after_ms": threshold_ms,
            "decision": deepcopy(decision),
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

        decision_publication, decision_error = self._capture_decision_publication(
            frame_id=frame_id,
            frame_index=self.frame_index,
            timestamp_ms_value=captured_at_ms,
            published_at_ms=completed_at_ms,
            status=status,
        )

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
            decision_publication=decision_publication,
            decision_error=decision_error,
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
