from __future__ import annotations

import io
import logging
import secrets
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Callable

from autonomy.decision_cycle.activation import DECISION_STEPS, activation_generation_id
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.errors import MemoryUpdateError
from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.report import delivery_values, report_from_host_result
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from .control import drive_mode, execution_mode
from autonomy.runtime.session import DEFAULT_INTERVAL_S, RunConfiguration

logger = logging.getLogger(__name__)

ONBOARD_OBSERVATION_STATE_SCHEMA = "automa_onboard_observation_state_v0"
OBSERVATION_PUBLICATION_SCHEMA = "automa_physical_observation_publication_v0"
DECISION_PUBLICATION_SCHEMA = "automa_physical_decision_publication_v0"
LATEST_FRAME_PATH = "/autonomy/observation/latest/frame.jpg"
LATEST_JSON_PATH = "/autonomy/observation/latest"
CAMERA_LATEST_FRAME_PATH = "/autonomy/camera/latest/frame.jpg"
CAMERA_LATEST_JSON_PATH = "/autonomy/camera/latest"
CAMERA_PUBLICATION_SCHEMA = "automa_physical_camera_publication_v0"
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


def stale_after_ms(interval_s: float) -> int:
    """Age beyond which a still-present result is marked stale at read time."""
    return max(1000, int(round(float(interval_s) * 2000.0)))


@dataclass
class LatestObservationState:
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
    preset: str | None = None
    decision_publication: dict[str, Any] | None = None
    decision_error: str | None = None

    def to_status_dict(self) -> dict[str, Any]:
        """Bounded status view without the raw image or full perception payload."""
        perception = None if self.cycle is None else self.cycle.get("perception")
        return {
            "schema": ONBOARD_OBSERVATION_STATE_SCHEMA,
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
            "preset": self.preset,
            "cycle_schema": None if self.cycle is None else self.cycle.get("schema"),
            "perception_status": None if not isinstance(perception, dict) else perception.get("status"),
        }


@dataclass
class LatestCameraFrame:
    """One camera sample published without waiting for a perception cycle."""

    frame_id: str
    frame_index: int
    captured_at_ms: int
    image: Any


@dataclass(frozen=True)
class _PendingFrame:
    camera: LatestCameraFrame
    mode_name: str
    user_steering: float
    user_throttle: float


class AutonomyPilotPart:
    """Adapt Donkey image memory to the shared cycle with always-on observation.

    Camera samples publish at ``interval_s`` without waiting for decisions.
    The background worker immediately takes the newest pending sample after
    each cycle. Superseded captures are counted between the samples the worker
    takes; drive ticks outside the capture cadence are not skipped frames.
    The shared runtime owns movement authority, freshness, and stopping;
    this part translates the camera and Donkey mode vocabulary.

    The decision publication is identified by the proposal, plan, and action
    activations the host runs (``decision_activations``) and the generation ID
    derived from them. Each cycle first applies selections restaged on the Pi
    (``AutonomyCycleHost.sync_selection``), as the Chase worker does, and a
    restaged proposal selection becomes the publication's generation once it
    runs.
    """

    def __init__(
        self,
        *,
        host: AutonomyCycleHost,
        interval_s: float = DEFAULT_INTERVAL_S,
        monotonic: Callable[[], float] | None = None,
        preset: str | None = None,
        vehicle_id: str | None = None,
        source_id: str | None = None,
        decision_activations: dict[str, dict[str, Any] | None] | None = None,
        generation_id: str | None = None,
        run_id: str | None = None,
        host_telemetry: Any | None = None,
        controller: Any | None = None,
    ) -> None:
        interval_s = RunConfiguration(interval_s=float(interval_s)).interval_s
        if getattr(host, "execution", None) is None:
            raise ValueError("AutonomyPilotPart needs a host from create_host()")
        self.host = host
        # Donkey's web controller. Runs this part starts or ends set its drive
        # mode; drive-mode changes the part did not make are the operator's.
        self.controller = controller
        self._drive_mode = drive_mode("manual")
        self._observed_drive_mode: str | None = None
        self.interval_s = float(interval_s)
        self.preset = preset
        self._monotonic = monotonic or time.monotonic
        self._lock = threading.RLock()
        self.frame_index = 0
        self._capture_sequence = 0
        self.frames_captured = 0
        self.processed_count = 0
        self.skipped_count = 0
        self._last_capture_monotonic: float | None = None
        self._last_decision_capture_sequence = -1
        self._pending_frame: _PendingFrame | None = None
        self._queue_generation = 0
        self._shutdown = False
        self._decision_inflight = False
        self._memory_update_halted = False
        self._inflight_frame_id: str | None = None
        self._decision_thread: threading.Thread | None = None
        self.latest_state: LatestObservationState | None = None
        # The last cycle's result, the decision-step runners that produced it,
        # and whether that cycle failed. Replacing a decision step's runner
        # retires the result.
        self._current_cycle: Any | None = None
        self._current_decision_runners: tuple[Any, ...] | None = None
        self._last_cycle_failed = False
        # A restaged proposal activation waiting for its runner to apply it.
        self._requested_proposal: Any | None = None
        self.latest_camera_frame: LatestCameraFrame | None = None
        self._last_control = AutonomyControl(reason="observation-warming").to_dict()
        # Optional final-boundary observer.  It receives only detached source
        # identity from this runtime owner; DriveMode remains responsible for
        # observing the actual user/pilot/selected values.
        self.host_telemetry = host_telemetry
        self._last_cycle: dict[str, Any] | None = None
        self.vehicle_id = vehicle_id
        self.source_id = source_id or (
            f"donkeycar:{vehicle_id}"
            if isinstance(vehicle_id, str) and vehicle_id
            else None
        )
        self.decision_activations = (
            deepcopy(dict(decision_activations))
            if isinstance(decision_activations, dict)
            else None
        )
        if generation_id is None and self.decision_activations is not None:
            try:
                generation_id = activation_generation_id(
                    self.decision_activations, prefix="decision"
                )
            except (TypeError, ValueError):
                generation_id = None
        self.generation_id = generation_id
        self.run_id = run_id or f"donkey-run-{secrets.token_hex(12)}"
        self._decision_identity_error = self._validate_decision_identity()
        self.last_status: dict[str, Any] = self.status()

    def observation_status(self) -> dict[str, Any]:
        """Bounded observation counters for the cycle host's status providers.

        Must not call back into ``AutonomyCycleHost.status``; that re-enters
        registered providers and hangs the Donkey HTTP status path.
        """
        with self._lock:
            latest = (
                None
                if self.latest_state is None
                else self.latest_state.to_status_dict()
            )
            camera = self.latest_camera_frame
            return {
                "interval_s": self.interval_s,
                "processed_count": self.processed_count,
                "skipped_count": self.skipped_count,
                "frames_captured": self.frames_captured,
                "perception_inflight": self._decision_inflight,
                "memory_update_halted": self._memory_update_halted,
                "preset": self.preset,
                "latest": latest,
                "latest_camera_frame_id": None if camera is None else camera.frame_id,
                "latest_json_path": LATEST_JSON_PATH,
                "latest_frame_path": LATEST_FRAME_PATH,
                "latest_camera_json_path": CAMERA_LATEST_JSON_PATH,
                "latest_camera_frame_path": CAMERA_LATEST_FRAME_PATH,
            }

    def status(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
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
        """Return the publisher-owned current action result at read time.

        The decision record is retained inside the same locked state as the
        camera frame. Read-time freshness is calculated from the producer's
        completion timestamp, so polling a stopped runtime cannot refresh an
        old result merely by reading it.
        """

        read_at_ms = timestamp_ms() if now_ms is None else int(now_ms)
        with self._lock:
            return self._decision_publication_from_locked_state(read_at_ms=read_at_ms)

    def reset_memory(self) -> dict[str, Any]:
        """Reset the live memory step under the same lock as observation cycles.

        Clears retained memory on the step and detaches memory from the latest
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
                report = reset()
            except Exception as exc:  # noqa: BLE001 - operator boundary
                return {
                    "ok": False,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            if report is None:
                return {
                    "ok": False,
                    "status": "absent",
                    "error": "no memory step is activated",
                }
            # Replace the published memory report until the next cycle.
            if self.latest_state is not None and isinstance(self.latest_state.cycle, dict):
                cycle = dict(self.latest_state.cycle)
                cycle["memory"] = deepcopy(report)
                self.latest_state = replace(self.latest_state, cycle=cycle)
            step = self.host.cycle.steps.memory
            step_status = step.status() if step is not None and callable(getattr(step, "status", None)) else None
            return {
                "ok": True,
                "status": "reset",
                "report": deepcopy(report),
                "memory": step_status,
            }

    def publish_latest_frame_jpeg(self) -> tuple[bytes | None, dict[str, Any]]:
        """Return the exact processed frame JPEG with matching publication metadata."""
        read_at_ms = timestamp_ms()
        with self._lock:
            latest = self.latest_state
            publication = self._publication_from_locked_state(read_at_ms=read_at_ms)
            image = None if latest is None else latest.image
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

    def publish_latest_camera(self, *, now_ms: int | None = None) -> dict[str, Any]:
        """Return the newest camera sample, whether or not perception has finished."""
        read_at_ms = timestamp_ms() if now_ms is None else int(now_ms)
        with self._lock:
            return self._camera_publication_from_locked_state(read_at_ms=read_at_ms)

    def publish_latest_camera_jpeg(self) -> tuple[bytes | None, dict[str, Any]]:
        """Return the JPEG for the newest camera sample, paired with its metadata."""
        read_at_ms = timestamp_ms()
        with self._lock:
            camera = self.latest_camera_frame
            publication = self._camera_publication_from_locked_state(read_at_ms=read_at_ms)
            image = None if camera is None else camera.image
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

    def wait_for_cycle(self, timeout_s: float = 5.0) -> None:
        """Block until the background perception cycle finishes, if one is running."""
        with self._lock:
            thread = self._decision_thread
        if thread is None:
            return
        thread.join(timeout_s)
        if thread.is_alive():
            raise TimeoutError("observation cycle did not finish")

    def completed_outputs(
        self, mode: str = "user"
    ) -> tuple[float, float, dict[str, Any], Any, dict[str, Any] | None]:
        """Return the pilot tuple from the last finished cycle, under the given mode."""
        return self._held_outputs(mode)

    def start(self, configuration: RunConfiguration) -> dict[str, Any]:
        with self._lock:
            self.interval_s = configuration.interval_s
            self._last_capture_monotonic = None
            self._discard_pending_locked()
        status = self.host.start(configuration)
        self._show_drive_mode(drive_mode(configuration.mode))
        return status

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._discard_pending_locked()
        status = self.host.stop()
        self._hand_back()
        return status

    def _hand_back(self) -> None:
        """Return Donkey to the operator from rest."""
        if self.controller is not None:
            self.controller.angle = 0.0
            self.controller.throttle = 0.0
        self._show_drive_mode(drive_mode("manual"))

    def _show_drive_mode(self, mode: str) -> None:
        self._drive_mode = mode
        if self.controller is not None:
            # The latch outlasts the drive loop feeding user/mode back in.
            self.controller.mode = mode
            self.controller.mode_latch = mode

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            self._discard_pending_locked()
        self.host.close()

    def _camera_publication_from_locked_state(self, *, read_at_ms: int) -> dict[str, Any]:
        camera = self.latest_camera_frame
        threshold_ms = stale_after_ms(self.interval_s)
        observation = self.latest_state
        perception_frame_id = None if observation is None else observation.frame_id
        if camera is None:
            health = PUBLICATION_HEALTH_WARMING if self.frames_captured == 0 else PUBLICATION_HEALTH_ABSENT
            perception_state = "pending" if self._decision_inflight else "absent"
            return {
                "schema": CAMERA_PUBLICATION_SCHEMA,
                "ok": False,
                "health": health,
                "read_at_ms": read_at_ms,
                "result_age_ms": None,
                "stale_after_ms": threshold_ms,
                "frame": None,
                "frames_captured": self.frames_captured,
                "perception_state": perception_state,
                "perception_frame_id": perception_frame_id,
                "perception_inflight": self._decision_inflight,
                "latest_camera_json_path": CAMERA_LATEST_JSON_PATH,
                "latest_camera_frame_path": CAMERA_LATEST_FRAME_PATH,
            }
        age_ms = max(0, read_at_ms - int(camera.captured_at_ms))
        if camera.image is None:
            health = PUBLICATION_HEALTH_UNAVAILABLE
        elif age_ms > threshold_ms:
            health = PUBLICATION_HEALTH_STALE
        else:
            health = PUBLICATION_HEALTH_HEALTHY
        if self._decision_inflight:
            perception_state = "pending"
        elif perception_frame_id == camera.frame_id:
            perception_state = "matched"
        elif perception_frame_id is None:
            perception_state = "absent"
        else:
            perception_state = "behind"
        return {
            "schema": CAMERA_PUBLICATION_SCHEMA,
            "ok": health in {PUBLICATION_HEALTH_HEALTHY, PUBLICATION_HEALTH_STALE},
            "health": health,
            "read_at_ms": read_at_ms,
            "result_age_ms": age_ms,
            "stale_after_ms": threshold_ms,
            "frame": {
                "frame_id": camera.frame_id,
                "frame_index": camera.frame_index,
                "captured_at_ms": camera.captured_at_ms,
                "has_image": camera.image is not None,
                "frame_path": CAMERA_LATEST_FRAME_PATH,
            },
            "frames_captured": self.frames_captured,
            "perception_state": perception_state,
            "perception_frame_id": perception_frame_id,
            "perception_inflight": self._decision_inflight,
            "latest_camera_json_path": CAMERA_LATEST_JSON_PATH,
            "latest_camera_frame_path": CAMERA_LATEST_FRAME_PATH,
        }

    def _store_camera_frame_locked(self, image: Any, captured_at_ms: int) -> LatestCameraFrame:
        frame = LatestCameraFrame(
            frame_id=f"donkey_frame_{self._capture_sequence:06d}",
            frame_index=self._capture_sequence,
            captured_at_ms=captured_at_ms,
            image=image,
        )
        self.latest_camera_frame = frame
        self._capture_sequence += 1
        self.frames_captured += 1
        return frame

    def _discard_pending_locked(self) -> None:
        # Stop/restart discards are not supersessions between decision frames.
        self._pending_frame = None
        self._last_decision_capture_sequence = self._capture_sequence - 1
        self._queue_generation += 1

    def _publication_from_locked_state(self, *, read_at_ms: int) -> dict[str, Any]:
        """Build a publication from the currently locked state and counters."""
        latest = self.latest_state
        processed_count = self.processed_count
        skipped_count = self.skipped_count
        interval_s = self.interval_s
        preset = self.preset
        generation_id = self.generation_id
        threshold_ms = stale_after_ms(interval_s)

        if latest is None:
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
                "interval_s": interval_s,
                "frames_captured": self.frames_captured,
                "processed_count": processed_count,
                "skipped_count": skipped_count,
                "preset": preset,
                "generation_id": generation_id,
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

        age_ms = max(0, read_at_ms - int(latest.completed_at_ms))
        if latest.status == "error":
            health = PUBLICATION_HEALTH_ERROR
        elif latest.image is None:
            health = PUBLICATION_HEALTH_UNAVAILABLE
        elif age_ms > threshold_ms:
            health = PUBLICATION_HEALTH_STALE
        else:
            health = PUBLICATION_HEALTH_HEALTHY

        perception = None if latest.cycle is None else deepcopy(latest.cycle.get("perception"))
        observation = None if latest.cycle is None else deepcopy(latest.cycle.get("observation"))
        # Republish the memory report through the cycle publication.
        memory = None if latest.cycle is None else deepcopy(latest.cycle.get("memory"))
        return {
            "schema": OBSERVATION_PUBLICATION_SCHEMA,
            "ok": health in {PUBLICATION_HEALTH_HEALTHY, PUBLICATION_HEALTH_STALE},
            "health": health,
            "read_at_ms": read_at_ms,
            "result_age_ms": age_ms,
            "stale_after_ms": threshold_ms,
            "interval_s": interval_s,
            "frames_captured": self.frames_captured,
            "processed_count": processed_count,
            "skipped_count": skipped_count,
            "preset": preset or latest.preset,
            "generation_id": generation_id,
            "frame": {
                "frame_id": latest.frame_id,
                "frame_index": latest.frame_index,
                "captured_at_ms": latest.captured_at_ms,
                "completed_at_ms": latest.completed_at_ms,
                "has_image": latest.image is not None,
                "frame_path": LATEST_FRAME_PATH,
            },
            "control": deepcopy(latest.control),
            "mode": latest.mode,
            "status": latest.status,
            "error": latest.error,
            "duration_ms": latest.duration_ms,
            "skipped_since_previous": latest.skipped_since_previous,
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
            if self.decision_activations is None:
                return "decision_activations_missing"
            if set(self.decision_activations) != set(DECISION_STEPS):
                return "decision_activations_invalid"
            if self.generation_id is None:
                return "generation_id_missing"
            require_ascii_id(self.generation_id, field_name="generation_id")
            require_ascii_id(self.run_id, field_name="run_id")
        except (TypeError, ValueError):
            return "decision_identity_invalid"
        return None

    def _sync_selection(self) -> None:
        sync = getattr(self.host, "sync_selection", None)
        if not callable(sync):
            return
        requested = sync().get("proposal")
        if requested is not None:
            self._requested_proposal = requested

    def _adopt_requested_proposal(self) -> None:
        """Publish under the restaged proposal's generation once its runner applies it."""

        requested = self._requested_proposal
        steps = getattr(getattr(self.host, "cycle", None), "steps", None)
        runner = getattr(steps, "proposal", None)
        if requested is None or runner is None or self.decision_activations is None:
            return
        if tuple(getattr(runner, "plugin_ids", ())) != tuple(requested.plugins):
            return
        activations = {**self.decision_activations, "proposal": requested.to_payload()}
        try:
            generation_id = activation_generation_id(activations, prefix="decision")
        except (TypeError, ValueError):
            return
        with self._lock:
            self.decision_activations = activations
            self.generation_id = generation_id
            self._requested_proposal = None
            self._decision_identity_error = self._validate_decision_identity()
        store = getattr(self.host_telemetry, "store", None)
        if store is not None:
            store.generation_id = generation_id
        logger.info("Decision generation: %s (restaged)", generation_id)

    def _decision_runners(self) -> tuple[Any, ...]:
        steps = getattr(getattr(self.host, "cycle", None), "steps", None)
        return tuple(getattr(steps, step, None) for step in DECISION_STEPS)

    def _runners_current(self) -> bool:
        current = self._current_decision_runners
        return current is not None and all(
            a is b for a, b in zip(current, self._decision_runners())
        )

    def _current_decision_result(self) -> Any | None:
        """Return the last cycle's action while the steps that produced it are active."""

        if self._current_cycle is None or not self._runners_current():
            return None
        return self._current_cycle.action

    def _current_decision_failed(self) -> bool:
        return self._last_cycle_failed and self._runners_current()

    def _record_current_cycle(self, cycle: Any | None, *, failed: bool) -> None:
        self._current_cycle = cycle
        self._current_decision_runners = self._decision_runners()
        action = None if cycle is None else cycle.action
        self._last_cycle_failed = failed or (
            action is not None and getattr(action, "status", "ok") != "ok"
        )

    def _capture_decision_publication(
        self,
        *,
        frame_id: str,
        frame_index: int,
        timestamp_ms_value: int,
        published_at_ms: int,
        status: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Capture the shared report for this completed host cycle.

        The document is the same ``vehicle_report_v0`` Chase writes. PiCar-only
        identity (source, activation, drive mode, and the capture frame) goes
        in ``values``. A failed cycle stays unpublished.
        """

        if status != "ok":
            return None, "failed_step"
        if self._decision_identity_error is not None:
            return None, self._decision_identity_error
        result = self._current_decision_result()
        if result is None:
            if self._current_decision_failed():
                return None, "failed_step"
            return None, "missing_result"
        if getattr(result, "status", "ok") != "ok":
            return None, "failed_step"
        result_frame_id = getattr(result, "frame_id", None)
        if result_frame_id != frame_id:
            return None, "mismatched_frame"
        records = self._current_cycle
        proposal = None if records is None else records.proposal
        plan = None if records is None else records.plan
        if proposal is None or plan is None or records.application is None:
            return None, "incomplete_result"
        if getattr(proposal, "status", None) != "ok" or getattr(result, "status", None) != "ok":
            return None, "incomplete_result"
        source = getattr(proposal, "source", None)
        try:
            source_export = source.to_dict() if source is not None else None
        except Exception:
            return None, "incomplete_result"
        if not isinstance(source_export, dict):
            return None, "incomplete_result"
        if (
            source_export.get("frame_id") != frame_id
            or source_export.get("frame_index") != frame_index
            or source_export.get("timestamp_ms") != timestamp_ms_value
        ):
            return None, "mismatched_frame"
        try:
            report = report_from_host_result(
                records,
                vehicle_id=self.vehicle_id,
                run_id=self.run_id,
                generation_id=self.generation_id,
                published_at_ms=published_at_ms,
                values={
                    "source_id": self.source_id,
                    "drive_mode": self._drive_mode,
                    "activation": {
                        "generation_id": self.generation_id,
                        "steps": deepcopy(self.decision_activations),
                    },
                    "source_frame": {
                        "frame_id": frame_id,
                        "frame_index": frame_index,
                        "captured_at_ms": timestamp_ms_value,
                        "completed_at_ms": published_at_ms,
                    },
                    **delivery_values(records.application),
                },
            )
        except (TypeError, ValueError):
            return None, "incomplete_result"
        return report.to_dict(), None

    def _decision_unavailable(
        self,
        *,
        reason: str,
        read_at_ms: int,
        result_age_ms: int | None = None,
    ) -> dict[str, Any]:
        threshold_ms = stale_after_ms(self.interval_s)
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

        latest = self.latest_state
        threshold_ms = stale_after_ms(self.interval_s)
        if latest is None:
            return self._decision_unavailable(reason="missing", read_at_ms=read_at_ms)
        decision = latest.decision_publication
        if decision is None:
            return self._decision_unavailable(
                reason=latest.decision_error or "unavailable",
                read_at_ms=read_at_ms,
            )
        # Replacing a decision step retires its result. Never
        # replay the detached result retained by a prior sensor frame.
        current = self._current_decision_result()
        if current is None or getattr(current, "status", "ok") != "ok":
            reason = (
                "failed_step"
                if self._current_decision_failed() or current is not None
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
        mode = mode or drive_mode("manual")
        mode_name = execution_mode(mode)
        operator_changed = mode != self._observed_drive_mode and mode != self._drive_mode
        self._observed_drive_mode = mode
        if operator_changed:
            self._drive_mode = mode
            with self._lock:
                self._discard_pending_locked()
            if mode_name == "autonomy":
                self.host.start(RunConfiguration(interval_s=self.interval_s))
            else:
                self.host.stop()
        elif execution_mode(self._drive_mode) == "autonomy" and self.host.run_state != "running":
            # The host ended the run: bounded decisions completed or a cycle failed.
            self._hand_back()
        with self._lock:
            now = self._monotonic()
            if not self._shutdown and (
                self._last_capture_monotonic is None
                or now - self._last_capture_monotonic >= self.interval_s
            ):
                self._last_capture_monotonic = now
                camera = self._store_camera_frame_locked(
                    detach_image(image_array), timestamp_ms()
                )
                self._pending_frame = _PendingFrame(
                    camera, mode_name, float(user_steering or 0.0), float(user_throttle or 0.0)
                )
                if not self._decision_inflight and not self._memory_update_halted:
                    self._decision_inflight = True
                    self._inflight_frame_id = camera.frame_id
                    thread = threading.Thread(
                        target=self._decision_worker, name="automa-decision-worker", daemon=True
                    )
                    self._decision_thread = thread
                    thread.start()
        self._publish_host_source_frame()
        self.last_status = self.status()
        return self._held_outputs(mode_name)

    def _decision_worker(self) -> None:
        try:
            while True:
                with self._lock:
                    pending = self._pending_frame
                    if pending is None or self._memory_update_halted or self._shutdown:
                        # Release ownership atomically with checking the slot,
                        # so a concurrent capture can start another worker.
                        self._decision_inflight = False
                        self._inflight_frame_id = None
                        self._decision_thread = None
                        return
                    self._pending_frame = None
                    camera = pending.camera
                    skipped_since_previous = (
                        camera.frame_index - self._last_decision_capture_sequence - 1
                    )
                    self._last_decision_capture_sequence = camera.frame_index
                    self.skipped_count += skipped_since_previous
                    self._inflight_frame_id = camera.frame_id
                    generation = self._queue_generation
                    was_running = getattr(self.host, "run_state", None) == "running"
                succeeded = self._process_frame(
                    image=camera.image,
                    mode_name=pending.mode_name,
                    user_steering=pending.user_steering,
                    user_throttle=pending.user_throttle,
                    captured_at_ms=camera.captured_at_ms,
                    frame_id=camera.frame_id,
                    frame_index=camera.frame_index,
                    skipped_since_previous=skipped_since_previous,
                )
                with self._lock:
                    ended = was_running and self.host.run_state != "running"
                    if generation == self._queue_generation and (not succeeded or ended):
                        self._discard_pending_locked()
        finally:
            with self._lock:
                if self._decision_thread is threading.current_thread():
                    self._decision_inflight = False
                    self._inflight_frame_id = None
                    self._decision_thread = None
            self.last_status = self.status()

    def _process_frame(
        self,
        *,
        image: Any,
        mode_name: str,
        user_steering: float,
        user_throttle: float,
        captured_at_ms: int,
        frame_id: str,
        frame_index: int,
        skipped_since_previous: int,
    ) -> bool:
        detached = image
        sensor_frame = SensorFrame(
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
            try:
                self._sync_selection()
                cycle_result = self.host.run(
                    DecisionFrameContext(
                        frame_id=frame_id,
                        frame_index=frame_index,
                        timestamp_ms=captured_at_ms,
                        sensor_frame=sensor_frame,
                        mode=mode_name,
                        user_steering=float(user_steering or 0.0),
                        user_throttle=float(user_throttle or 0.0),
                        metadata={
                            "runtime": "donkeycar",
                            "control_application": "shared_execution",
                            "capture_interval_s": self.interval_s,
                            "capture_sequence": frame_index,
                            "skipped_since_previous": skipped_since_previous,
                        },
                    )
                )
                control = cycle_result.control
                cycle_dict = cycle_result.to_dict()
                self._record_current_cycle(cycle_result, failed=False)
                self._adopt_requested_proposal()
                completed_at_ms = cycle_result.completed_at_ms
                duration_ms = cycle_result.duration_ms
                status = "ok"
                error = None
            except Exception as exc:
                logger.exception("Autonomy decision cycle failed for frame %s", frame_id)
                self._record_current_cycle(None, failed=True)
                control = AutonomyControl(reason="observation-cycle-error")
                if isinstance(exc, MemoryUpdateError):
                    with self._lock:
                        self._memory_update_halted = True
                        self._last_control = control.to_dict()
                        self._last_cycle = None
                cycle_dict = None
                completed_at_ms = timestamp_ms()
                duration_ms = max(0, completed_at_ms - captured_at_ms)
                status = "error"
                error = f"{type(exc).__name__}: {exc}"

            decision_publication, decision_error = self._capture_decision_publication(
                frame_id=frame_id,
                frame_index=frame_index,
                timestamp_ms_value=captured_at_ms,
                published_at_ms=completed_at_ms,
                status=status,
            )

            control_dict = (
                cycle_result.control_record() if cycle_dict is not None else {
                    **control.to_dict(), "applied": False,
                    "application": self.host.execution.status()["application"],
                }
            )
            latest = LatestObservationState(
                frame_id=frame_id,
                frame_index=frame_index,
                captured_at_ms=captured_at_ms,
                completed_at_ms=completed_at_ms,
                mode=mode_name,
                status=status,
                image=detached,
                control=deepcopy(control_dict),
                cycle=cycle_dict,
                error=error,
                duration_ms=duration_ms,
                skipped_since_previous=skipped_since_previous,
                preset=self.preset,
                decision_publication=decision_publication,
                decision_error=decision_error,
            )
            with self._lock:
                self._last_control = control_dict
                self._last_cycle = cycle_dict
                self.latest_state = latest
                self.frame_index = frame_index + 1
                self.processed_count += 1
        finally:
            self._publish_host_source_frame()
        self.last_status = self.status()
        return status == "ok"

    def _publish_host_source_frame(self) -> None:
        """Hand off the latest completed source frame to the final observer.

        This is a bounded in-process identity handoff.  The source frame is
        copied from the runtime-owned latest observation state and is never
        created by the DriveMode observer.  A held cadence tick therefore
        repeats the same source identity instead of becoming a new frame.
        """

        observer = self.host_telemetry
        setter = getattr(observer, "set_source_frame", None)
        if not callable(setter):
            return
        with self._lock:
            latest = self.latest_state
            source_frame = (
                None
                if latest is None
                else {
                    "frame_id": latest.frame_id,
                    "frame_index": latest.frame_index,
                    "captured_at_ms": latest.captured_at_ms,
                    "completed_at_ms": latest.completed_at_ms,
                }
            )
        try:
            setter(source_frame)
        except Exception:
            # Telemetry is diagnostic only and must never change host outputs.
            return

    def _held_outputs(self, mode_name: str):
        output = self.host.execution.output()
        with self._lock:
            control = deepcopy(self._last_control)
            generation_id = self.generation_id
            cycle = None if self._last_cycle is None else deepcopy(self._last_cycle)
        return (output.steering, output.throttle, control, generation_id, cycle)
