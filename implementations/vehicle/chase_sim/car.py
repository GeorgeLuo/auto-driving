from __future__ import annotations

import base64
import math
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote_to_bytes

from .defaults import DEFAULT_CHASE_UI_WS_URL, get_default_chase_ui_ws_url
from .frame_identity import (
    ChaseCaptureValidationError,
    evaluate_chase_evaluator_reference,
    format_chase_frame_id,
    validate_chase_sensor_capture,
)
from .metrics_ws import (
    MetricsUiWebSocketError,
    MetricsUiWsClient,
    build_chase_session_fingerprint,
    compare_chase_session_fingerprints,
)
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
CHASE_PASSIVE_CAMERA_ID = "front_camera"
# Metrics UI delivery failures: absent frontend, hung frontend, or mid-request drop.
# Automa normalizes them to frontend_disconnected so status/recovery stay on
# simulator_frontend (reload browser), while the precise protocol code is kept
# in error details.
FRONTEND_DELIVERY_PROTOCOL_CODES = frozenset(
    {
        "frontend_not_connected",
        "frontend_unresponsive",
        "frontend_disconnected",
    }
)
CHASE_PASSIVE_PRESERVED_FIELDS = (
    "gameId",
    "scenarioId",
    "simulationEpoch",
    "playback",
    "controlSource",
    "controlInput",
    "actorId",
    "cameraId",
)

_PASSIVE_RECEIPT_FIELD_NAMES = {
    "gameId": "game_id",
    "scenarioId": "scenario_id",
    "simulationEpoch": "simulation_epoch",
    "playback": "playback",
    "controlSource": "control_source",
    "controlInput": "control_input",
    "actorId": "actor_id",
    "cameraId": "camera_id",
}


class ChasePassiveCaptureError(RuntimeError):
    """Structured failure at the passive simulator-observation boundary."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        self.code = code
        self.detail = message
        self.details = details or {}
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        layer = {
            "simulator_unreachable": "simulator_server",
            "frontend_disconnected": "simulator_frontend",
            "wrong_game": "chase_game",
            "front_view_unavailable": "vehicle",
            "simulator_capability_missing": "passive_capture",
            "simulator_state_changed": "passive_capture",
        }.get(self.code, "passive_capture")
        return {
            "schema": "automa_cli_error_v1",
            "error": self.code,
            "layer": layer,
            "message": self.detail,
            "details": self.details,
            "recovery": None,
            "exit_code": 1,
        }


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _normalize_passive_receipt_fingerprint(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    normalized = {
        normalized_name: source.get(protocol_name)
        for protocol_name, normalized_name in _PASSIVE_RECEIPT_FIELD_NAMES.items()
    }
    unknown_fields: list[str] = []
    for protocol_name, normalized_name in _PASSIVE_RECEIPT_FIELD_NAMES.items():
        field_value = normalized[normalized_name]
        if protocol_name == "controlInput":
            valid = protocol_name in source and (
                field_value is None
                or (
                    isinstance(field_value, dict)
                    and isinstance(field_value.get("source"), str)
                    and bool(field_value["source"].strip())
                    and isinstance(field_value.get("forward"), bool)
                    and isinstance(field_value.get("reverse"), bool)
                    and isinstance(field_value.get("steering"), (int, float))
                    and not isinstance(field_value.get("steering"), bool)
                    and math.isfinite(float(field_value["steering"]))
                )
            )
        elif protocol_name == "playback":
            valid = (
                isinstance(field_value, dict)
                and isinstance(field_value.get("frameIndex"), int)
                and not isinstance(field_value.get("frameIndex"), bool)
                and field_value["frameIndex"] >= 0
                and field_value.get("phase")
                in {"running", "paused-before-actions"}
                and isinstance(field_value.get("pendingAction"), bool)
            )
        else:
            valid = isinstance(field_value, str) and bool(field_value.strip())
        if not valid:
            unknown_fields.append(normalized_name)
    return {
        "schema": "chase_session_fingerprint_v1",
        **normalized,
        "unknown_fields": unknown_fields,
    }


def _compare_passive_receipt_fingerprints(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    field_names = tuple(_PASSIVE_RECEIPT_FIELD_NAMES.values())
    unknown_fields = sorted(
        {
            *(
                str(item)
                for item in before.get("unknown_fields", [])
                if isinstance(item, str)
            ),
            *(
                str(item)
                for item in after.get("unknown_fields", [])
                if isinstance(item, str)
            ),
        }
    )
    changed_fields = [
        field_name
        for field_name in field_names
        if before.get(field_name) != after.get(field_name)
    ]
    return {
        "preserved": not unknown_fields and not changed_fields,
        "unknown_fields": unknown_fields,
        "changed_fields": changed_fields,
        "before": before,
        "after": after,
    }


def _passive_receipt_environment(
    fingerprint: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: fingerprint.get(key)
        for key in (
            "game_id",
            "scenario_id",
            "simulation_epoch",
            "playback",
            "control_source",
            "control_input",
        )
    }


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
        self._last_evaluator_reference: dict[str, Any] = {
            "status": "unavailable",
            "reason": "not_captured",
            "path": "evaluator.reference",
        }
        self._last_passive_capture: dict[str, Any] | None = None
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

    @property
    def last_evaluator_reference(self) -> dict[str, Any]:
        """Frame-scoped evaluator-reference status without evaluator payload."""

        return dict(self._last_evaluator_reference)

    @property
    def last_passive_capture(self) -> dict[str, Any] | None:
        """Most recent passive-capture capability and preservation receipt."""

        return dict(self._last_passive_capture) if self._last_passive_capture else None

    def inspect_passive_capture(
        self,
        *,
        timeout_s: float | None = None,
        include_image: bool = False,
    ) -> dict[str, Any]:
        """Read one atomic frame and prove that the simulator session was preserved."""

        operation_timeout = float(
            self.timeout_s if timeout_s is None else timeout_s
        )
        if not math.isfinite(operation_timeout) or operation_timeout <= 0:
            raise ValueError("passive capture timeout must be finite and greater than zero")
        started = time.monotonic()
        deadline = started + operation_timeout
        phases: dict[str, dict[str, Any]] = {}

        before_state = self._passive_phase(
            "state_before",
            phases,
            deadline,
            self.client.get_state,
            error_code="simulator_unreachable",
        )
        before_debug = self._passive_phase(
            "debug_before",
            phases,
            deadline,
            self.client.get_play_debug,
            error_code="frontend_disconnected",
        )
        if before_debug.get("gameId") != "chase":
            raise ChasePassiveCaptureError(
                code="wrong_game",
                message=(
                    "The connected frontend is not exposing Chase; "
                    f"reported gameId={before_debug.get('gameId')!r}."
                ),
                details={"game_id": before_debug.get("gameId"), "phases": phases},
            )
        before = build_chase_session_fingerprint(
            state=before_state,
            debug=before_debug,
        )

        capture = self._passive_phase(
            "sensor_capture",
            phases,
            deadline,
            self.client.play_game_query,
            CHASE_ATOMIC_EVALUATION_QUERY,
            {
                "actorId": "chaser",
                "cameraId": CHASE_PASSIVE_CAMERA_ID,
                "width": 640,
                "height": 480,
            },
            error_code="front_view_unavailable",
        )
        passive_observation = capture.get("passiveObservation")
        if isinstance(passive_observation, dict):
            return self._passive_receipt_result(
                capture=capture,
                passive_observation=passive_observation,
                fallback_environment=before,
                phases=phases,
                operation_timeout=operation_timeout,
                started=started,
                include_image=include_image,
            )

        sensor = validate_chase_sensor_capture(capture, expected_actor_id="chaser")
        evaluator = evaluate_chase_evaluator_reference(capture, sensor=sensor)

        after_state = self._passive_phase(
            "state_after",
            phases,
            deadline,
            self.client.get_state,
            error_code="simulator_capability_missing",
        )
        after_debug = self._passive_phase(
            "debug_after",
            phases,
            deadline,
            self.client.get_play_debug,
            error_code="simulator_capability_missing",
        )
        after = build_chase_session_fingerprint(
            state=after_state,
            debug=after_debug,
        )
        preservation = compare_chase_session_fingerprints(before, after)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        if preservation["changed_fields"]:
            status = "unavailable"
            code = "simulator_state_changed"
            reason = "session_fingerprint_changed"
        elif preservation["unknown_fields"]:
            status = "unsupported"
            code = "simulator_capability_missing"
            reason = "preservation_fields_unavailable"
        else:
            status = "available"
            code = None
            reason = None

        evaluator_status = {
            key: value
            for key, value in evaluator.items()
            if key != "reference" and value is not None
        }
        result: dict[str, Any] = {
            "schema": "chase_passive_capture_v1",
            "status": status,
            "code": code,
            "reason": reason,
            "sensor": sensor,
            "evaluator_reference": evaluator_status,
            "session_preservation": preservation,
            "environment": {
                "game_id": before.get("game_id"),
                "scenario_id": before.get("scenario_id"),
                "simulation_epoch": before.get("simulation_epoch"),
                "playback": before.get("playback"),
                "control_source": before.get("control_source"),
                "control_input": before.get("control_input"),
            },
            "timeout_s": operation_timeout,
            "elapsed_ms": elapsed_ms,
            "phases": phases,
            "allowed_operations": [
                "get_state",
                "get_play_debug",
                f"play_game_query:{CHASE_ATOMIC_EVALUATION_QUERY}",
            ],
            "mutation_attempted": False,
        }
        if include_image:
            raw_sensor = capture.get("sensor")
            raw_image = raw_sensor.get("image") if isinstance(raw_sensor, dict) else None
            result["image"] = dict(raw_image) if isinstance(raw_image, dict) else None

        self._last_capture_shadow_reference = (
            evaluator.get("reference")
            if evaluator.get("status") == "available"
            and isinstance(evaluator.get("reference"), dict)
            else None
        )
        self._last_evaluator_reference = evaluator_status
        self._last_simulator_frame_index = int(sensor["simulator_frame_index"])
        self._last_passive_capture = {
            key: value for key, value in result.items() if key != "image"
        }
        return result

    def _passive_receipt_result(
        self,
        *,
        capture: dict[str, Any],
        passive_observation: dict[str, Any],
        fallback_environment: dict[str, Any],
        phases: dict[str, dict[str, Any]],
        operation_timeout: float,
        started: float,
        include_image: bool,
    ) -> dict[str, Any]:
        supported = passive_observation.get("supported") is True
        reason_record = (
            passive_observation.get("reason")
            if isinstance(passive_observation.get("reason"), dict)
            else {}
        )
        preservation_record = (
            passive_observation.get("preservation")
            if isinstance(passive_observation.get("preservation"), dict)
            else {}
        )
        before_source = (
            preservation_record.get("before")
            if supported
            else passive_observation.get("before")
        )
        after_source = (
            preservation_record.get("after")
            if supported
            else passive_observation.get("after")
        )
        before = _normalize_passive_receipt_fingerprint(before_source)
        after = _normalize_passive_receipt_fingerprint(after_source)
        preservation = _compare_passive_receipt_fingerprints(before, after)

        declared_fields = passive_observation.get("preservedFields")
        declared_field_set = {
            str(item)
            for item in declared_fields
            if isinstance(item, str)
        } if isinstance(declared_fields, list) else set()
        missing_declared_fields = [
            _PASSIVE_RECEIPT_FIELD_NAMES[field_name]
            for field_name in CHASE_PASSIVE_PRESERVED_FIELDS
            if field_name not in declared_field_set
        ]
        if supported:
            preservation["unknown_fields"] = sorted(
                {
                    *preservation["unknown_fields"],
                    *missing_declared_fields,
                }
            )
            preservation["preserved"] = bool(
                preservation_record.get("preserved") is True
                and not preservation["unknown_fields"]
                and not preservation["changed_fields"]
            )

        query_id = passive_observation.get("queryId")
        actor_id = passive_observation.get("actorId")
        camera_id = passive_observation.get("cameraId")
        if supported and query_id != CHASE_ATOMIC_EVALUATION_QUERY:
            preservation["unknown_fields"] = sorted(
                {*preservation["unknown_fields"], "query_id"}
            )
        if supported and actor_id != "chaser":
            preservation["unknown_fields"] = sorted(
                {*preservation["unknown_fields"], "actor_id"}
            )
        if supported and camera_id != CHASE_PASSIVE_CAMERA_ID:
            preservation["unknown_fields"] = sorted(
                {*preservation["unknown_fields"], "camera_id"}
            )
        if preservation["unknown_fields"]:
            preservation["preserved"] = False

        sensor: dict[str, Any] | None = None
        evaluator_status: dict[str, Any] = {
            "status": "unavailable",
            "reason": "reference_missing",
            "path": "evaluator.reference",
        }
        evaluator: dict[str, Any] | None = None
        if supported:
            sensor = validate_chase_sensor_capture(
                capture,
                expected_actor_id="chaser",
            )
            evaluator = evaluate_chase_evaluator_reference(capture, sensor=sensor)
            evaluator_status = {
                key: value
                for key, value in evaluator.items()
                if key != "reference" and value is not None
            }
            playback = (
                before.get("playback")
                if isinstance(before.get("playback"), dict)
                else {}
            )
            receipt_identity = {
                "game_id": before.get("game_id"),
                "simulation_epoch": before.get("simulation_epoch"),
                "simulator_frame_index": playback.get("frameIndex"),
                "actor_id": before.get("actor_id"),
                "camera_id": before.get("camera_id"),
            }
            sensor_identity = {
                "game_id": sensor.get("game_id"),
                "simulation_epoch": sensor.get("simulation_epoch"),
                "simulator_frame_index": sensor.get("simulator_frame_index"),
                "actor_id": sensor.get("actor_id"),
                "camera_id": CHASE_PASSIVE_CAMERA_ID,
            }
            receipt_identity_fields = {
                "game_id": "game_id",
                "simulation_epoch": "simulation_epoch",
                "simulator_frame_index": "playback",
                "actor_id": "actor_id",
                "camera_id": "camera_id",
            }
            mismatched_identity = [
                key
                for key in receipt_identity
                if receipt_identity_fields[key]
                not in preservation["unknown_fields"]
                if receipt_identity[key] != sensor_identity[key]
            ]
            if mismatched_identity:
                raise ChaseCaptureValidationError(
                    code="capture_identity_invalid",
                    path=(
                        "passiveObservation.preservation.before."
                        + mismatched_identity[0]
                    ),
                    message=(
                        "preservation receipt does not match the captured "
                        f"sensor identity ({mismatched_identity})"
                    ),
                )

        unsupported_reason = str(reason_record.get("code") or "")
        if not supported:
            reason_changed_fields = reason_record.get("changedFields")
            if isinstance(reason_changed_fields, list):
                preservation["changed_fields"] = sorted(
                    {
                        *preservation["changed_fields"],
                        *(
                            _PASSIVE_RECEIPT_FIELD_NAMES.get(str(item), str(item))
                            for item in reason_changed_fields
                        ),
                    }
                )
            preservation["preserved"] = False

        if not supported and unsupported_reason == "session_changed":
            status = "unavailable"
            code = "simulator_state_changed"
            reason = "session_fingerprint_changed"
        elif not supported:
            status = "unsupported"
            code = "simulator_capability_missing"
            reason = unsupported_reason or "passive_observation_unsupported"
        elif preservation["changed_fields"]:
            status = "unavailable"
            code = "simulator_state_changed"
            reason = "session_fingerprint_changed"
        elif not preservation["preserved"]:
            status = "unsupported"
            code = "simulator_capability_missing"
            reason = "preservation_receipt_invalid"
        else:
            status = "available"
            code = None
            reason = None

        environment = (
            _passive_receipt_environment(before)
            if not before["unknown_fields"]
            else {
                key: fallback_environment.get(key)
                for key in (
                    "game_id",
                    "scenario_id",
                    "simulation_epoch",
                    "playback",
                    "control_source",
                    "control_input",
                )
            }
        )
        result: dict[str, Any] = {
            "schema": "chase_passive_capture_v1",
            "status": status,
            "code": code,
            "reason": reason,
            "sensor": sensor,
            "evaluator_reference": evaluator_status,
            "session_preservation": preservation,
            "environment": environment,
            "timeout_s": operation_timeout,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "phases": phases,
            "allowed_operations": [
                "get_state",
                "get_play_debug",
                f"play_game_query:{CHASE_ATOMIC_EVALUATION_QUERY}",
            ],
            "mutation_attempted": False,
            "passive_observation": {
                "supported": supported,
                "query_id": query_id,
                "actor_id": actor_id,
                "camera_id": camera_id,
                "preserved_fields": (
                    sorted(declared_field_set)
                    if declared_field_set
                    else []
                ),
                "reason": reason_record or None,
            },
        }
        if include_image and status == "available":
            raw_sensor = capture.get("sensor")
            raw_image = (
                raw_sensor.get("image")
                if isinstance(raw_sensor, dict)
                else None
            )
            result["image"] = (
                dict(raw_image)
                if isinstance(raw_image, dict)
                else None
            )

        self._last_capture_shadow_reference = (
            evaluator.get("reference")
            if isinstance(evaluator, dict)
            and evaluator.get("status") == "available"
            and isinstance(evaluator.get("reference"), dict)
            else None
        )
        self._last_evaluator_reference = evaluator_status
        self._last_simulator_frame_index = (
            int(sensor["simulator_frame_index"])
            if isinstance(sensor, dict)
            else None
        )
        self._last_passive_capture = {
            key: value
            for key, value in result.items()
            if key != "image"
        }
        return result

    def _passive_phase(
        self,
        name: str,
        phases: dict[str, dict[str, Any]],
        deadline: float,
        operation: Any,
        *args: Any,
        error_code: str = "simulator_capability_missing",
    ) -> dict[str, Any]:
        phase_started = time.monotonic()
        remaining = deadline - phase_started
        if remaining <= 0:
            raise ChasePassiveCaptureError(
                code=error_code,
                message=f"Passive capture timed out before {name}.",
                details={"incomplete_phase": name, "phases": phases},
            )
        try:
            value = operation(*args, timeout_s=remaining)
        except ChaseCaptureValidationError:
            raise
        except (MetricsUiWebSocketError, OSError, TimeoutError, ValueError) as exc:
            elapsed_ms = int((time.monotonic() - phase_started) * 1000)
            protocol_code = (
                exc.code
                if isinstance(exc, MetricsUiWebSocketError) and isinstance(exc.code, str)
                else None
            )
            is_frontend_delivery = protocol_code in FRONTEND_DELIVERY_PROTOCOL_CODES
            effective_error_code = (
                "frontend_disconnected" if is_frontend_delivery else error_code
            )
            protocol_error = (
                dict(exc.details)
                if isinstance(exc, MetricsUiWebSocketError)
                and isinstance(exc.details, dict)
                else {}
            )
            if protocol_code and "code" not in protocol_error:
                protocol_error["code"] = protocol_code
            if is_frontend_delivery:
                minimum_external_change = (
                    "Open or reload the Metrics UI browser frontend at the exact "
                    "HTTP origin, then rerun status."
                )
            else:
                minimum_external_change = (
                    "Metrics UI must expose atomic-evaluation-capture and the "
                    "required session fingerprint fields without mutation."
                )
            raise ChasePassiveCaptureError(
                code=effective_error_code,
                message=(
                    f"Passive capture could not complete {name} after "
                    f"{elapsed_ms}ms: {exc}"
                ),
                details={
                    "incomplete_phase": name,
                    "elapsed_ms": elapsed_ms,
                    "protocol_evidence": str(exc),
                    "protocol_code": protocol_code,
                    "protocol_error": protocol_error or None,
                    "minimum_external_change": minimum_external_change,
                    "mutation_attempted": False,
                    "phases": phases,
                },
            ) from exc
        phases[name] = {
            "status": "complete",
            "duration_ms": int((time.monotonic() - phase_started) * 1000),
        }
        if not isinstance(value, dict):
            raise ChasePassiveCaptureError(
                code="simulator_capability_missing",
                message=f"Passive capture phase {name} returned a non-object value.",
                details={"incomplete_phase": name, "phases": phases},
            )
        return value

    def _capture_front_camera(self, path: Path, endpoint: str) -> dict[str, Any]:
        """Capture one atomic image/identity/evaluator-reference response."""

        path.parent.mkdir(parents=True, exist_ok=True)
        passive = self.inspect_passive_capture(
            timeout_s=self.timeout_s,
            include_image=True,
        )
        if passive["status"] != "available":
            code = str(passive.get("code") or "simulator_capability_missing")
            if code == "simulator_state_changed":
                message = "Simulator session changed during passive capture."
            else:
                unknown = passive["session_preservation"].get("unknown_fields", [])
                message = (
                    "Metrics UI cannot prove passive session preservation; "
                    f"missing fields: {', '.join(unknown) or 'unknown'}."
                )
            raise ChasePassiveCaptureError(
                code=code,
                message=message,
                details={
                    "session_preservation": passive["session_preservation"],
                    "mutation_attempted": False,
                },
            )

        sensor = passive["sensor"]
        image = passive.get("image")
        if not isinstance(image, dict):
            raise ChaseCaptureValidationError(
                code="capture_image_invalid",
                path="sensor.image",
                message="validated image payload is unavailable",
            )
        width = int(sensor["image"]["width"])
        height = int(sensor["image"]["height"])

        byte_count = 0
        suffix = path.suffix.lower()
        if isinstance(image.get("dataUrl"), str) and image["dataUrl"]:
            content_type, data = _decode_data_url(image["dataUrl"])
            byte_count = len(data)
            path.write_bytes(data)
        elif isinstance(image.get("svg"), str) and image["svg"].strip():
            # Validated captures require raster dataUrl; keep a structured error
            # if an unvalidated/legacy payload still reaches the write path.
            raise ChaseCaptureValidationError(
                code="capture_image_invalid",
                path="sensor.image.svg",
                message=(
                    "SVG-only captures are not accepted for "
                    f"{suffix or 'the requested output'}; provide a decodable "
                    "raster dataUrl compatible with the worker .png output"
                ),
            )
        else:
            raise ChaseCaptureValidationError(
                code="capture_image_invalid",
                path="sensor.image.dataUrl",
                message=(
                    "Chase atomic evaluation capture has no raster image encoding "
                    f"compatible with {suffix or 'the requested output'}"
                ),
            )
        if byte_count == 0 and path.exists():
            byte_count = path.stat().st_size

        frame_index = int(sensor["simulator_frame_index"])
        simulation_epoch = str(sensor["simulation_epoch"])

        capture: dict[str, Any] = {
            "endpoint": CHASE_ATOMIC_EVALUATION_QUERY,
            "requested_endpoint": endpoint,
            "path": str(path),
            "bytes": byte_count,
            "content_type": content_type,
            "width": width,
            "height": height,
            "captured_at_ms": int(time.time() * 1000),
            "capture_id": sensor["capture_id"],
            "simulator_frame_index": frame_index,
            "simulation_epoch": simulation_epoch,
            "frame_index": frame_index,
            "frame_id": format_chase_frame_id(frame_index),
            "identity_pairing": "atomic_evaluation_capture",
            "evaluator_reference": passive["evaluator_reference"],
            "passive_capture": {
                "status": passive["status"],
                "session_preservation": passive["session_preservation"],
                "mutation_attempted": False,
            },
        }
        return capture

    def read_sensors(self, request: SensorReadRequest) -> SensorSnapshot:
        _reject_unsupported_sensors(request)
        started_ms = _timestamp_ms()
        readings: dict[str, SensorReading] = {}
        self._last_capture_shadow_reference = None
        self._last_evaluator_reference = {
            "status": "unavailable",
            "reason": "not_captured",
            "path": "evaluator.reference",
        }
        self._last_passive_capture = None
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
        if self._last_passive_capture:
            sensor = self._last_passive_capture.get("sensor")
            if isinstance(sensor, dict):
                snapshot_metadata["simulation_epoch"] = sensor.get("simulation_epoch")
            snapshot_metadata["passive_capture"] = {
                "status": self._last_passive_capture.get("status"),
                "mutation_attempted": False,
            }
            snapshot_metadata["evaluator_reference"] = self.last_evaluator_reference

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
    return content_type, unquote_to_bytes(payload)
