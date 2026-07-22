from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

from .defaults import DEFAULT_CHASE_UI_WS_URL, get_default_chase_ui_ws_url
from .frame_identity import (
    build_chase_shadow_reference,
    format_chase_frame_id,
)
from .metrics_ws import MetricsUiWebSocketError, MetricsUiWsClient
from autonomy.vehicle import (
    FRONT_CAMERA_SENSOR_ID,
    CarInterface,
    SensorReadRequest,
    SensorReading,
    SensorSnapshot,
    VehicleAction,
    VehicleCapabilities,
    VehiclePulse,
)


CHASE_SET_CHASER_INPUT = "set-chaser-input"
CHASE_SET_CHASER_CONTROL_SOURCE = "set-chaser-control-source"
CHASE_ATOMIC_EVALUATION_QUERY = "atomic-evaluation-capture"


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _reject_unsupported_sensors(request: SensorReadRequest) -> None:
    unsupported = set(request.requested_sensors) - {FRONT_CAMERA_SENSOR_ID}
    if unsupported:
        raise ValueError(f"unsupported Chase sim sensors requested: {sorted(unsupported)}")


def _nested_get(record: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = record
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _debug_chaser_action_sources(debug: dict[str, Any]) -> dict[str, Any]:
    return {
        "actions.chaserInput.source": _nested_get(debug, ("actions", "chaserInput", "source")),
        "actions.chaserAction.source": _nested_get(debug, ("actions", "chaserAction", "source")),
        "actors.chaser.action.source": _nested_get(debug, ("actors", "chaser", "action", "source")),
    }


def _debug_has_ws_chaser_source(debug: dict[str, Any]) -> bool:
    return any(value == "ws" for value in _debug_chaser_action_sources(debug).values())


def _play_sidebar_values(state: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    sections = state.get("playSidebarSections")
    if not isinstance(sections, list):
        return values
    for section in sections:
        if not isinstance(section, dict):
            continue
        rows = section.get("rows")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id")
            if isinstance(row_id, str) and "value" in row:
                values[row_id] = row.get("value")
    return values


class ChaseSimCar(CarInterface):
    """Chase simulator embodiment accessed through Metrics UI WS control."""

    def __init__(
        self,
        *,
        ws_url: str | None = None,
        timeout_s: float = 5.0,
        vehicle_id: str = "chase-sim-chaser",
    ):
        self.ws_url = (ws_url or get_default_chase_ui_ws_url()).strip() or DEFAULT_CHASE_UI_WS_URL
        self.timeout_s = float(timeout_s)
        self.client = MetricsUiWsClient(self.ws_url, timeout_s=self.timeout_s)
        # Evaluator-only shadow reference from the most recent capture. Not part of
        # SensorSnapshot so it never enters observation/memory inputs.
        self._last_capture_shadow_reference: dict[str, Any] | None = None
        self._last_simulator_frame_index: int | None = None
        self._capabilities = VehicleCapabilities(
            vehicle_id=vehicle_id,
            vehicle_kind="chase-sim-ws",
            can_capture_highres=False,
            sensors={
                FRONT_CAMERA_SENSOR_ID: {
                    "sensor_kind": "camera",
                    "pose": "simulated_fixed_front",
                    "available": True,
                    "default_endpoint": CHASE_ATOMIC_EVALUATION_QUERY,
                    "physical_limitations": (
                        "simulated low-mounted forward-facing view",
                        "no map/debug state exposed through the vehicle interface",
                    ),
                },
            },
            notes=(
                "Applies normalized RC-car-like actions to Chase via Metrics UI WS.",
                "Chase WS control uses fixed scenario speed; throttle magnitude is represented by pulse duration.",
                "Use prepare_for_external_control() before running an external decision model.",
            ),
        )

    @property
    def capabilities(self) -> VehicleCapabilities:
        return self._capabilities

    def prepare_for_external_control(self) -> dict[str, Any]:
        """Switch Chase to Play/WS control and verify the simulator consumes WS input."""
        started_ms = int(time.time() * 1000)
        sidebar_ack = self.client.set_play_app()
        play_debug_before = self._wait_for_play_debug()
        play_ack = self.client.play()
        control_ack = self._play_game_command_with_retry(
            CHASE_SET_CHASER_CONTROL_SOURCE,
            {"source": "ws"},
        )
        idle_ack = self._play_game_command_with_retry(
            CHASE_SET_CHASER_INPUT,
            {"motion": "none", "steering": 0.0},
        )
        verification = self._wait_for_ws_control_source()
        playback_verification = self._optional_frame_advance(
            min_frame_index=play_debug_before.get("frameIndex"),
        )
        return {
            "set_play_app": sidebar_ack,
            "play": play_ack,
            "playback_verification": playback_verification,
            "set_control_source": control_ack,
            "set_idle_input": idle_ack,
            "verification": verification,
            "ws_url": self.ws_url,
            "started_at_ms": started_ms,
            "completed_at_ms": int(time.time() * 1000),
        }

    def _optional_frame_advance(self, *, min_frame_index: Any = None) -> dict[str, Any]:
        try:
            return self._wait_for_frame_advance(
                min_frame_index=min_frame_index,
                timeout_s=min(1.0, self.timeout_s),
            )
        except MetricsUiWebSocketError as exc:
            return {
                "verified": False,
                "warning": str(exc),
            }

    def _play_game_command_with_retry(
        self,
        command_id: str,
        payload: Any = None,
        *,
        attempts: int = 3,
    ) -> dict[str, Any]:
        errors: list[str] = []
        for attempt in range(1, attempts + 1):
            try:
                ack = self.client.play_game_command(command_id, payload)
                return {
                    "attempt": attempt,
                    "ack": ack,
                }
            except MetricsUiWebSocketError as exc:
                errors.append(str(exc))
                time.sleep(min(0.25 * attempt, 0.75))
        raise MetricsUiWebSocketError(
            f"Chase play command {command_id!r} failed after {attempts} attempts: {errors}",
        )

    def _wait_for_play_debug(self, *, timeout_s: float | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + float(timeout_s or self.timeout_s)
        last_error: str | None = None
        while time.monotonic() < deadline:
            try:
                debug = self._read_debug()
                if debug.get("gameId") == "chase":
                    return debug
                last_error = f"unexpected gameId={debug.get('gameId')!r}"
            except MetricsUiWebSocketError as exc:
                last_error = str(exc)
            time.sleep(0.15)
        raise MetricsUiWebSocketError(
            f"Chase Play debug did not become available before timeout: {last_error}",
        )

    def _read_debug(self) -> dict[str, Any]:
        """Read simulator debug for adapter readiness checks only."""
        return self.client.get_play_debug(timeout_s=self.timeout_s)

    def _wait_for_frame_advance(
        self,
        *,
        min_frame_index: Any = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + float(timeout_s or self.timeout_s)
        baseline = min_frame_index if isinstance(min_frame_index, (int, float)) else None
        latest: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            debug = self._wait_for_play_debug(timeout_s=min(1.0, max(0.1, deadline - time.monotonic())))
            latest = debug
            frame_index = debug.get("frameIndex")
            if not isinstance(baseline, (int, float)):
                return {
                    "frame_index": frame_index,
                    "verified": True,
                }
            if isinstance(frame_index, (int, float)) and frame_index > baseline:
                return {
                    "baseline_frame_index": baseline,
                    "frame_index": frame_index,
                    "verified": True,
                }
            time.sleep(0.1)
        raise MetricsUiWebSocketError(
            "Chase timeline did not advance before timeout; "
            f"baseline_frame={baseline}, last_frame={None if latest is None else latest.get('frameIndex')}. "
            "Refresh/open the Metrics UI Play frontend if this persists.",
        )

    def _wait_for_ws_control_source(
        self,
        *,
        min_frame_index: Any = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + float(timeout_s or self.timeout_s)
        baseline = min_frame_index if isinstance(min_frame_index, (int, float)) else None
        latest: dict[str, Any] | None = None
        latest_sidebar_source: Any = None
        while time.monotonic() < deadline:
            debug = self._wait_for_play_debug(timeout_s=min(1.0, max(0.1, deadline - time.monotonic())))
            latest = debug
            try:
                state = self.client.get_state(timeout_s=min(1.0, max(0.1, deadline - time.monotonic())))
                latest_sidebar_source = _play_sidebar_values(state).get("chaser-control-source")
            except MetricsUiWebSocketError:
                latest_sidebar_source = None
            frame_index = debug.get("frameIndex")
            frame_advanced = not isinstance(baseline, (int, float))
            if isinstance(frame_index, (int, float)) and isinstance(baseline, (int, float)):
                frame_advanced = frame_index > baseline
            ws_control_source = _debug_has_ws_chaser_source(debug) or latest_sidebar_source == "ws"
            if frame_advanced and ws_control_source:
                return {
                    "frame_index": frame_index,
                    "sources": _debug_chaser_action_sources(debug),
                    "sidebar_source": latest_sidebar_source,
                    "verified": True,
                }
            time.sleep(0.1)

        sources = _debug_chaser_action_sources(latest or {})
        raise MetricsUiWebSocketError(
            "Chase did not report WS chaser control before timeout; "
            f"last_frame={None if latest is None else latest.get('frameIndex')}, "
            f"sources={sources}, sidebar_source={latest_sidebar_source!r}",
        )

    def stop(self) -> None:
        self.execute_action(VehicleAction(), throttle=0.0)

    def execute_action(
        self,
        action: VehicleAction,
        *,
        throttle: float,
        recording: bool = False,
    ) -> dict[str, Any]:
        del recording
        moving = max(0.0, min(1.0, float(throttle))) > 0.0
        payload = {
            "motion": "forward" if action.forward and moving else "reverse" if action.reverse and moving else "none",
            "forward": bool(action.forward and moving),
            "reverse": bool(action.reverse and moving),
            "steering": action.steering,
        }
        ack = self.client.play_game_command(CHASE_SET_CHASER_INPUT, payload)
        return {
            "action": action.to_dict(),
            "throttle": max(0.0, min(1.0, float(throttle))),
            "payload": payload,
            "ack": ack,
            "sent_at_ms": int(time.time() * 1000),
        }

    def execute_pulse(self, pulse: VehiclePulse) -> dict[str, Any]:
        started_ms = int(time.time() * 1000)
        try:
            command = self.execute_action(
                pulse.action,
                throttle=pulse.throttle,
                recording=pulse.recording,
            )
            time.sleep(pulse.duration_s)
        finally:
            self.stop()

        if pulse.settle_s > 0:
            time.sleep(pulse.settle_s)

        return {
            "label": pulse.label,
            "pulse": pulse.to_dict(),
            "command": command,
            "started_at_ms": started_ms,
            "completed_at_ms": int(time.time() * 1000),
        }

    @property
    def last_capture_shadow_reference(self) -> dict[str, Any] | None:
        """Evaluator-only shadow reference from the most recent front-camera capture."""

        return self._last_capture_shadow_reference

    @property
    def last_simulator_frame_index(self) -> int | None:
        return self._last_simulator_frame_index

    def _capture_front_camera(self, path: Path, endpoint: str) -> dict[str, Any]:
        """Capture one atomic image/identity/evaluator-reference response."""

        path.parent.mkdir(parents=True, exist_ok=True)
        capture_response = self.client.play_game_query(
            CHASE_ATOMIC_EVALUATION_QUERY,
            {"actorId": "chaser", "width": 640, "height": 480},
            timeout_s=self.timeout_s,
        )
        shadow_reference = build_chase_shadow_reference(capture_response)
        if shadow_reference is None:
            raise ValueError(
                "Chase atomic evaluation capture has an invalid identity or control reference"
            )

        sensor = capture_response.get("sensor")
        image = sensor.get("image") if isinstance(sensor, dict) else None
        if not isinstance(image, dict):
            raise ValueError("Chase atomic evaluation capture has no sensor image")
        width = image.get("width")
        height = image.get("height")
        if (
            isinstance(width, bool)
            or not isinstance(width, int)
            or width <= 0
            or isinstance(height, bool)
            or not isinstance(height, int)
            or height <= 0
        ):
            raise ValueError("Chase atomic evaluation capture has invalid image dimensions")

        byte_count = 0
        if isinstance(image.get("svg"), str) and path.suffix.lower() == ".svg":
            path.write_text(image["svg"], encoding="utf-8")
            content_type = "image/svg+xml"
        elif isinstance(image.get("dataUrl"), str):
            content_type, data = _decode_data_url(image["dataUrl"])
            byte_count = len(data)
            path.write_bytes(data)
        else:
            raise ValueError(
                "Chase atomic evaluation capture has no image encoding compatible "
                f"with {path.suffix or 'the requested output'}"
            )
        if byte_count == 0 and path.exists():
            byte_count = path.stat().st_size

        frame_index = int(shadow_reference["simulator_frame_index"])
        simulation_epoch = str(shadow_reference["simulation_epoch"])
        self._last_simulator_frame_index = frame_index
        self._last_capture_shadow_reference = shadow_reference

        capture: dict[str, Any] = {
            "endpoint": CHASE_ATOMIC_EVALUATION_QUERY,
            "requested_endpoint": endpoint,
            "path": str(path),
            "bytes": byte_count,
            "content_type": content_type,
            "width": width,
            "height": height,
            "captured_at_ms": int(time.time() * 1000),
            "capture_id": shadow_reference["capture_id"],
            "simulator_frame_index": frame_index,
            "simulation_epoch": simulation_epoch,
            "frame_index": frame_index,
            "frame_id": format_chase_frame_id(frame_index),
            "identity_pairing": "atomic_evaluation_capture",
        }
        return capture

    def read_sensors(self, request: SensorReadRequest) -> SensorSnapshot:
        _reject_unsupported_sensors(request)
        started_ms = _timestamp_ms()
        readings: dict[str, SensorReading] = {}
        self._last_capture_shadow_reference = None
        self._last_simulator_frame_index = None

        if request.sensor_requested(FRONT_CAMERA_SENSOR_ID):
            capture = self._capture_front_camera(
                request.front_camera_path(),
                endpoint=request.front_camera_endpoint,
            )
            readings[FRONT_CAMERA_SENSOR_ID] = SensorReading(
                sensor_id=FRONT_CAMERA_SENSOR_ID,
                sensor_kind="camera",
                path=capture.get("path"),
                captured_at_ms=int(capture.get("captured_at_ms") or _timestamp_ms()),
                metadata=capture,
            )

        snapshot_metadata: dict[str, Any] = {"vehicle": self.capabilities.to_dict()}
        if self._last_simulator_frame_index is not None:
            snapshot_metadata["simulator_frame_index"] = self._last_simulator_frame_index
            snapshot_metadata["frame_id"] = format_chase_frame_id(self._last_simulator_frame_index)
        if isinstance(self._last_capture_shadow_reference, dict):
            snapshot_metadata["simulation_epoch"] = self._last_capture_shadow_reference[
                "simulation_epoch"
            ]

        return SensorSnapshot(
            read_id=request.read_id,
            readings=readings,
            started_at_ms=started_ms,
            completed_at_ms=_timestamp_ms(),
            request=request.to_dict(),
            metadata=snapshot_metadata,
        )


def _decode_data_url(data_url: str) -> tuple[str, bytes]:
    header, _, payload = data_url.partition(",")
    if not payload:
        return "application/octet-stream", data_url.encode("utf-8")
    content_type = header.removeprefix("data:").split(";", 1)[0] or "application/octet-stream"
    if ";base64" in header:
        return content_type, base64.b64decode(payload)
    return content_type, payload.encode("utf-8")
