"""The latest-frame decision loop every vehicle runtime hosts.

PiCar ticks and Chase streams submit continuously: one pending slot holds the
newest sample while one worker runs decisions sequentially. Replacing a waiting
sample increments frames_dropped.
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from autonomy.decision_cycle.activation import (
    DECISION_STEPS,
    activation_generation_id,
    activation_presets,
)
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionCycleResult, cycle_result_of
from autonomy.decision_cycle.memory.errors import MemoryUpdateError
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.recording import RunRecording
from autonomy.runtime.report import delivery_values, error_application, report_from_host_result
from autonomy.runtime.session import RunConfiguration
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading

logger = logging.getLogger(__name__)

ONBOARD_OBSERVATION_STATE_SCHEMA = "automa_onboard_observation_state"
FRAME_PUBLICATION_SCHEMA = "host_frame_publication"
LATEST_FRAME_PATH = "/host/observation/latest/frame.jpg"
OBSERVATION_TRANSACTION_PATH = "/host/observation/transaction"
FRAME_TRANSACTION_SCHEMA = "host_frame_transaction"
LATEST_JSON_PATH = "/host/observation/latest"
CAMERA_LATEST_FRAME_PATH = "/host/camera/latest/frame.jpg"
CAMERA_LATEST_JSON_PATH = "/host/camera/latest"
CAMERA_PUBLICATION_SCHEMA = "automa_physical_camera_publication"

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
    """One completed frame: its report and the image that frame was computed from."""

    report: dict[str, Any]
    image: Any
    jpeg_cache: _FrameJpegCache = field(default_factory=_FrameJpegCache, repr=False, compare=False)

    @property
    def frame_id(self) -> str:
        return self.report["frame_id"]

    @property
    def frame_index(self) -> int:
        return self.report["frame_index"]

    @property
    def captured_at_ms(self) -> int:
        return self.report["timestamp_ms"]

    @property
    def completed_at_ms(self) -> int:
        return self.report["published_at_ms"]

    @property
    def status(self) -> str:
        return self.report["status"]

    @property
    def error(self) -> str | None:
        return self.report["error"]

    @property
    def mode(self) -> str | None:
        application = self.report.get("application")
        if isinstance(application, dict):
            mode = application.get("mode")
            return mode if isinstance(mode, str) else None
        return None

    def to_status_dict(self) -> dict[str, Any]:
        """Bounded status view without the raw image or the cycle body."""
        cycle = self.report.get("cycle") if isinstance(self.report.get("cycle"), dict) else {}
        perception = cycle.get("perception")
        application = self.report.get("application") if isinstance(self.report.get("application"), dict) else {}
        values = self.report.get("values") if isinstance(self.report.get("values"), dict) else {}
        timing = values.get("timing") if isinstance(values.get("timing"), dict) else {}
        activation = values.get("activation") if isinstance(values.get("activation"), dict) else {}
        steps = activation.get("steps") if isinstance(activation.get("steps"), dict) else {}
        return {
            "schema": ONBOARD_OBSERVATION_STATE_SCHEMA,
            "frame_id": self.frame_id,
            "frame_index": self.frame_index,
            "captured_at_ms": self.captured_at_ms,
            "completed_at_ms": self.completed_at_ms,
            "mode": self.mode,
            "status": self.status,
            "error": self.error,
            "control": deepcopy(application),
            "has_image": self.image is not None or self.jpeg_cache.data is not None,
            "duration_ms": timing.get("duration_ms", 0),
            "presets": activation_presets(steps),
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
    user_steering: float | None
    user_throttle: float | None


class FrameLoop:
    """Feed camera samples to the shared cycle host and publish the results.

    One completed cycle publishes one ``host_report``. ``host.run`` applies
    restaged selections and records the applied identity; this loop copies
    that identity into the report. The live camera sample stays separate.
    """

    def __init__(
        self,
        *,
        host: AutonomyCycleHost,
        presets: dict[str, str] | None = None,
        host_name: str | None = None,
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
        self.latest_camera_frame: LatestCameraFrame | None = None
        self._session_frame_id: str | None = None
        self._last_control = AutonomyControl(reason="observation-warming").to_dict()
        self.host_name = host_name
        self.source_id = source_id or (
            f"{runtime}:{host_name}" if isinstance(host_name, str) and host_name else None
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
                if self.latest_state is None
                else self.latest_state.report.get("schema")
            ),
        }

    def publish_latest(self, *, now_ms: int | None = None) -> dict[str, Any]:
        """Return one bounded latest frame/result publication computed at read time."""
        read_at_ms = timestamp_ms() if now_ms is None else int(now_ms)
        with self._lock:
            publication = self._publication_from_locked_state(read_at_ms=read_at_ms)
        return deepcopy(publication)

    def read_transaction(self) -> tuple[dict[str, Any], LatestObservationState | None, str]:
        """Borrow the frame publication and the state it was built from.

        Completed frame data is replaced, rather than updated in place. The
        route may retain these references across JPEG work without copying
        the report, including when an ETag avoids serialization entirely.
        Callers must only read the borrowed nested values.
        """
        with self._lock:
            publication = self._publication_from_locked_state(read_at_ms=timestamp_ms())
            revision = f"{self.run_id}:frame:{self._publication_revision}:{self._queue_generation}"
            return publication, self.latest_state, revision

    def publish_latest_frame_jpeg(self) -> tuple[bytes | None, dict[str, Any]]:
        """Return the exact processed frame JPEG with matching publication metadata."""
        read_at_ms = timestamp_ms()
        with self._lock:
            latest = self.latest_state
            publication = self._publication_from_locked_state(read_at_ms=read_at_ms)
        publication = deepcopy(publication)
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
    def frame_jpeg(frame: LatestCameraFrame | LatestObservationState) -> bytes:
        return frame.jpeg_cache.get(frame.image)

    @classmethod
    def _jpeg(cls, frame: LatestCameraFrame | LatestObservationState | None,
              publication: dict[str, Any]) -> tuple[bytes | None, dict[str, Any]]:
        if frame is None or (frame.image is None and frame.jpeg_cache.data is None):
            return None, publication
        try:
            return cls.frame_jpeg(frame), publication
        except Exception:
            # An encoding failure leaves the publication alone. The caller
            # reports the image error beside it.
            return None, publication

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
            return self.frame_jpeg(camera)
        return encode_jpeg(image)

    def start(self, configuration: RunConfiguration, *, record: bool = False) -> dict[str, Any]:
        with self._lifecycle_lock:
            if record and (self.recording_root is None or self.host_name is None):
                raise RuntimeError("recording requires a runtime directory and host identity")
            recording = RunRecording(
                self.recording_root, host_name=self.host_name, encode_image=self._recording_jpeg,
            ) if record else None
            with self._lock:
                self._session_frame_id = None
                self._memory_update_halted = False
                self._discard_pending_locked()
            status = self.host.start(configuration, recording=recording)
            return status

    def stop(self, *, reason: str = "stopped", error: str | None = None) -> dict[str, Any]:
        with self._lifecycle_lock:
            with self._lock:
                self._discard_pending_locked()
            status = self.host.stop(reason=reason, error=error)
            return status

    def stop_after_error(self, exc: Exception) -> None:
        """End the run ``exc`` broke; a loop thread outlives a stop that fails too."""
        try:
            self.stop(reason="error", error=f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001 - the host's last error records both
            logger.exception("Stopping the run after %s failed", type(exc).__name__)

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            self._discard_pending_locked()
        self.host.close()

    def submit(
        self,
        image: Any,
        *,
        mode: str | None = None,
        user_steering: float | None = None,
        user_throttle: float | None = None,
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
                camera, mode, user_steering, user_throttle,
            )
            if not self._decision_inflight and not self._memory_update_halted and not self._shutdown:
                self._decision_inflight = True
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
        """The frame envelope, borrowing the completed report."""
        latest = self.latest_state
        threshold_ms = stale_after_ms(self._cycle_duration_ms)
        if latest is None:
            health = (
                PUBLICATION_HEALTH_WARMING
                if self.processed_count == 0
                else PUBLICATION_HEALTH_ABSENT
            )
            return {
                "schema": FRAME_PUBLICATION_SCHEMA,
                "ok": False,
                "health": health,
                "read_at_ms": read_at_ms,
                "result_age_ms": None,
                "stale_after_ms": threshold_ms,
                "report": None,
            }
        report = latest.report
        published_at_ms = report.get("published_at_ms")
        age_ms = 0 if type(published_at_ms) is not int else max(0, read_at_ms - published_at_ms)
        values = report.get("values") if isinstance(report.get("values"), dict) else {}
        ceiling = values.get("stale_after_ms")
        if type(ceiling) is not int or ceiling <= 0:
            ceiling = threshold_ms
        health = PUBLICATION_HEALTH_STALE if age_ms > ceiling else PUBLICATION_HEALTH_HEALTHY
        return {
            "schema": FRAME_PUBLICATION_SCHEMA,
            "ok": True,
            "health": health,
            "read_at_ms": read_at_ms,
            "result_age_ms": age_ms,
            "stale_after_ms": ceiling,
            "report": report,
        }

    def _write_applied_decision(self, applied: dict[str, Any]) -> None:
        """Copy the host's applied identity into this loop's publication."""

        generation_id = applied.get("generation_id")
        steps = applied.get("steps")
        if not isinstance(generation_id, str) or not isinstance(steps, dict):
            return
        with self._lock:
            self.decision_activations = deepcopy(steps)
            self.generation_id = generation_id
        self._identity_applied(generation_id)
        logger.info("Decision generation: %s (restaged)", generation_id)

    def _capture_frame_report(
        self,
        result: DecisionCycleResult,
        *,
        camera: LatestCameraFrame,
        completed_at_ms: int,
        status: str,
        error: str | None,
        mode_name: str,
    ) -> dict[str, Any]:
        """The report for this camera frame. Another frame's outputs are not reused."""

        frame_id = camera.frame_id
        report_application = None
        if result.application is None:
            report_application = error_application(mode_name)
        generation_id = self.generation_id or "unassigned"
        activations = result.context.metadata.get("step_activations")
        steps = deepcopy(activations if isinstance(activations, dict) else self.decision_activations)
        steps = steps if isinstance(steps, dict) else {}
        values: dict[str, Any] = {
            "source_id": self.source_id or f"{self.runtime}:local",
            "activation": {"generation_id": generation_id, "steps": steps},
            "source_frame": {
                "frame_id": frame_id,
                "frame_index": camera.frame_index,
                "captured_at_ms": camera.captured_at_ms,
                "completed_at_ms": completed_at_ms,
            },
            "stale_after_ms": stale_after_ms(self._cycle_duration_ms),
        }
        if result.application is not None:
            values.update(delivery_values(result.application))
        return report_from_host_result(
            result,
            host_name=self.host_name or self.runtime,
            run_id=self.run_id,
            generation_id=generation_id,
            published_at_ms=completed_at_ms,
            values=values,
            status=status,
            error=error,
            report_application=report_application,
        ).to_dict()

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
                            self.stop_after_error(exc)
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
            self.last_status = self.status()

    def _process_frame(
        self,
        *,
        camera: LatestCameraFrame,
        mode_name: str,
        user_steering: float | None,
        user_throttle: float | None,
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

        status = "error"
        try:
            try:
                cycle_result = self.host.run(
                    DecisionFrameContext(
                        frame_id=frame_id,
                        frame_index=frame_index,
                        timestamp_ms=captured_at_ms,
                        sensor_frame=sensor_frame,
                        metadata={
                            **camera.metadata,
                            "runtime": self.runtime,
                            "control_application": "shared_execution",
                            "capture_sequence": camera.sequence,
                        },
                    ),
                    mode=mode_name,
                    user_steering=user_steering,
                    user_throttle=user_throttle,
                )
                completed_at_ms = int(cycle_result.completed_at_ms)
                duration_ms = cycle_result.duration_ms
                status = "ok"
                error = None
            except Exception as exc:
                logger.exception("Autonomy decision cycle failed for frame %s", frame_id)
                cycle_result = cycle_result_of(exc)
                if isinstance(exc, MemoryUpdateError):
                    with self._lock:
                        self._memory_update_halted = True
                completed_at_ms = int(cycle_result.completed_at_ms) if cycle_result is not None else timestamp_ms()
                duration_ms = cycle_result.duration_ms if cycle_result is not None else max(0, completed_at_ms - captured_at_ms)
                status = "error"
                error = f"{type(exc).__name__}: {exc}"
            if cycle_result is None or getattr(cycle_result.context, "frame_id", None) != frame_id:
                cycle_result = DecisionCycleResult(
                    context=DecisionFrameContext(
                        frame_id=frame_id,
                        frame_index=frame_index,
                        timestamp_ms=captured_at_ms,
                        sensor_frame=sensor_frame,
                    ),
                    perception=None,
                    observation=None,
                    memory=None,
                    proposal=None,
                    plan=None,
                    action=None,
                    control=AutonomyControl(reason="observation-cycle-error"),
                    started_at_ms=captured_at_ms,
                    completed_at_ms=completed_at_ms,
                )
            applied_decision = getattr(self.host, "applied_decision", None)
            applied = applied_decision() if callable(applied_decision) else None
            if status == "ok":
                control_dict = cycle_result.control_record()
            else:
                execution = getattr(self.host, "execution", None)
                application_status = None
                if execution is not None:
                    application_status = execution.status()["application"]
                control_dict = {
                    **AutonomyControl(reason="observation-cycle-error").to_dict(),
                    "applied": False,
                    "application": application_status,
                }
            # The cycle ran outside this lock. Publishing borrows nothing from
            # the previous frame: cycle_result is this attempt or an empty one.
            with self._lock:
                self._cycle_duration_ms = float(duration_ms)
                if isinstance(applied, dict) and applied.get("generation_id") != self.generation_id:
                    self._write_applied_decision(applied)
                metadata = cycle_result.context.metadata or {}
                applied_steps = metadata.get("step_activations")
                if isinstance(applied_steps, dict):
                    self.presets = activation_presets(applied_steps)
                    # A loop started without an identity still publishes the
                    # decision steps this frame applied.
                    if not self.decision_activations:
                        decision_identity = {
                            step: applied_steps.get(step) for step in DECISION_STEPS
                        }
                        try:
                            self.generation_id = activation_generation_id(
                                decision_identity, prefix="decision",
                            )
                        except (TypeError, ValueError):
                            pass
                        else:
                            self.decision_activations = deepcopy(decision_identity)
                try:
                    report = self._capture_frame_report(
                        cycle_result,
                        camera=camera,
                        completed_at_ms=completed_at_ms,
                        status=status,
                        error=error,
                        mode_name=mode_name,
                    )
                except Exception:
                    logger.exception("Frame report failed for %s", frame_id)
                    report = None
                if report is not None:
                    self._last_control = control_dict
                    self.latest_state = LatestObservationState(
                        report=report,
                        image=camera.image,
                        jpeg_cache=camera.jpeg_cache,
                    )
                    self._publication_revision += 1
                    self.frame_index = frame_index + 1
                    self.processed_count += 1
                else:
                    status = "error"
        finally:
            self._frame_completed()
        self.last_status = self.status()
        return status == "ok"

    def _held_outputs(self, mode_name: str):
        output = self.host.execution.output()
        with self._lock:
            control = deepcopy(self._last_control)
            generation_id = self.generation_id
            cycle = None if self.latest_state is None else deepcopy(self.latest_state.report)
        return (output.steering, output.throttle, control, generation_id, cycle)
