"""The latest-frame decision loop every vehicle runtime hosts.

PiCar ticks and Chase streams submit continuously: one pending slot holds the
newest sample while one worker runs decisions sequentially. Replacing a waiting
sample increments frames_dropped.
The cycle host owns movement authority, freshness, stopping, recording, and
the applied decision identity; this loop owns pending frames and publications.
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
from typing import Callable

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionCycleResult
from autonomy.decision_cycle.memory.errors import MemoryUpdateError
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.recording import RunRecording
from autonomy.runtime.report import delivery_values, report_from_host_result
from autonomy.runtime.session import RunConfiguration
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading

logger = logging.getLogger(__name__)

FRAME_PUBLICATION_SCHEMA = "host_frame_publication"
OBSERVATION_TRANSACTION_PATH = "/host/observation/transaction"
FRAME_TRANSACTION_SCHEMA = "host_frame_transaction"
LATEST_JSON_PATH = "/host/observation/latest"

PUBLICATION_HEALTH_WARMING = "warming"
PUBLICATION_HEALTH_HEALTHY = "healthy"
PUBLICATION_HEALTH_STALE = "stale"


def timestamp_ms() -> int:
    return int(time.time() * 1000)


def encode_jpeg(image_array: object) -> bytes:
    """Encode a grayscale or RGB array as JPEG bytes without writing to disk."""
    from PIL import Image
    import numpy as np

    if image_array is None:
        raise ValueError("image_array is required")
    array = np.asarray(image_array)
    if array.ndim == 2:
        pixels = array
    elif array.ndim == 3 and array.shape[2] >= 3:
        pixels = array[:, :, :3]
    else:
        raise ValueError(f"unsupported image shape {array.shape}")
    image = Image.fromarray(pixels.astype("uint8"))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def stale_after_ms(cycle_duration_ms: float | None) -> int:
    """Age beyond which a still-present result is marked stale at read time."""
    if cycle_duration_ms is None:
        return 1000
    return max(1000, int(round(float(cycle_duration_ms) * 2.0)))


@dataclass
class _FrameJpegCache:
    data: bytes | None = field(default=None, repr=False)
    lock: object = field(default_factory=threading.Lock, repr=False, compare=False)

    def get(self, image: object) -> bytes:
        with self.lock:
            if self.data is None:
                self.data = encode_jpeg(image)
            return self.data


@dataclass
class LatestObservationState:
    """One completed frame: its report and the image that frame was computed from."""

    report: dict[str, object]
    image: object
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


@dataclass
class LatestCameraFrame:
    """One camera sample, kept as it arrives and before any decision reads it.

    ``sequence`` counts captures in this loop; ``frame_index`` is the
    vehicle's frame identity, which may skip (a simulator frame index).
    """

    frame_id: str
    frame_index: int
    captured_at_ms: int
    image: object
    sequence: int = 0
    metadata: dict[str, object] = field(default_factory=dict)
    jpeg_cache: _FrameJpegCache = field(default_factory=_FrameJpegCache, repr=False, compare=False)
    image_loader: Callable[[], object] | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class _PendingFrame:
    camera: LatestCameraFrame
    mode_name: str
    user_steering: float | None
    user_throttle: float | None


class FrameLoop:
    """Feed camera samples to the shared cycle host and publish the results.

    One completed cycle publishes one ``host_report``. ``host.run`` applies
    restaged selections and stamps the identity it applied on the cycle's
    context; the report names that identity. The live camera sample stays
    separate.
    """

    def __init__(
        self,
        *,
        host: AutonomyCycleHost,
        host_name: str | None = None,
        source_id: str | None = None,
        run_id: str | None = None,
        recording_root: Path | None = None,
        runtime: str = "frame_loop",
        camera_source: str | None = None,
        frame_prefix: str | None = None,
    ) -> None:
        if host.execution is None:
            raise ValueError(f"{type(self).__name__} needs a host with a control target")
        self.host = host
        self.runtime = runtime
        self.camera_source = camera_source or f"{runtime}_camera"
        self.frame_prefix = frame_prefix or runtime
        self.recording_root = recording_root
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self.frames_received = 0
        self.frames_dropped = 0
        self.processed_count = 0
        self._pending_frame: _PendingFrame | None = None
        self._queue_generation = 0
        self._shutdown = False
        self._memory_update_halted = False
        self._inflight_camera: LatestCameraFrame | None = None
        self._decision_thread: threading.Thread | None = None
        self.latest_state: LatestObservationState | None = None
        self._publication_revision = 0
        self._session_frame_id: str | None = None
        self.host_name = host_name
        self.source_id = source_id or (f"{runtime}:{host_name}" if host_name else None)
        self.run_id = run_id or f"{runtime}-run-{secrets.token_hex(12)}"
        host.register_status_provider("observation", self.observation_status)

    # Adapter hook. A vehicle adapter overrides it to mirror the shared
    # session in its own vocabulary; the loop never depends on it.

    def _frame_completed(self) -> None:
        pass

    def observation_status(self) -> dict[str, object]:
        """Bounded observation counters for the cycle host's status providers.

        Must not call back into ``AutonomyCycleHost.status``; that re-enters
        registered providers and hangs the status route.
        """
        with self._lock:
            latest = self.latest_state
            return {
                "processed_count": self.processed_count,
                "frames_received": self.frames_received,
                "frames_dropped": self.frames_dropped,
                "latest": None if latest is None else {
                    "frame_id": latest.frame_id,
                    "frame_index": latest.frame_index,
                    "captured_at_ms": latest.captured_at_ms,
                    "completed_at_ms": latest.completed_at_ms,
                    "duration_ms": latest.report["values"].get("timing", {}).get("duration_ms", 0),
                },
            }

    def publish_latest(self, *, now_ms: int | None = None) -> dict[str, object]:
        """Return one bounded latest frame/result publication computed at read time."""
        read_at_ms = timestamp_ms() if now_ms is None else int(now_ms)
        with self._lock:
            publication = self._publication_from_locked_state(read_at_ms=read_at_ms)
        return deepcopy(publication)

    def read_transaction(self) -> tuple[dict[str, object], LatestObservationState | None, str]:
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

    @staticmethod
    def frame_jpeg(frame: LatestCameraFrame | LatestObservationState) -> bytes:
        return frame.jpeg_cache.get(frame.image)

    def wait_for_cycle(self, timeout_s: float = 5.0) -> None:
        """Block until the background decision cycle finishes, if one is running."""
        with self._lock:
            thread = self._decision_thread
        if thread is None:
            return
        thread.join(timeout_s)
        if thread.is_alive():
            raise TimeoutError("observation cycle did not finish")

    def _recording_jpeg(self, image: object) -> bytes:
        # The owning worker's capture outlives its recording callback. The
        # per-capture cache serializes encoding without a publication lock.
        camera = self._inflight_camera
        if camera is not None and image is camera.image:
            return self.frame_jpeg(camera)
        return encode_jpeg(image)

    def start(self, configuration: RunConfiguration, *, record: bool = False) -> dict[str, object]:
        with self._lifecycle_lock:
            recording = None
            if record:
                if self.recording_root is None or self.host_name is None:
                    raise RuntimeError("recording requires a runtime directory and host identity")
                recording = RunRecording(
                    self.recording_root, host_name=self.host_name, encode_image=self._recording_jpeg,
                )
            with self._lock:
                self._session_frame_id = None
                self._memory_update_halted = False
                self._discard_pending_locked()
            return self.host.start(configuration, recording=recording)

    def stop(self, *, reason: str = "stopped", error: str | None = None) -> dict[str, object]:
        with self._lifecycle_lock:
            with self._lock:
                self._discard_pending_locked()
            return self.host.stop(reason=reason, error=error)

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
        image: object,
        *,
        mode: str | None = None,
        user_steering: float | None = None,
        user_throttle: float | None = None,
        captured_at_ms: int | None = None,
        frame_id: str | None = None,
        frame_index: int | None = None,
        metadata: dict[str, object] | None = None,
        jpeg_bytes: bytes | None = None,
        image_loader: Callable[[], object] | None = None,
    ) -> None:
        """Make one camera sample the newest pending frame.

        ``metadata`` carries vehicle frame identity (for example a simulator
        frame index and epoch) onto the sensor frame and the cycle context.
        """
        if mode is None:
            mode = self.host.execution.mode
        with self._lock:
            if self._shutdown:
                return
            if frame_id is not None and frame_id == self._session_frame_id:
                # A paused simulator repeats its frame; there is nothing new to decide.
                return
            self._session_frame_id = frame_id
            sequence = self.frames_received
            camera = LatestCameraFrame(
                frame_id=frame_id or f"{self.frame_prefix}_frame_{sequence:06d}",
                frame_index=sequence if frame_index is None else int(frame_index),
                captured_at_ms=timestamp_ms() if captured_at_ms is None else int(captured_at_ms),
                image=image,
                sequence=sequence,
                metadata=dict(metadata or {}),
                jpeg_cache=_FrameJpegCache(jpeg_bytes),
                image_loader=image_loader,
            )
            self.frames_received += 1
            if self._memory_update_halted:
                return
            if self._pending_frame is not None:
                self.frames_dropped += 1
            self._pending_frame = _PendingFrame(
                camera, mode, user_steering, user_throttle,
            )
            if self._decision_thread is None:
                thread = threading.Thread(
                    target=self._decision_worker, name="automa-decision-worker", daemon=True
                )
                # The worker waits for this lock, so it sees itself recorded;
                # a start that fails records nothing and the next capture retries.
                thread.start()
                self._decision_thread = thread

    def _discard_pending_locked(self) -> None:
        self._pending_frame = None
        self._queue_generation += 1

    def _publication_from_locked_state(self, *, read_at_ms: int) -> dict[str, object]:
        """The frame envelope, borrowing the completed report."""
        latest = self.latest_state
        report = None
        age_ms = None
        ceiling_ms = stale_after_ms(None)
        health = PUBLICATION_HEALTH_WARMING
        if latest is not None:
            report = latest.report
            age_ms = max(0, read_at_ms - latest.completed_at_ms)
            ceiling_ms = report["values"]["stale_after_ms"]
            health = PUBLICATION_HEALTH_STALE if age_ms > ceiling_ms else PUBLICATION_HEALTH_HEALTHY
        return {
            "schema": FRAME_PUBLICATION_SCHEMA,
            "ok": latest is not None,
            "health": health,
            "read_at_ms": read_at_ms,
            "result_age_ms": age_ms,
            "stale_after_ms": ceiling_ms,
            "report": report,
        }

    def _capture_frame_report(self, result: DecisionCycleResult, *, error: str | None) -> dict[str, object]:
        """The report for this camera frame, naming the identity the host stamped on it."""
        metadata = result.context.metadata
        generation_id = metadata["generation_id"]
        values: dict[str, object] = {
            "source_id": self.source_id or f"{self.runtime}:local",
            "activation": {"steps": metadata["step_activations"]},
            "stale_after_ms": stale_after_ms(result.duration_ms),
            **delivery_values(result.application),
        }
        return report_from_host_result(
            result,
            host_name=self.host_name or self.runtime,
            run_id=self.run_id,
            generation_id=generation_id,
            published_at_ms=int(result.completed_at_ms),
            values=values,
            status="ok" if error is None else "error",
            error=error,
        ).to_dict()

    def _frame_report(self, result: DecisionCycleResult, *, error: str | None) -> dict[str, object] | None:
        """This frame's report; step data that is not JSON publishes it with null steps."""
        try:
            return self._capture_frame_report(result, error=error)
        except Exception as exc:
            logger.exception("Frame report failed for %s", result.context.frame_id)
            stepless = replace(
                result, perception=None, observation=None, memory=None, proposal=None, plan=None, action=None,
            )
            try:
                return self._capture_frame_report(
                    stepless, error=f"frame report failed: {type(exc).__name__}: {exc}",
                )
            except Exception:
                logger.exception("Error report failed for %s", result.context.frame_id)
                return None

    def _decision_worker(self) -> None:
        try:
            while True:
                with self._lock:
                    pending = self._pending_frame
                    if pending is None or self._memory_update_halted or self._shutdown:
                        # Release ownership atomically with checking the slot,
                        # so a concurrent capture can start another worker.
                        self._release_worker_locked()
                        return
                    self._pending_frame = None
                    camera = pending.camera
                    self._inflight_camera = camera
                    generation = self._queue_generation
                    was_running = self.host.run_state == "running"
                try:
                    if camera.image_loader is not None:
                        camera.image = camera.image_loader()
                        camera.image_loader = None
                except Exception as exc:
                    with self._lifecycle_lock:
                        if self._is_current(generation):
                            self.stop_after_error(exc)
                    continue
                if not self._is_current(generation):
                    continue
                succeeded = self._process_frame(
                    camera=camera,
                    mode_name=pending.mode_name,
                    user_steering=pending.user_steering,
                    user_throttle=pending.user_throttle,
                )
                with self._lock:
                    ended = was_running and self.host.run_state != "running"
                    if self._is_current(generation) and (not succeeded or ended):
                        self._discard_pending_locked()
        finally:
            with self._lock:
                self._release_worker_locked()

    def _is_current(self, generation: int) -> bool:
        """No start, stop, or shutdown has discarded the queue since ``generation``."""
        with self._lock:
            return generation == self._queue_generation

    def _release_worker_locked(self) -> None:
        if self._decision_thread is threading.current_thread():
            self._inflight_camera = None
            self._decision_thread = None

    def _frame_context(self, camera: LatestCameraFrame) -> DecisionFrameContext:
        captured_at_ms = camera.captured_at_ms
        sensor_frame = SensorFrame(
            read_id=camera.frame_id,
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
        return DecisionFrameContext(
            frame_id=camera.frame_id,
            frame_index=camera.frame_index,
            timestamp_ms=captured_at_ms,
            sensor_frame=sensor_frame,
            metadata={
                **camera.metadata,
                "runtime": self.runtime,
                "control_application": "shared_execution",
                "capture_sequence": camera.sequence,
            },
        )

    def _process_frame(
        self,
        *,
        camera: LatestCameraFrame,
        mode_name: str,
        user_steering: float | None,
        user_throttle: float | None,
    ) -> bool:
        error = None
        try:
            try:
                result = self.host.run(
                    self._frame_context(camera),
                    mode=mode_name,
                    user_steering=user_steering,
                    user_throttle=user_throttle,
                )
            except Exception as exc:
                logger.exception("Autonomy decision cycle failed for frame %s", camera.frame_id)
                # The host attaches the result of every cycle it ran, raised or not.
                result = exc.cycle_result
                error = f"{type(exc).__name__}: {exc}"
                if isinstance(exc, MemoryUpdateError):
                    with self._lock:
                        self._memory_update_halted = True
            # Built outside the lock: drive ticks and readers wait only for the swap.
            report = self._frame_report(result, error=error)
            if report is None:
                return False
            with self._lock:
                self.latest_state = LatestObservationState(
                    report=report,
                    image=camera.image,
                    jpeg_cache=camera.jpeg_cache,
                )
                self._publication_revision += 1
                self.processed_count += 1
            return report["status"] == "ok"
        finally:
            self._frame_completed()
