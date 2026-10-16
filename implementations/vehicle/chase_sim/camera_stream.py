"""A passive camera subscription with one waiting frame, decoded in memory."""
from __future__ import annotations

import threading
import time
from typing import Any

from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from .car import (
    ChasePassiveCaptureError,
    _compare_passive_receipt_fingerprints,
    _normalize_passive_receipt_fingerprint,
)
from .frame_identity import ChaseCaptureValidationError, validate_chase_sensor_capture
from .metrics_ws import (
    MetricsUiWebSocketError,
    MetricsUiWsClient,
    _WebSocketConnection,
    build_chase_session_fingerprint,
    compare_chase_session_fingerprints,
)

STABLE_SESSION_FIELDS = ("game_id", "scenario_id", "simulation_epoch", "control_source")


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class ChaseCameraStream:
    """Drain the wire continuously; retain only the newest undecoded frame.

    The subscription proves passive preservation at startup. Each frame carries
    its own image/epoch/index; a cheap state read checks scenario and authority
    before the frame enters perception. Neither operation changes vehicle input.
    """

    def __init__(self, client: MetricsUiWsClient) -> None:
        self.client = client
        self._ws = _WebSocketConnection(client.url, timeout_s=client.timeout_s)
        self._condition = threading.Condition()
        self._closed = threading.Event()
        self._latest: tuple[dict[str, Any], int] | None = None
        self._error: Exception | None = None
        self._reader: threading.Thread | None = None
        self._subscription_id: str | None = None
        self._baseline: dict[str, Any] = {}
        self._session_baseline: dict[str, Any] = {}
        self._state_request_number = 0
        self._state_request_id: str | None = None
        self._state_reply: dict[str, Any] | None = None
        self._last_index = -1
        self._last_source_us = -1
        self._first_source_us: int | None = None
        self._subscribe_started_ms = 0.0
        self._last_progress_ms = 0.0
        self._last_idle_check_ms = 0.0
        self.frames_received = 0
        self.frames_replaced = 0

    def __enter__(self) -> ChaseCameraStream:
        try:
            if self._closed.is_set():
                raise MetricsUiWebSocketError("Camera stream is closed")
            self._ws.connect()
            deadline = time.monotonic() + self.client.timeout_s
            self._ws.send_json({"type": "register", "role": "agent"})
            self.client._wait_for_registration(self._ws, deadline)
            self._subscribe_started_ms = time.monotonic() * 1000
            self._ws.send_json({
                "type": "play_camera_stream_subscribe",
                "request_id": "automa-camera-subscribe",
                "actorId": "chaser", "cameraId": FRONT_CAMERA_SENSOR_ID,
                "width": 640, "height": 480, "imageFormat": "image/jpeg",
                "quality": 0.8, "maxRateHz": 30, "dropPolicy": "latest-frame",
            })
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MetricsUiWebSocketError("Camera stream subscription timed out")
                message = self._ws.recv_json(timeout_s=remaining)
                self._raise_protocol_error(message)
                if message.get("type") != "play_camera_stream_result":
                    continue
                payload = message.get("payload")
                if not isinstance(payload, dict) or payload.get("event") != "subscribed":
                    raise MetricsUiWebSocketError("Camera stream subscription was rejected", details=payload)
                self._validate_subscription(payload)
                self._accept_frame(payload.get("frame"))
                break
            # A paused simulator may produce no new frames. Closing the socket
            # interrupts this blocking reader; consumer waits remain bounded.
            self._ws._require_sock().settimeout(None)
            self._reader = threading.Thread(target=self._receive, name="chase-camera-stream", daemon=True)
            self._reader.start()
            self._session_baseline = build_chase_session_fingerprint(
                state=self._request_state(), debug={"gameId": "chase",
                                                    "simulationEpoch": self._baseline["simulation_epoch"]},
            )
            missing = [name for name in ("scenario_id", "control_source")
                       if self._session_baseline.get(name) is None]
            if missing:
                raise ChasePassiveCaptureError(
                    code="simulator_capability_missing",
                    message="Camera stream requires sidebar rows scenario-select and chaser-control-source.",
                    details={"unknown_fields": missing, "mutation_attempted": False},
                )
            return self
        except Exception:
            self.close()
            raise

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    @staticmethod
    def _raise_protocol_error(message: dict[str, Any]) -> None:
        if message.get("type") == "error":
            payload = message.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            raise MetricsUiWebSocketError(
                str(message.get("error") or "Camera stream failed"),
                code=payload.get("code"), details=payload,
            )

    def _validate_subscription(self, payload: dict[str, Any]) -> None:
        subscription_id = payload.get("subscriptionId")
        preservation = _record(payload.get("preservation"))
        before = _normalize_passive_receipt_fingerprint(preservation.get("before"))
        after = _normalize_passive_receipt_fingerprint(preservation.get("after"))
        receipt = _compare_passive_receipt_fingerprints(before, after)
        if (not isinstance(subscription_id, str) or not subscription_id
                or _record(payload.get("cameraStream")).get("supported") is not True
                or _record(payload.get("playback")).get("advanced") is not False
                or preservation.get("preserved") is not True or not receipt["preserved"]
                or before.get("game_id") != "chase" or before.get("actor_id") != "chaser"
                or before.get("camera_id") != FRONT_CAMERA_SENSOR_ID):
            raise ChasePassiveCaptureError(
                code="simulator_capability_missing",
                message="Camera stream did not prove passive session preservation.",
                details={"session_preservation": receipt, "mutation_attempted": False},
            )
        self._subscription_id = subscription_id
        self._baseline = before
        frame = _record(payload.get("frame"))
        identity = _record(frame.get("frameIdentity"))
        if identity.get("frameIndex") != before["playback"]["frameIndex"]:
            raise self._invalid("frameIdentity.frameIndex", "subscription image and receipt disagree")

    @staticmethod
    def _invalid(path: str, message: str) -> ChaseCaptureValidationError:
        return ChaseCaptureValidationError(code="capture_identity_invalid", path=path, message=message)

    def _accept_frame(self, frame: Any) -> None:
        if not isinstance(frame, dict):
            raise self._invalid("frame", "expected a camera stream frame")
        identity = frame.get("frameIdentity")
        identity = identity if isinstance(identity, dict) else {}
        index = identity.get("frameIndex")
        if (frame.get("subscriptionId") != self._subscription_id
                or frame.get("actorId") != "chaser" or frame.get("cameraId") != FRONT_CAMERA_SENSOR_ID
                or identity.get("gameId") != "chase"
                or identity.get("simulationEpoch") != self._baseline["simulation_epoch"]
                or type(index) is not int or index <= self._last_index
                or _record(frame.get("playback")).get("advanced") is not False):
            raise self._invalid("frameIdentity", "stream identity changed or frame index did not advance")
        source_us, published_us = frame.get("sourceTimestampUs"), frame.get("publishedAtUs")
        if (type(source_us) is not int or type(published_us) is not int
                or source_us < 0 or source_us < self._last_source_us or published_us < source_us):
            raise self._invalid("sourceTimestampUs", "invalid or regressed stream timestamps")
        self._last_index, self._last_source_us = index, source_us
        if self._first_source_us is None:
            self._first_source_us = source_us
        # The first image was captured after our subscribe request. Anchor its
        # source clock to that earliest possible local time, conservatively
        # preserving subscription latency and later wire/slot backlog as age.
        local_capture_ms = self._subscribe_started_ms + (source_us - self._first_source_us) / 1000
        age_ms = time.monotonic() * 1000 - local_capture_ms
        if age_ms < -250:
            raise self._invalid("sourceTimestampUs", "stream source clock jumped ahead")
        captured_at_ms = int(time.time() * 1000 - max(0.0, age_ms))
        self._last_progress_ms = time.monotonic() * 1000
        with self._condition:
            self.frames_received += 1
            self.frames_replaced += int(self._latest is not None)
            self._latest = frame, captured_at_ms
            self._condition.notify_all()

    def _receive(self) -> None:
        try:
            while not self._closed.is_set():
                message = self._ws.recv_json()
                self._raise_protocol_error(message)
                if message.get("type") == "play_camera_stream_frame":
                    self._accept_frame(message.get("payload"))
                elif message.get("type") == "state_update":
                    with self._condition:
                        if (self._state_request_id is not None
                                and message.get("request_id") == self._state_request_id):
                            if not isinstance(message.get("payload"), dict):
                                raise MetricsUiWebSocketError("Camera state reply must contain an object")
                            self._state_reply = message["payload"]
                            self._condition.notify_all()
                elif message.get("type") == "play_camera_stream_result":
                    payload = _record(message.get("payload"))
                    if payload.get("event") == "ended":
                        raise MetricsUiWebSocketError("Camera stream ended", details=payload)
        except Exception as exc:
            if not self._closed.is_set():
                with self._condition:
                    self._error = exc
                    self._latest = None
                    self._condition.notify_all()

    def _request_state(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.client.timeout_s
        with self._condition:
            if self._closed.is_set():
                raise MetricsUiWebSocketError("Camera stream is closed")
            if self._error is not None:
                raise self._error
            self._state_request_number += 1
            request_id = f"automa-camera-state-{self._state_request_number}"
            self._state_request_id, self._state_reply = request_id, None
        try:
            self._ws.send_json({"type": "get_state", "request_id": request_id}, deadline=deadline)
            with self._condition:
                self._condition.wait_for(
                    lambda: self._state_reply is not None or self._error is not None or self._closed.is_set(),
                    timeout=max(0.0, deadline - time.monotonic()),
                )
                if self._error is not None:
                    raise self._error
                if self._closed.is_set():
                    raise MetricsUiWebSocketError("Camera stream closed during state request")
                if self._state_reply is None or time.monotonic() >= deadline:
                    raise MetricsUiWebSocketError("Camera stream state request timed out")
                return self._state_reply
        except Exception:
            self._ws.abort()
            raise
        finally:
            with self._condition:
                self._state_request_id, self._state_reply = None, None

    def read_camera_frame(self, *, timeout_s: float = 0.1) -> SensorFrame | None:
        with self._condition:
            self._condition.wait_for(
                lambda: self._latest is not None or self._error is not None or self._closed.is_set(),
                timeout=timeout_s,
            )
            if self._error is not None:
                raise self._error
            if self._closed.is_set():
                return None
            sample = self._latest
            self._latest = None
        if sample is None:
            self._check_idle()
            return None
        frame, captured_at_ms = sample
        identity = frame["frameIdentity"]
        # The stream's per-frame wire format shares the atomic sensor envelope.
        sensor = validate_chase_sensor_capture({
            "contractVersion": 1,
            "captureId": f"{self._subscription_id}:{identity['frameIndex']}",
            "actorId": frame["actorId"], "frameIdentity": identity,
            "playback": frame["playback"], "sensor": frame.get("sensor"),
        }, include_image_array=True)
        if sensor["image"]["content_type"] != "image/jpeg":
            raise self._invalid("sensor.image.contentType", "camera stream must contain JPEG images")
        self._check_session(self._request_state(), sensor)
        return SensorFrame(
            read_id=sensor["frame_id"],
            readings={FRONT_CAMERA_SENSOR_ID: SensorReading(
                sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera",
                captured_at_ms=captured_at_ms, value=sensor["image"]["image_array"],
            )},
            started_at_ms=captured_at_ms, completed_at_ms=int(time.time() * 1000),
            metadata={
                "simulator_frame_index": sensor["simulator_frame_index"],
                "simulation_epoch": sensor["simulation_epoch"], "camera_transport": "stream",
                "source_timestamp_us": frame["sourceTimestampUs"],
                "published_at_us": frame["publishedAtUs"],
                "frames_received": self.frames_received, "frames_replaced": self.frames_replaced,
            },
        )

    def _check_session(self, state: dict[str, Any], sensor: dict[str, Any]) -> None:
        current = build_chase_session_fingerprint(state=state, debug={
            "gameId": sensor["game_id"],
            "simulationEpoch": sensor["simulation_epoch"],
        })
        receipt = compare_chase_session_fingerprints(
            self._session_baseline, current, field_names=STABLE_SESSION_FIELDS,
        )
        if not receipt["preserved"]:
            raise ChasePassiveCaptureError(
                code="simulator_state_changed" if receipt["changed_fields"] else "simulator_capability_missing",
                message="Simulator identity or control authority changed during the camera stream.",
                details={"session_preservation": receipt, "mutation_attempted": False},
            )

    def _check_idle(self) -> None:
        now_ms = time.monotonic() * 1000
        if now_ms - max(self._last_progress_ms, self._last_idle_check_ms) < self.client.timeout_s * 1000:
            return
        self._last_idle_check_ms = now_ms
        state = self._request_state()
        self._check_session(state, self._baseline)
        if _record(state.get("playback")).get("isPlaying") is False:
            return
        # Only a stalled stream needs this expensive diagnostic. Game-level
        # pause-before-actions can hold its frame while the app remains playing.
        debug = self.client.get_play_debug()
        if debug.get("phase") == "paused-before-actions" or debug.get("actionApplicationPending") is True:
            return
        if self._last_progress_ms > now_ms:
            return
        raise MetricsUiWebSocketError("Camera stream stopped producing frames while playing",
                                     code="frontend_unresponsive")

    def close(self) -> None:
        self._closed.set()
        with self._condition:
            self._latest = None
            request_pending = self._state_request_id is not None
            self._condition.notify_all()
        try:
            if request_pending:
                self._ws.abort()
            elif self._ws.sock is not None and self._subscription_id is not None:
                self._ws.send_json({"type": "play_camera_stream_unsubscribe",
                                    "subscriptionId": self._subscription_id},
                                   deadline=time.monotonic() + 0.1)
        except (OSError, MetricsUiWebSocketError):
            pass
        finally:
            self._ws.abort()
            self._ws.close()
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=1.0)
