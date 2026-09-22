from __future__ import annotations
import unittest
from unittest import mock
from implementations.vehicle.chase_sim.car import (
    CHASE_ATOMIC_EVALUATION_QUERY,
    CHASE_PASSIVE_CAMERA_ID,
    ChasePassiveCaptureError,
    ChaseSimCar,
)
from implementations.vehicle.chase_sim.metrics_ws import (
    MetricsUiCommandResponse,
    MetricsUiWebSocketError,
    MetricsUiWsClient,
    build_chase_session_fingerprint,
    compare_chase_session_fingerprints,
)
from tests.implementations.vehicle.chase_frame_identity_fixtures import (
    ChaseFrameIdentityFixture,
    _atomic_capture,
    _session_debug,
    _session_state,
    _with_passive_receipt,
)


class ChaseFrameIdentityTests(ChaseFrameIdentityFixture, unittest.TestCase):
    def test_passive_capture_reports_unknown_or_changed_session_fields(self) -> None:
        before = build_chase_session_fingerprint(
            state=_session_state(),
            debug=_session_debug(),
        )
        self.assertEqual(before["unknown_fields"], [])
        after = build_chase_session_fingerprint(
            state=_session_state(control_source="keyboard"),
            debug=_session_debug(control_source="keyboard"),
        )
        comparison = compare_chase_session_fingerprints(before, after)
        self.assertFalse(comparison["preserved"])
        self.assertIn("control_source", comparison["changed_fields"])
        self.assertIn("control_input", comparison["changed_fields"])

        unsupported = build_chase_session_fingerprint(
            state={}, debug={"gameId": "chase"}
        )
        self.assertIn("scenario_id", unsupported["unknown_fields"])
        self.assertIn("control_source", unsupported["unknown_fields"])

    def test_passive_capture_uses_only_read_only_protocol_methods(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.5)
        calls: list[str] = []
        timeouts: list[float] = []

        def state(*, timeout_s: float) -> dict:
            calls.append("get_state")
            timeouts.append(timeout_s)
            return _session_state()

        def debug(*, timeout_s: float) -> dict:
            calls.append("get_play_debug")
            timeouts.append(timeout_s)
            return _session_debug()

        def query(query_id: str, payload: dict, *, timeout_s: float) -> dict:
            calls.append(f"play_game_query:{query_id}")
            timeouts.append(timeout_s)
            return _with_passive_receipt(_atomic_capture())

        with mock.patch.object(
            car.client, "get_state", side_effect=state
        ), mock.patch.object(
            car.client,
            "get_play_debug",
            side_effect=debug,
        ), mock.patch.object(
            car.client,
            "play_game_query",
            side_effect=query,
        ), mock.patch.object(
            car.client,
            "play_game_command",
            side_effect=AssertionError("passive capture must not mutate the game"),
        ):
            result = car.inspect_passive_capture()

        self.assertEqual(result["status"], "available")
        self.assertFalse(result["mutation_attempted"])
        self.assertEqual(len(timeouts), 3)
        self.assertTrue(
            all(
                timeouts[index] >= timeouts[index + 1]
                for index in range(len(timeouts) - 1)
            ),
            timeouts,
        )
        self.assertLessEqual(result["elapsed_ms"], 500)
        self.assertEqual(
            calls,
            [
                "get_state",
                "get_play_debug",
                f"play_game_query:{CHASE_ATOMIC_EVALUATION_QUERY}",
            ],
        )
        self.assertEqual(
            result["environment"]["simulation_epoch"],
            "chase-run:test",
        )
        self.assertIsNone(result["environment"]["control_input"])
        self.assertEqual(
            result["passive_observation"]["camera_id"],
            CHASE_PASSIVE_CAMERA_ID,
        )

    def test_passive_capture_rejects_invalid_declared_receipt(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.5)
        capture = _with_passive_receipt(_atomic_capture())
        capture["passiveObservation"]["preservation"]["after"][
            "simulationEpoch"
        ] = "chase-run:other"
        with mock.patch.object(
            car.client,
            "get_state",
            return_value=_session_state(),
        ), mock.patch.object(
            car.client,
            "get_play_debug",
            return_value=_session_debug(),
        ), mock.patch.object(
            car.client,
            "play_game_query",
            return_value=capture,
        ):
            result = car.inspect_passive_capture()

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["code"], "simulator_state_changed")
        self.assertIn(
            "simulation_epoch",
            result["session_preservation"]["changed_fields"],
        )

    def test_passive_capture_rejects_incomplete_nested_receipt_fields(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.5)
        capture = _with_passive_receipt(_atomic_capture())
        for side in ("before", "after"):
            fingerprint = capture["passiveObservation"]["preservation"][side]
            fingerprint["playback"] = {"frameIndex": 12}
            fingerprint["controlInput"] = {"source": "programmatic"}

        with mock.patch.object(
            car.client,
            "get_state",
            return_value=_session_state(),
        ), mock.patch.object(
            car.client,
            "get_play_debug",
            return_value=_session_debug(),
        ), mock.patch.object(
            car.client,
            "play_game_query",
            return_value=capture,
        ):
            result = car.inspect_passive_capture()

        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["code"], "simulator_capability_missing")
        self.assertIn("playback", result["session_preservation"]["unknown_fields"])
        self.assertIn(
            "control_input",
            result["session_preservation"]["unknown_fields"],
        )

    def test_structured_missing_frontend_error_is_not_server_unreachable(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.5)
        with mock.patch.object(
            car.client,
            "get_state",
            side_effect=MetricsUiWebSocketError(
                "Frontend not connected",
                code="frontend_not_connected",
                details={
                    "code": "frontend_not_connected",
                    "command": "get_state",
                },
            ),
        ), self.assertRaises(ChasePassiveCaptureError) as raised:
            car.inspect_passive_capture()

        self.assertEqual(raised.exception.code, "frontend_disconnected")
        self.assertEqual(
            raised.exception.details["protocol_error"]["command"],
            "get_state",
        )
        self.assertEqual(
            raised.exception.details["protocol_code"],
            "frontend_not_connected",
        )
        self.assertEqual(raised.exception.to_dict()["layer"], "simulator_frontend")

    def test_frontend_delivery_failures_during_sensor_capture_map_to_frontend_layer(
        self,
    ) -> None:
        """Absent, unresponsive, and mid-request disconnect stay on simulator_frontend."""

        for protocol_code in (
            "frontend_not_connected",
            "frontend_unresponsive",
            "frontend_disconnected",
        ):
            with self.subTest(protocol_code=protocol_code):
                car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.5)
                with mock.patch.object(
                    car.client,
                    "get_state",
                    return_value=_session_state(),
                ), mock.patch.object(
                    car.client,
                    "get_play_debug",
                    return_value=_session_debug(),
                ), mock.patch.object(
                    car.client,
                    "play_game_query",
                    side_effect=MetricsUiWebSocketError(
                        f"frontend delivery failed: {protocol_code}",
                        code=protocol_code,
                        details={"code": protocol_code, "command": "play_game_query"},
                    ),
                ), self.assertRaises(
                    ChasePassiveCaptureError
                ) as raised:
                    car.inspect_passive_capture()

                exc = raised.exception
                self.assertEqual(exc.code, "frontend_disconnected")
                self.assertEqual(exc.to_dict()["layer"], "simulator_frontend")
                self.assertEqual(exc.details["protocol_code"], protocol_code)
                self.assertEqual(
                    exc.details["protocol_error"]["code"],
                    protocol_code,
                )
                self.assertEqual(exc.details["incomplete_phase"], "sensor_capture")
                self.assertIn("reload", exc.details["minimum_external_change"].lower())
                self.assertNotIn(
                    "atomic-evaluation-capture",
                    exc.details["minimum_external_change"],
                )

    def test_passive_capture_fails_closed_when_session_changes(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.5)
        with mock.patch.object(
            car.client,
            "get_state",
            side_effect=[
                _session_state(control_source="programmatic"),
                _session_state(control_source="keyboard"),
            ],
        ), mock.patch.object(
            car.client,
            "get_play_debug",
            side_effect=[
                _session_debug(control_source="programmatic"),
                _session_debug(control_source="keyboard"),
            ],
        ), mock.patch.object(
            car.client,
            "play_game_query",
            return_value=_atomic_capture(),
        ):
            result = car.inspect_passive_capture()

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["code"], "simulator_state_changed")
        self.assertIn(
            "control_source", result["session_preservation"]["changed_fields"]
        )

    def test_play_game_query_validates_response_envelope(self) -> None:
        client = MetricsUiWsClient("ws://example.test/ws")
        response = MetricsUiCommandResponse(
            message={
                "type": "play_game_query_result",
                "payload": {
                    "queryId": CHASE_ATOMIC_EVALUATION_QUERY,
                    "result": _atomic_capture(),
                },
            }
        )
        with mock.patch.object(client, "command", return_value=response) as command:
            result = client.play_game_query(
                CHASE_ATOMIC_EVALUATION_QUERY, {"actorId": "chaser"}
            )
        self.assertEqual(result["contractVersion"], 1)
        command.assert_called_once()

        bad = MetricsUiCommandResponse(
            message={"payload": {"queryId": "wrong", "result": {}}}
        )
        with mock.patch.object(client, "command", return_value=bad), self.assertRaises(
            MetricsUiWebSocketError
        ):
            client.play_game_query(CHASE_ATOMIC_EVALUATION_QUERY)
