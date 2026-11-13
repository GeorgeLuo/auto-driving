from __future__ import annotations

import logging
import math
import time

from .camera_stream import ChaseCameraStream
from .defaults import DEFAULT_CHASE_UI_WS_URL, get_default_chase_ui_ws_url
from .frame_identity import (
    ChaseCaptureValidationError,
    evaluate_chase_evaluator_reference,
    validate_chase_sensor_capture,
)
from .metrics_ws import (
    MetricsUiWebSocketError,
    MetricsUiWsClient,
)
from autonomy.vehicle import (
    FRONT_CAMERA_SENSOR_ID,
    CarInterface,
    VehicleAction,
    VehicleCapabilities,
    VehiclePulse,
    run_vehicle_pulse,
)


VEHICLE_KIND = "chase-sim-ws"
CHASE_SET_CHASER_INPUT = "set-chaser-input"
logger = logging.getLogger(__name__)

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
from .session import (
    CHASE_PASSIVE_PRESERVED_FIELDS, PASSIVE_RECEIPT_FIELD_NAMES, ChasePassiveCaptureError,
    normalize_passive_receipt_fingerprint, compare_passive_receipt_fingerprints,
    passive_receipt_environment, build_chase_session_fingerprint,
)
from .steering import chase_steering


def _nested_get(record: dict[str, object], path: tuple[str, ...]) -> object:
    value: object = record
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _debug_chaser_action_sources(debug: dict[str, object]) -> dict[str, object]:
    return {
        "actions.chaserInput.source": _nested_get(debug, ("actions", "chaserInput", "source")),
        "actions.chaserAction.source": _nested_get(debug, ("actions", "chaserAction", "source")),
        "actors.chaser.action.source": _nested_get(debug, ("actors", "chaser", "action", "source")),
    }


def _debug_has_ws_chaser_source(debug: dict[str, object]) -> bool:
    return any(value == "ws" for value in _debug_chaser_action_sources(debug).values())


def _play_sidebar_values(state: dict[str, object]) -> dict[str, object]:
    values: dict[str, object] = {}
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
        self._capabilities = VehicleCapabilities(
            vehicle_id=vehicle_id,
            vehicle_kind=VEHICLE_KIND,
            can_capture_highres=False,
            sensors={
                FRONT_CAMERA_SENSOR_ID: {
                    "sensor_kind": "camera",
                    "pose": "simulated_fixed_front",
                    "available": True,
                    "physical_limitations": (
                        "simulated low-mounted forward-facing view",
                        "no map/debug state exposed through the vehicle interface",
                    ),
                },
            },
            notes=(
                "Applies normalized RC-car-like actions to Chase via Metrics UI WS.",
                "Chase WS has directional throttle at fixed scenario speed; receipts report this quantization.",
                "Control acquisition is managed by the shared execution runtime.",
            ),
        )

    @property
    def capabilities(self) -> VehicleCapabilities:
        return self._capabilities

    def prepare_for_external_control(self) -> dict[str, object]:
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

    def _optional_frame_advance(self, *, min_frame_index: object = None) -> dict[str, object]:
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
        payload: object = None,
        *,
        attempts: int = 3,
    ) -> dict[str, object]:
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

    def _wait_for_play_debug(self, *, timeout_s: float | None = None) -> dict[str, object]:
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

    def _read_debug(self) -> dict[str, object]:
        """Read simulator debug for adapter readiness checks only."""
        return self.client.get_play_debug(timeout_s=self.timeout_s)

    def _wait_for_frame_advance(
        self,
        *,
        min_frame_index: object = None,
        timeout_s: float | None = None,
    ) -> dict[str, object]:
        deadline = time.monotonic() + float(timeout_s or self.timeout_s)
        baseline = min_frame_index if isinstance(min_frame_index, (int, float)) else None
        latest: dict[str, object] | None = None
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
        min_frame_index: object = None,
        timeout_s: float | None = None,
    ) -> dict[str, object]:
        deadline = time.monotonic() + float(timeout_s or self.timeout_s)
        baseline = min_frame_index if isinstance(min_frame_index, (int, float)) else None
        latest: dict[str, object] | None = None
        latest_sidebar_source: object = None
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
    ) -> dict[str, object]:
        del recording
        moving = max(0.0, min(1.0, float(throttle))) > 0.0
        payload = {
            "motion": "forward" if action.forward and moving else "reverse" if action.reverse and moving else "none",
            "forward": bool(action.forward and moving),
            "reverse": bool(action.reverse and moving),
            "steering": chase_steering(action.steering),
        }
        ack = self.client.play_game_command(CHASE_SET_CHASER_INPUT, payload)
        return {
            "action": action.to_dict(),
            "throttle": max(0.0, min(1.0, float(throttle))),
            "payload": payload,
            "boundary": "chase_ws_input",
            "throttle_semantics": "directional_fixed_speed",
            "ack": ack,
            "sent_at_ms": int(time.time() * 1000),
        }

    def execute_pulse(self, pulse: VehiclePulse) -> dict[str, object]:
        return run_vehicle_pulse(self, pulse)

    def inspect_passive_capture(
        self,
        *,
        timeout_s: float | None = None,
        include_image: bool = False,
    ) -> dict[str, object]:
        """Read one atomic frame and prove that the simulator session was preserved."""

        operation_timeout = float(
            self.timeout_s if timeout_s is None else timeout_s
        )
        if not math.isfinite(operation_timeout) or operation_timeout <= 0:
            raise ValueError("passive capture timeout must be finite and greater than zero")
        started = time.monotonic()
        deadline = started + operation_timeout
        phases: dict[str, dict[str, object]] = {}

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
        if not isinstance(passive_observation, dict):
            raise ChasePassiveCaptureError(
                code="simulator_capability_missing",
                message="Metrics UI did not return a passive preservation receipt; update Metrics UI.",
                details={"phases": phases, "mutation_attempted": False},
            )
        return self._passive_receipt_result(
            capture=capture, passive_observation=passive_observation,
            fallback_environment=before, phases=phases,
            operation_timeout=operation_timeout, started=started, include_image=include_image,
        )

    def _passive_receipt_result(
        self,
        *,
        capture: dict[str, object],
        passive_observation: dict[str, object],
        fallback_environment: dict[str, object],
        phases: dict[str, dict[str, object]],
        operation_timeout: float,
        started: float,
        include_image: bool,
    ) -> dict[str, object]:
        supported = passive_observation.get("supported") is True
        reason_record = _record(passive_observation.get("reason"))
        before, preservation, declared_field_set = _receipt_preservation(
            passive_observation, supported=supported, reason_record=reason_record
        )
        sensor: dict[str, object] | None = None
        evaluator_status: dict[str, object] = {
            "status": "unavailable",
            "reason": "reference_missing",
            "path": "evaluator.reference",
        }
        if supported:
            sensor, _, evaluator_status = _validated_capture(
                capture, before=before, unknown_fields=preservation["unknown_fields"], include_image=include_image
            )
        status, code, reason = _receipt_status(
            supported=supported,
            unsupported_reason=str(reason_record.get("code") or ""),
            changed=bool(preservation["changed_fields"]),
            preserved=bool(preservation["preserved"]),
        )
        result: dict[str, object] = {
            "schema": "chase_passive_capture",
            "status": status,
            "code": code,
            "reason": reason,
            "sensor": sensor,
            "evaluator_reference": evaluator_status,
            "session_preservation": preservation,
            "environment": _receipt_environment(before, fallback_environment),
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
                "query_id": passive_observation.get("queryId"),
                "actor_id": passive_observation.get("actorId"),
                "camera_id": passive_observation.get("cameraId"),
                "preserved_fields": sorted(declared_field_set),
                "reason": reason_record or None,
            },
        }
        if include_image and status == "available":
            raw_sensor = capture.get("sensor")
            raw_image = raw_sensor.get("image") if isinstance(raw_sensor, dict) else None
            result["image"] = dict(raw_image) if isinstance(raw_image, dict) else None
        return result

    def _passive_phase(
        self,
        name: str,
        phases: dict[str, dict[str, object]],
        deadline: float,
        operation: object,
        *args: object,
        error_code: str = "simulator_capability_missing",
    ) -> dict[str, object]:
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

    def camera_stream(self) -> ChaseCameraStream:
        """Require the current passive camera stream protocol."""
        usage = self.client.command(
            {"type": "get_play_game_usage"}, response_type="play_game_usage",
        ).payload
        if not isinstance(usage, dict):
            raise MetricsUiWebSocketError("Camera capability response must contain an object")
        protocol = usage.get("protocol")
        camera = protocol.get("cameraStream") if isinstance(protocol, dict) else None
        if not isinstance(camera, dict) or camera.get("supported") is not True:
            raise ChasePassiveCaptureError(
                code="simulator_capability_missing",
                message="Metrics UI does not advertise cameraStream; update Metrics UI.",
            )
        return ChaseCameraStream(self.client)


def _record(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _receipt_preservation(
    passive_observation: dict[str, object],
    *,
    supported: bool,
    reason_record: dict[str, object],
):
    """The receipt's before fingerprint, its preservation verdict, and the fields it declares preserved."""

    preservation_record = _record(passive_observation.get("preservation"))
    source = preservation_record if supported else passive_observation
    before = normalize_passive_receipt_fingerprint(source.get("before"))
    after = normalize_passive_receipt_fingerprint(source.get("after"))
    preservation = compare_passive_receipt_fingerprints(before, after)
    declared_fields = passive_observation.get("preservedFields")
    declared_field_set = (
        {item for item in declared_fields if isinstance(item, str)} if isinstance(declared_fields, list) else set()
    )
    if supported:
        missing_declared_fields = [
            PASSIVE_RECEIPT_FIELD_NAMES[field_name]
            for field_name in CHASE_PASSIVE_PRESERVED_FIELDS
            if field_name not in declared_field_set
        ]
        preservation["unknown_fields"] = sorted({*preservation["unknown_fields"], *missing_declared_fields})
        preservation["preserved"] = bool(
            preservation_record.get("preserved") is True
            and not preservation["unknown_fields"]
            and not preservation["changed_fields"]
        )
        expected_identity = (
            ("query_id", passive_observation.get("queryId"), CHASE_ATOMIC_EVALUATION_QUERY),
            ("actor_id", passive_observation.get("actorId"), "chaser"),
            ("camera_id", passive_observation.get("cameraId"), CHASE_PASSIVE_CAMERA_ID),
        )
        mismatched = [name for name, actual, expected in expected_identity if actual != expected]
        preservation["unknown_fields"] = sorted({*preservation["unknown_fields"], *mismatched})
    else:
        reason_changed_fields = reason_record.get("changedFields")
        if isinstance(reason_changed_fields, list):
            preservation["changed_fields"] = sorted({
                *preservation["changed_fields"],
                *(PASSIVE_RECEIPT_FIELD_NAMES.get(str(item), str(item)) for item in reason_changed_fields),
            })
        preservation["preserved"] = False
    if preservation["unknown_fields"]:
        preservation["preserved"] = False
    return before, preservation, declared_field_set


def _validated_capture(
    capture: dict[str, object],
    *,
    before: dict[str, object],
    unknown_fields: list[str],
    include_image: bool,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """The validated sensor, the evaluator reference and its status; the receipt must match the sensor's identity."""

    sensor = validate_chase_sensor_capture(capture, expected_actor_id="chaser", include_validated_bytes=include_image)
    evaluator = evaluate_chase_evaluator_reference(capture, sensor=sensor)
    evaluator_status = {key: value for key, value in evaluator.items() if key != "reference" and value is not None}
    playback = _record(before.get("playback"))
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
        if receipt_identity_fields[key] not in unknown_fields
        if receipt_identity[key] != sensor_identity[key]
    ]
    if mismatched_identity:
        raise ChaseCaptureValidationError(
            code="capture_identity_invalid",
            path="passiveObservation.preservation.before." + mismatched_identity[0],
            message=f"preservation receipt does not match the captured sensor identity ({mismatched_identity})",
        )
    return sensor, evaluator, evaluator_status


def _receipt_status(
    *,
    supported: bool,
    unsupported_reason: str,
    changed: bool,
    preserved: bool,
) -> tuple[str, str | None, str | None]:
    """The receipt's status, code and reason."""

    if not supported and unsupported_reason == "session_changed":
        return "unavailable", "simulator_state_changed", "session_fingerprint_changed"
    if not supported:
        return "unsupported", "simulator_capability_missing", unsupported_reason or "passive_observation_unsupported"
    if changed:
        return "unavailable", "simulator_state_changed", "session_fingerprint_changed"
    if not preserved:
        return "unsupported", "simulator_capability_missing", "preservation_receipt_invalid"
    return "available", None, None


def _receipt_environment(before: dict[str, object], fallback_environment: dict[str, object]) -> dict[str, object]:
    """The session environment from the receipt, with the caller's fallback for the fields it could not read."""

    environment = passive_receipt_environment(before)
    if not before["unknown_fields"]:
        return environment
    return {key: fallback_environment.get(key, environment.get(key)) for key in (
        "game_id", "scenario_id", "simulation_epoch", "playback", "control_source", "control_input",
    )}
