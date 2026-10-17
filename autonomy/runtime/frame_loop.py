"""The latest-frame decision loop every vehicle runtime hosts.

PiCar ticks and Chase streams submit continuously: one pending slot holds the
newest sample while one worker runs decisions sequentially. Replacing a waiting
sample increments frames_dropped. Chase atomic pulls wait for worker capacity.
The cycle host owns movement authority, freshness, stopping, and recording;
this loop owns pending frames, publications, and the applied decision identity.
"""
from __future__ import annotations

import io
import logging
import secrets
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.decision_cycle.activation import (
    DECISION_STEPS,
    activation_generation_id,
    activation_presets,
)
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.errors import MemoryUpdateError
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.recording import RunRecording
from autonomy.runtime.report import delivery_values, diagnostic_ceiling, report_from_host_result
from autonomy.runtime.session import RunConfiguration
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading

logger = logging.getLogger(__name__)

ONBOARD_OBSERVATION_STATE_SCHEMA = "automa_onboard_observation_state_v0"
OBSERVATION_PUBLICATION_SCHEMA = "automa_physical_observation_publication_v0"
DECISION_PUBLICATION_SCHEMA = "automa_physical_decision_publication_v0"
LATEST_FRAME_PATH = "/autonomy/observation/latest/frame.jpg"
OBSERVATION_TRANSACTION_PATH = "/autonomy/observation/transaction"
OBSERVATION_TRANSACTION_SCHEMA = "automa_observation_transaction_v0"
LATEST_JSON_PATH = "/autonomy/observation/latest"
CAMERA_LATEST_FRAME_PATH = "/autonomy/camera/latest/frame.jpg"
CAMERA_LATEST_JSON_PATH = "/autonomy/camera/latest"
CAMERA_PUBLICATION_SCHEMA = "automa_physical_camera_publication_v0"
DECISION_LATEST_PATH = "/autonomy/decision/latest"
DECISION_TRANSACTION_PATH = "/autonomy/decision/transaction"
DECISION_TRANSACTION_SCHEMA = "automa_decision_transaction_v0"

PUBLICATION_HEALTH_ABSENT = "absent"
PUBLICATION_HEALTH_WARMING = "warming"
PUBLICATION_HEALTH_HEALTHY = "healthy"
PUBLICATION_HEALTH_STALE = "stale"
PUBLICATION_HEALTH_UNAVAILABLE = "unavailable"
PUBLICATION_HEALTH_ERROR = "error"


def timestamp_ms() -> int:
    return int(time.time() * 1000)


def detach_image(image_array: Any) -> Any:
    """Copy array-like camera memory so later writes cannot mutate it."""
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


def stale_after_ms(cycle_duration_ms: float | None) -> int:
    """Age beyond which a still-present result is marked stale at read time."""
    if cycle_duration_ms is None:
        return 1000
    return max(1000, int(round(float(cycle_duration_ms) * 2.0)))


@dataclass
class _FrameJpegCache:
    data: bytes | None = field(default=None, repr=False)
    lock: Any = field(default_factory=threading.Lock, repr=False, compare=False)

    def get(self, image: Any) -> bytes:
        with self.lock:
            if self.data is None:
                self.data = encode_jpeg(image)
            return self.data


@dataclass
class LatestObservationState:
    """One detached observation retained in memory for inspection."""

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
    duration_ms: float = 0
    presets: dict[str, str] = field(default_factory=dict)
    decision_publication: dict[str, Any] | None = None
    decision_error: str | None = None
    context: dict[str, Any] | None = None
    jpeg_cache: _FrameJpegCache = field(default_factory=_FrameJpegCache, repr=False, compare=False)

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
            "presets": dict(self.presets),
            "cycle_schema": None if self.cycle is None else self.cycle.get("schema"),
            "perception_status": None if not isinstance(perception, dict) else perception.get("status"),
        }


@dataclass
class LatestCameraFrame:
    """One camera sample published without waiting for a decision cycle.

    ``sequence`` counts captures in this loop; ``frame_index`` is the
    vehicle's frame identity, which may skip (a simulator frame index).
    """

    frame_id: str
    frame_index: int
    captured_at_ms: int
    image: Any
    sequence: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    jpeg_cache: _FrameJpegCache = field(default_factory=_FrameJpegCache, repr=False, compare=False)
    image_loader: Callable[[], Any] | None = field(default=None, repr=False, compare=False)

    @property
    def has_image(self) -> bool:
        return self.image is not None or self.jpeg_cache.data is not None


@dataclass(frozen=True)
class _PendingFrame:
    camera: LatestCameraFrame
    mode_name: str
    user_steering: float
    user_throttle: float


class FrameLoop:
    """Feed camera samples to the shared cycle host and publish the results.

    The decision publication is identified by the proposal, plan, and action
    activations the host runs (``decision_activations``) and the generation ID
    derived from them. ``host.run`` applies restaged selections and records
    the applied identity; this loop copies it into the publication.
    """

    def __init__(
        self,
        *,
        host: AutonomyCycleHost,
        presets: dict[str, str] | None = None,
        vehicle_id: str | None = None,
        source_id: str | None = None,
        decision_activations: dict[str, dict[str, Any] | None] | None = None,
        generation_id: str | None = None,
        run_id: str | None = None,
        recording_root: Path | None = None,
        runtime: str = "frame_loop",
        camera_source: str | None = None,
        frame_prefix: str | None = None,
    ) -> None:
        if getattr(host, "execution", None) is None:
            raise ValueError(f"{type(self).__name__} needs a host with a control target")
        self.host = host
        self.runtime = runtime
        self.camera_source = camera_source or f"{runtime}_camera"
        self.frame_prefix = frame_prefix or runtime
        self.recording_root = recording_root
        # Each step's preset label, refreshed from every frame's applied activations.
        self.presets = dict(presets or {})
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._capture_ready = threading.Event()
        self._capture_ready.set()
        self.frame_index = 0
        self._capture_sequence = 0
        self.frames_received = 0
        self.frames_dropped = 0
        self.processed_count = 0
        self._cycle_duration_ms: float | None = None
        self._pending_frame: _PendingFrame | None = None
        self._queue_generation = 0
        self._shutdown = False
        self._decision_inflight = False
        self._memory_update_halted = False
        self._inflight_frame_id: str | None = None
        self._inflight_camera: LatestCameraFrame | None = None
        self._decision_thread: threading.Thread | None = None
        self.latest_state: LatestObservationState | None = None
        self._publication_revision = 0
        # The last cycle's result, the decision-step runners that produced it,
        # and whether that cycle failed. Replacing a decision step's runner
        # retires the result.
        self._current_cycle: Any | None = None
        self._current_decision_runners: tuple[Any, ...] | None = None
        self._last_cycle_failed = False
        self.latest_camera_frame: LatestCameraFrame | None = None
        self._session_frame_id: str | None = None
        self._last_control = AutonomyControl(reason="observation-warming").to_dict()
        self._last_cycle: dict[str, Any] | None = None
        self.vehicle_id = vehicle_id
        self.source_id = source_id or (
            f"{runtime}:{vehicle_id}" if isinstance(vehicle_id, str) and vehicle_id else None
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
        self.run_id = run_id or f"{runtime}-run-{secrets.token_hex(12)}"
        self._decision_identity_error = self._validate_decision_identity()
        self.last_status: dict[str, Any] = self.status()
        host.register_status_provider("observation", self.observation_status)

    # Adapter hooks. A vehicle adapter overrides these to mirror the shared
    # session in its own vocabulary; the loop never depends on them.

    def _frame_completed(self) -> None:
        pass

    def _identity_applied(self, generation_id: str) -> None:
        pass

    def observation_status(self) -> dict[str, Any]:
        """Bounded observation counters for the cycle host's status providers.

        Must not call back into ``AutonomyCycleHost.status``; that re-enters
        registered providers and hangs the status route.
        """
        with self._lock:
            latest = (
                None
                if self.latest_state is None
                else self.latest_state.to_status_dict()
            )
            camera = self.latest_camera_frame
            return {
                "runtime": self.runtime,
                "processed_count": self.processed_count,
                "frames_received": self.frames_received,
                "frames_dropped": self.frames_dropped,
                "perception_inflight": self._decision_inflight,
                "memory_update_halted": self._memory_update_halted,
                "presets": dict(self.presets),
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
        """Return the current action result at read time.

        The decision record is retained inside the same locked state as the
        camera frame. Read-time freshness is calculated from the producer's
        completion timestamp, so polling a stopped runtime cannot refresh an
        old result merely by reading it.
        """

        read_at_ms = timestamp_ms() if now_ms is None else int(now_ms)
        with self._lock:
            return self._decision_publication_from_locked_state(read_at_ms=read_at_ms)

    def publish_decision_transaction(self) -> tuple[dict[str, Any], bytes | None]:
        """Return the current decision and its image from one locked snapshot.

        A cycle can complete between two separate reads, so the image and the
        decision can come from different cycles. Cycles run back to back, so
        this happens often. Callers that publish the live view need both from
        the same completed cycle.
        """

        read_at_ms = timestamp_ms()
        with self._lock:
            publication = self._decision_publication_from_locked_state(read_at_ms=read_at_ms)
            latest = self.latest_state
            image = None if latest is None else latest.image
            decision = publication.get("decision")
            same_frame = (
                latest is not None
                and isinstance(decision, dict)
                and decision.get("frame_id") == latest.frame_id
            )
        if not same_frame or image is None:
            return publication, None
        try:
            return publication, self._frame_jpeg(latest)
        except Exception:  # noqa: BLE001 - a bad image must not replace the decision
            logger.exception("Decision transaction image encode failed")
            return publication, None

    def transaction_snapshot(self, kind: str) -> tuple[dict[str, Any], LatestObservationState | None, str]:
        """Snapshot publication, capture and content revision before JPEG work."""
        with self._lock:
            publication = (self._decision_publication_from_locked_state(read_at_ms=timestamp_ms())
                           if kind == "decision" else self._publication_from_locked_state(read_at_ms=timestamp_ms()))
            revision = f"{self.run_id}:{kind}:{self._publication_revision}:{self._queue_generation}"
            return publication, self.latest_state, revision

    def reset_memory(self) -> dict[str, Any]:
        """Reset the live memory step under the same lock as observation cycles.

        Clears retained memory on the step and detaches memory from the latest
        published observation so operators see an empty map immediately.
        """
        with self._lock:
            try:
                report = self.host.reset_memory()
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
                self._publication_revision += 1
            self._memory_update_halted = False
            step = self.host.step("memory")
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
        return self._jpeg(latest, publication)

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
        return self._jpeg(camera, publication)

    @staticmethod
    def _frame_jpeg(frame: LatestCameraFrame | LatestObservationState) -> bytes:
        return frame.jpeg_cache.get(frame.image)

    @classmethod
    def _jpeg(cls, frame: LatestCameraFrame | LatestObservationState | None,
              publication: dict[str, Any]) -> tuple[bytes | None, dict[str, Any]]:
        if frame is None or (frame.image is None and frame.jpeg_cache.data is None):
            return None, publication
        try:
            return cls._frame_jpeg(frame), publication
        except Exception as exc:
            return None, {
                **publication,
                "health": PUBLICATION_HEALTH_ERROR,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def wait_for_cycle(self, timeout_s: float = 5.0) -> None:
        """Block until the background decision cycle finishes, if one is running."""
        with self._lock:
            thread = self._decision_thread
        if thread is None:
            return
        thread.join(timeout_s)
        if thread.is_alive():
            raise TimeoutError("observation cycle did not finish")

    def completed_outputs(
        self, mode: str = "manual"
    ) -> tuple[float, float, dict[str, Any], Any, dict[str, Any] | None]:
        """Return the pilot tuple from the last finished cycle."""
        return self._held_outputs(mode)

    def _recording_jpeg(self, image: Any) -> bytes:
        # The owning worker's capture outlives its recording callback. The
        # per-capture cache serializes encoding without a publication lock.
        camera = self._inflight_camera
        if camera is not None and image is camera.image:
            return self._frame_jpeg(camera)
        return encode_jpeg(image)

    def start(self, configuration: RunConfiguration, *, record: bool = False) -> dict[str, Any]:
        if record and (self.recording_root is None or self.vehicle_id is None):
            raise RuntimeError("recording requires a runtime directory and vehicle identity")
        recording = RunRecording(
            self.recording_root, vehicle_id=self.vehicle_id, encode_image=self._recording_jpeg,
        ) if record else None
        with self._lock:
            self._session_frame_id = None
            self._memory_update_halted = False
            self._discard_pending_locked()
        status = self.host.start(configuration, recording=recording)
        return status

    def stop(self, *, reason: str = "stopped", error: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._discard_pending_locked()
        status = self.host.stop(reason=reason, error=error)
        return status

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            self._discard_pending_locked()
        self.host.close()

    def wait_until_ready(self, timeout_s: float = 0.1) -> bool:
        """Wait for pull-capture capacity; push adapters never wait here."""
        if not self._capture_ready.wait(timeout_s):
            return False
        with self._lock:
            return (
                not self._shutdown
                and not self._memory_update_halted
                and not self._decision_inflight
                and self._pending_frame is None
            )

    def submit(
        self,
        image: Any,
        *,
        mode: str | None = None,
        user_steering: float = 0.0,
        user_throttle: float = 0.0,
        captured_at_ms: int | None = None,
        frame_id: str | None = None,
        frame_index: int | None = None,
        metadata: dict[str, Any] | None = None,
        jpeg_bytes: bytes | None = None,
        image_loader: Callable[[], Any] | None = None,
    ) -> LatestCameraFrame | None:
        """Publish one camera sample and make it the newest pending frame.

        ``metadata`` carries vehicle frame identity (for example a simulator
        frame index and epoch) onto the sensor frame and the cycle context.
        """
        if mode is None:
            mode = self.host.execution.mode
        with self._lock:
            if self._shutdown:
                return None
            if frame_id is not None and frame_id == self._session_frame_id:
                # A paused simulator repeats its frame; there is nothing new to decide.
                return self.latest_camera_frame
            self._session_frame_id = frame_id
            camera = self._store_camera_frame_locked(
                image,
                timestamp_ms() if captured_at_ms is None else int(captured_at_ms),
                frame_id=frame_id,
                frame_index=frame_index,
                metadata=metadata, jpeg_bytes=jpeg_bytes,
            )
            camera.image_loader = image_loader
            if self._memory_update_halted:
                return camera
            if self._pending_frame is not None:
                self.frames_dropped += 1
            self._pending_frame = _PendingFrame(
                camera, mode, float(user_steering or 0.0), float(user_throttle or 0.0)
            )
            if not self._decision_inflight and not self._memory_update_halted and not self._shutdown:
                self._decision_inflight = True
                self._capture_ready.clear()
                self._inflight_frame_id = camera.frame_id
                thread = threading.Thread(
                    target=self._decision_worker, name="automa-decision-worker", daemon=True
                )
                self._decision_thread = thread
                thread.start()
            return camera

    def _camera_publication_from_locked_state(self, *, read_at_ms: int) -> dict[str, Any]:
        camera = self.latest_camera_frame
        threshold_ms = stale_after_ms(self._cycle_duration_ms)
        observation = self.latest_state
        perception_frame_id = None if observation is None else observation.frame_id
        if camera is None:
            health = PUBLICATION_HEALTH_WARMING if self.frames_received == 0 else PUBLICATION_HEALTH_ABSENT
            perception_state = "pending" if self._decision_inflight else "absent"
            return {
                "schema": CAMERA_PUBLICATION_SCHEMA,
                "ok": False,
                "health": health,
                "read_at_ms": read_at_ms,
                "result_age_ms": None,
                "stale_after_ms": threshold_ms,
                "frame": None,
                "frames_received": self.frames_received,
                "frames_dropped": self.frames_dropped,
                "perception_state": perception_state,
                "perception_frame_id": perception_frame_id,
                "perception_inflight": self._decision_inflight,
                "latest_camera_json_path": CAMERA_LATEST_JSON_PATH,
                "latest_camera_frame_path": CAMERA_LATEST_FRAME_PATH,
            }
        age_ms = max(0, read_at_ms - int(camera.captured_at_ms))
        if not camera.has_image:
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
                "has_image": camera.has_image,
                "frame_path": CAMERA_LATEST_FRAME_PATH,
            },
            "frames_received": self.frames_received,
            "frames_dropped": self.frames_dropped,
            "perception_state": perception_state,
            "perception_frame_id": perception_frame_id,
            "perception_inflight": self._decision_inflight,
            "latest_camera_json_path": CAMERA_LATEST_JSON_PATH,
            "latest_camera_frame_path": CAMERA_LATEST_FRAME_PATH,
        }

    def _store_camera_frame_locked(
        self,
        image: Any,
        captured_at_ms: int,
        *,
        frame_id: str | None = None,
        frame_index: int | None = None,
        metadata: dict[str, Any] | None = None,
        jpeg_bytes: bytes | None = None,
    ) -> LatestCameraFrame:
        sequence = self._capture_sequence
        frame = LatestCameraFrame(
            frame_id=frame_id or f"{self.frame_prefix}_frame_{sequence:06d}",
            frame_index=sequence if frame_index is None else int(frame_index),
            captured_at_ms=captured_at_ms,
            image=image,
            sequence=sequence,
            metadata=dict(metadata or {}),
            jpeg_cache=_FrameJpegCache(jpeg_bytes),
        )
        self.latest_camera_frame = frame
        self._capture_sequence += 1
        self.frames_received += 1
        return frame

    def _discard_pending_locked(self) -> None:
        self._pending_frame = None
        self._queue_generation += 1

    def _publication_from_locked_state(self, *, read_at_ms: int) -> dict[str, Any]:
        """Build a publication from the currently locked state and counters."""
        latest = self.latest_state
        processed_count = self.processed_count
        presets = dict(self.presets)
        generation_id = self.generation_id
        threshold_ms = stale_after_ms(self._cycle_duration_ms)

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
                "frames_received": self.frames_received,
                "frames_dropped": self.frames_dropped,
                "processed_count": processed_count,
                "presets": presets,
                "generation_id": generation_id,
                "frame": None,
                "control": None,
                "mode": None,
                "status": health,
                "error": None,
                "duration_ms": None,
                "timing": None,
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
        timing = None if latest.cycle is None else deepcopy(latest.cycle.get("timing"))
        # Republish the memory report through the cycle publication.
        memory = None if latest.cycle is None else deepcopy(latest.cycle.get("memory"))
        context = latest.context or (latest.cycle or {}).get("context") or {}
        selections = (context.get("metadata") or {}).get("step_activations")
        if isinstance(selections, dict) and all(step in selections for step in DECISION_STEPS):
            generation_id = activation_generation_id(
                {step: selections[step] for step in DECISION_STEPS}, prefix="decision",
            )
        else:
            generation_id = None
        return {
            "schema": OBSERVATION_PUBLICATION_SCHEMA,
            "ok": health in {PUBLICATION_HEALTH_HEALTHY, PUBLICATION_HEALTH_STALE},
            "health": health,
            "read_at_ms": read_at_ms,
            "result_age_ms": age_ms,
            "stale_after_ms": threshold_ms,
            "frames_received": self.frames_received,
            "frames_dropped": self.frames_dropped,
            "processed_count": processed_count,
            "presets": dict(latest.presets),
            "generation_id": generation_id,
            "step_activations": deepcopy(selections),
            "context": {
                key: context[key] for key in ("mode", "user_steering", "user_throttle")
                if key in context
            },
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
            "timing": timing,
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

    def _write_applied_decision(self, applied: dict[str, Any]) -> None:
        """Copy the host's applied identity into this loop's publication."""

        generation_id = applied.get("generation_id")
        steps = applied.get("steps")
        if not isinstance(generation_id, str) or not isinstance(steps, dict):
            return
        with self._lock:
            self.decision_activations = deepcopy(steps)
            self.generation_id = generation_id
            self._decision_identity_error = self._validate_decision_identity()
        self._identity_applied(generation_id)
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
        """Capture the shared ``vehicle_report_v0`` for this completed cycle.

        Runtime identity (source, activation, and the capture frame) goes in
        ``values``, with the display freshness limit. A failed cycle that
        still has its application is published; the failure stays in the
        cycle. A cycle that never produced an application stays unpublished.
        """

        if status != "ok":
            return None, "failed_step"
        if self._decision_identity_error is not None:
            return None, self._decision_identity_error
        result = self._current_decision_result()
        records = self._current_cycle
        if result is None or records is None:
            if self._current_decision_failed():
                return None, "failed_step"
            return None, "missing_result"
        result_frame_id = getattr(result, "frame_id", None)
        if result_frame_id != frame_id:
            return None, "mismatched_frame"
        proposal = records.proposal
        if proposal is None or records.application is None:
            return None, "incomplete_result"
        source = getattr(proposal, "source", None)
        if source is not None:
            try:
                source_export = source.to_dict()
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
                    "stale_after_ms": stale_after_ms(self._cycle_duration_ms),
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
        threshold_ms = stale_after_ms(self._cycle_duration_ms)
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
        """Build the decision wire payload, fail-closed at read time."""

        latest = self.latest_state
        if latest is None:
            return self._decision_unavailable(reason="missing", read_at_ms=read_at_ms)
        decision = latest.decision_publication
        if decision is None:
            return self._decision_unavailable(
                reason=latest.decision_error or "unavailable",
                read_at_ms=read_at_ms,
            )
        threshold_ms = diagnostic_ceiling(decision["values"])
        # Replacing a decision step retires its result. Never
        # replay the detached result retained by a prior sensor frame.
        current = self._current_decision_result()
        if current is None:
            reason = "failed_step" if self._current_decision_failed() else "reset"
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
                        self._inflight_camera = None
                        self._decision_thread = None
                        self._capture_ready.set()
                        return
                    self._pending_frame = None
                    camera = pending.camera
                    self._inflight_frame_id = camera.frame_id
                    self._inflight_camera = camera
                    generation = self._queue_generation
                    was_running = getattr(self.host, "run_state", None) == "running"
                try:
                    if camera.image_loader is not None:
                        camera.image = camera.image_loader()
                        camera.image_loader = None
                except Exception as exc:
                    with self._lifecycle_lock:
                        with self._lock:
                            current = generation == self._queue_generation
                        if current:
                            self.stop(reason="error", error=f"{type(exc).__name__}: {exc}")
                    continue
                with self._lock:
                    if generation != self._queue_generation:
                        continue
                succeeded = self._process_frame(
                    camera=camera,
                    mode_name=pending.mode_name,
                    user_steering=pending.user_steering,
                    user_throttle=pending.user_throttle,
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
                    self._inflight_camera = None
                    self._decision_thread = None
                    self._capture_ready.set()
            self.last_status = self.status()

    def _process_frame(
        self,
        *,
        camera: LatestCameraFrame,
        mode_name: str,
        user_steering: float,
        user_throttle: float,
    ) -> bool:
        frame_id, frame_index = camera.frame_id, camera.frame_index
        captured_at_ms = camera.captured_at_ms
        sensor_frame = SensorFrame(
            read_id=frame_id,
            readings={
                FRONT_CAMERA_SENSOR_ID: SensorReading(
                    sensor_id=FRONT_CAMERA_SENSOR_ID,
                    sensor_kind="camera",
                    captured_at_ms=captured_at_ms,
                    value=camera.image,
                    metadata={"source": self.camera_source},
                )
            },
            started_at_ms=captured_at_ms,
            completed_at_ms=captured_at_ms,
            metadata={**camera.metadata, "runtime": self.runtime},
        )

        try:
            try:
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
                            **camera.metadata,
                            "runtime": self.runtime,
                            "control_application": "shared_execution",
                            "capture_sequence": camera.sequence,
                        },
                    )
                )
                control = cycle_result.control
                cycle_dict = cycle_result.to_dict()
                applied = self.host.applied_decision()
                completed_at_ms = cycle_result.completed_at_ms
                duration_ms = cycle_result.duration_ms
                status = "ok"
                error = None
            except Exception as exc:
                logger.exception("Autonomy decision cycle failed for frame %s", frame_id)
                applied = None
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

            control_dict = (
                cycle_result.control_record() if cycle_dict is not None else {
                    **control.to_dict(), "applied": False,
                    "application": self.host.execution.status()["application"],
                }
            )
            # Readers must see one completed cycle and its matching report.
            # Plugin execution above stays outside the publication lock.
            with self._lock:
                if cycle_dict is not None:
                    self._cycle_duration_ms = float(duration_ms)
                self._record_current_cycle(cycle_result if cycle_dict is not None else None, failed=status != "ok")
                if isinstance(applied, dict) and applied.get("generation_id") != self.generation_id:
                    self._write_applied_decision(applied)
                decision_publication, decision_error = self._capture_decision_publication(
                    frame_id=frame_id,
                    frame_index=frame_index,
                    timestamp_ms_value=captured_at_ms,
                    published_at_ms=completed_at_ms,
                    status=status,
                )

                context = (
                    self.host.last_context.to_dict()
                    if self.host.last_context is not None
                    and self.host.last_context.frame_id == frame_id else None
                )
                applied_steps = ((context or {}).get("metadata") or {}).get("step_activations")
                if isinstance(applied_steps, dict):
                    self.presets = activation_presets(applied_steps)
                latest = LatestObservationState(
                    frame_id=frame_id,
                    frame_index=frame_index,
                    captured_at_ms=captured_at_ms,
                    completed_at_ms=completed_at_ms,
                    mode=mode_name,
                    status=status,
                    image=camera.image,
                    control=deepcopy(control_dict),
                    cycle=cycle_dict,
                    error=error,
                    duration_ms=duration_ms,
                    presets=dict(self.presets),
                    decision_publication=decision_publication,
                    decision_error=decision_error,
                    context=context,
                    jpeg_cache=camera.jpeg_cache,
                )
                self._last_control = control_dict
                self._last_cycle = cycle_dict
                self.latest_state = latest
                self._publication_revision += 1
                self.frame_index = frame_index + 1
                self.processed_count += 1
        finally:
            self._frame_completed()
        self.last_status = self.status()
        return status == "ok"

    def _held_outputs(self, mode_name: str):
        output = self.host.execution.output()
        with self._lock:
            control = deepcopy(self._last_control)
            generation_id = self.generation_id
            cycle = None if self._last_cycle is None else deepcopy(self._last_cycle)
        return (output.steering, output.throttle, control, generation_id, cycle)
