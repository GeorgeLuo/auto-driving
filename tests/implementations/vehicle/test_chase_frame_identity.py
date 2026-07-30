from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReadRequest
from implementations.vehicle.chase_sim.car import (
    CHASE_ATOMIC_EVALUATION_QUERY,
    ChaseSimCar,
)
from implementations.vehicle.chase_sim.frame_identity import (
    ChaseCaptureValidationError,
    align_candidate_with_shadow,
    build_chase_shadow_reference,
    evaluate_chase_evaluator_reference,
    format_chase_frame_id,
    frame_indices_strictly_increasing,
    score_shadow_alignment_batch,
    simulator_epoch_from_snapshot,
    simulator_frame_index_from_snapshot,
    validate_chase_sensor_capture,
)
from implementations.vehicle.chase_sim.metrics_ws import (
    MetricsUiCommandResponse,
    MetricsUiWebSocketError,
    MetricsUiWsClient,
    build_chase_session_fingerprint,
    compare_chase_session_fingerprints,
)


_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _atomic_capture(
    *,
    frame_index: int = 42,
    simulation_epoch: str = "chase-run:test",
    action_frame_index: int | None = None,
) -> dict:
    return {
        "contractVersion": 1,
        "captureId": f"chase:evaluation:{simulation_epoch}:chaser:{frame_index}",
        "actorId": "chaser",
        "frameIdentity": {
            "gameId": "chase",
            "simulationEpoch": simulation_epoch,
            "frameIndex": frame_index,
        },
        "playback": {"advanced": False},
        "sensor": {
            "image": {
                "contentType": "image/png",
                "rendererId": "chase-actor-view-threejs-v1",
                "width": 1,
                "height": 1,
                "dataUrl": _PNG_DATA_URL,
            }
        },
        "evaluator": {
            "classification": "non-sensor",
            "shadow": {
                "kind": "visible-observation-summary",
                "visibleWallCount": 99,
                "map": {"privileged": True},
            },
            "reference": {
                "kind": "actor-control-reference",
                "scenarioId": "chaser-depth-obstacles",
                "controlSource": "programmatic",
                "phase": "after-actions",
                "actionFrameIndex": (
                    frame_index if action_frame_index is None else action_frame_index
                ),
                "input": {
                    "source": "programmatic",
                    "forward": True,
                    "reverse": False,
                    "steering": 0.25,
                },
                "action": {
                    "source": "programmatic",
                    "forward": True,
                    "reverse": False,
                    "steering": 0.2,
                    "selectedActionProposalId": "proposal-1",
                },
            },
        },
    }


def _session_state(
    *,
    scenario: str = "chaser-depth-obstacles",
    control_source: str = "programmatic",
    playing: bool = True,
) -> dict:
    return {
        "gameId": "chase",
        "playback": {"isPlaying": playing, "playbackRate": 1},
        "playSidebarSections": [
            {
                "rows": [
                    {"id": "scenario-select", "value": scenario},
                    {"id": "chaser-control-source", "value": control_source},
                ]
            }
        ],
    }


def _session_debug(
    *,
    simulation_epoch: str = "chase-run:test",
    control_source: str = "programmatic",
) -> dict:
    return {
        "gameId": "chase",
        "simulationEpoch": simulation_epoch,
        "actions": {
            "chaserInput": {
                "source": control_source,
                "motion": "forward",
                "forward": True,
                "reverse": False,
                "steering": 0.25,
            }
        },
    }


class ChaseFrameIdentityTests(unittest.TestCase):
    def test_builds_bounded_shadow_reference_from_atomic_capture(self) -> None:
        self.assertEqual(format_chase_frame_id(42), "chase_frame_000042")
        shadow = build_chase_shadow_reference(_atomic_capture())
        assert shadow is not None

        self.assertEqual(shadow["schema"], "chase_shadow_reference_v1")
        self.assertTrue(shadow["evaluator_only"])
        self.assertEqual(shadow["simulator_frame_index"], 42)
        self.assertEqual(shadow["simulation_epoch"], "chase-run:test")
        self.assertEqual(shadow["chaser_control_source"], "programmatic")
        self.assertEqual(shadow["chaser_action"]["selectedActionProposalId"], "proposal-1")
        self.assertNotIn("shadow", shadow)
        self.assertNotIn("visibleWallCount", str(shadow))
        self.assertNotIn("map", str(shadow))

    def test_atomic_reference_rejects_invalid_or_future_identity(self) -> None:
        self.assertIsNone(
            build_chase_shadow_reference(
                _atomic_capture(frame_index=10, action_frame_index=11)
            )
        )
        missing_epoch = _atomic_capture()
        missing_epoch["frameIdentity"].pop("simulationEpoch")
        self.assertIsNone(build_chase_shadow_reference(missing_epoch))

        coerced_boolean = _atomic_capture()
        coerced_boolean["evaluator"]["reference"]["input"]["forward"] = 1
        self.assertIsNone(build_chase_shadow_reference(coerced_boolean))

    def test_sensor_capture_is_independent_from_optional_evaluator_reference(self) -> None:
        missing = _atomic_capture()
        missing["evaluator"].pop("reference")
        sensor = validate_chase_sensor_capture(missing)
        evaluator = evaluate_chase_evaluator_reference(missing, sensor=sensor)

        self.assertEqual(sensor["frame_id"], "chase_frame_000042")
        self.assertEqual(evaluator["status"], "unavailable")
        self.assertEqual(evaluator["reason"], "reference_missing")
        self.assertIsNone(evaluator["reference"])

        malformed = _atomic_capture(frame_index=10, action_frame_index=11)
        evaluator = evaluate_chase_evaluator_reference(
            malformed,
            sensor=validate_chase_sensor_capture(malformed),
        )
        self.assertEqual(evaluator["status"], "invalid")
        self.assertEqual(evaluator["path"], "evaluator.reference.actionFrameIndex")

    def test_required_capture_diagnostics_name_exact_first_path(self) -> None:
        missing_epoch = _atomic_capture()
        missing_epoch["frameIdentity"].pop("simulationEpoch")
        with self.assertRaises(ChaseCaptureValidationError) as raised:
            validate_chase_sensor_capture(missing_epoch)
        self.assertEqual(raised.exception.code, "capture_identity_invalid")
        self.assertEqual(raised.exception.path, "frameIdentity.simulationEpoch")

        invalid_image = _atomic_capture()
        invalid_image["sensor"]["image"]["dataUrl"] = "data:image/png;base64,not-base64"
        with self.assertRaises(ChaseCaptureValidationError) as raised:
            validate_chase_sensor_capture(invalid_image)
        self.assertEqual(raised.exception.code, "capture_image_invalid")
        self.assertEqual(raised.exception.path, "sensor.image.dataUrl")

    def test_alignment_requires_epoch_and_strictly_increasing_frames(self) -> None:
        shadow = build_chase_shadow_reference(_atomic_capture(frame_index=7))
        assert shadow is not None
        ok = align_candidate_with_shadow(
            candidate_frame_index=7,
            candidate_simulation_epoch="chase-run:test",
            shadow_reference=shadow,
        )
        self.assertTrue(ok["aligned"])
        wrong_epoch = align_candidate_with_shadow(
            candidate_frame_index=7,
            candidate_simulation_epoch="chase-run:other",
            shadow_reference=shadow,
        )
        self.assertFalse(wrong_epoch["aligned"])

        frames = []
        for index in (10, 11):
            reference = build_chase_shadow_reference(_atomic_capture(frame_index=index))
            frames.append(
                {
                    "frame_id": format_chase_frame_id(index),
                    "simulator_frame_index": index,
                    "simulation_epoch": "chase-run:test",
                    "shadow_reference": reference,
                }
            )
        score = score_shadow_alignment_batch(frames, min_frames=2)
        self.assertTrue(score["passed"], score)
        self.assertTrue(score["consistent_run_identity"])

        frames.reverse()
        reversed_score = score_shadow_alignment_batch(frames, min_frames=2)
        self.assertFalse(reversed_score["passed"])
        self.assertFalse(reversed_score["advancing_simulator_frames"])
        self.assertFalse(frame_indices_strictly_increasing([11, 10]))
        self.assertTrue(frame_indices_strictly_increasing([10, 11, 15]))

    def test_read_sensors_uses_one_atomic_query_and_keeps_shadow_outside_snapshot(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.5)
        capture = _atomic_capture(frame_index=123)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            car.client,
            "play_game_query",
            return_value=capture,
        ) as query, mock.patch.object(
            car.client,
            "get_state",
            side_effect=[_session_state(), _session_state()],
        ) as get_state, mock.patch.object(
            car.client,
            "get_play_debug",
            side_effect=[_session_debug(), _session_debug()],
        ) as get_debug, mock.patch.object(
            car.client,
            "get_play_front_view_snapshot",
            side_effect=AssertionError("capture must not use the sequential snapshot path"),
        ):
            snapshot = car.read_sensors(
                SensorReadRequest(
                    output_dir=Path(tmp),
                    read_id="atomic",
                    image_extension="png",
                    front_camera_endpoint=CHASE_ATOMIC_EVALUATION_QUERY,
                )
            )
            image_exists = Path(
                snapshot.readings[FRONT_CAMERA_SENSOR_ID].path or ""
            ).is_file()

        query.assert_called_once_with(
            CHASE_ATOMIC_EVALUATION_QUERY,
            {"actorId": "chaser", "width": 640, "height": 480},
            timeout_s=mock.ANY,
        )
        self.assertEqual(get_state.call_count, 2)
        self.assertEqual(get_debug.call_count, 2)
        self.assertEqual(car.last_simulator_frame_index, 123)
        self.assertEqual(simulator_frame_index_from_snapshot(snapshot), 123)
        self.assertEqual(simulator_epoch_from_snapshot(snapshot), "chase-run:test")
        reading = snapshot.readings[FRONT_CAMERA_SENSOR_ID]
        self.assertEqual(reading.metadata["identity_pairing"], "atomic_evaluation_capture")
        self.assertEqual(reading.metadata["simulation_epoch"], "chase-run:test")
        self.assertTrue(image_exists)
        self.assertNotIn("shadow_reference", snapshot.metadata)
        self.assertNotIn("visibleWallCount", str(snapshot.to_dict()))
        self.assertNotIn("actor-control-reference", str(snapshot.to_dict()))
        self.assertEqual(
            snapshot.readings[FRONT_CAMERA_SENSOR_ID].metadata["evaluator_reference"]["status"],
            "available",
        )

    def test_malformed_evaluator_reference_does_not_block_sensor_capture(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.3)
        malformed = _atomic_capture(frame_index=10, action_frame_index=11)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            car.client,
            "play_game_query",
            return_value=malformed,
        ), mock.patch.object(
            car.client,
            "get_state",
            side_effect=[_session_state(), _session_state()],
        ), mock.patch.object(
            car.client,
            "get_play_debug",
            side_effect=[_session_debug(), _session_debug()],
        ):
            snapshot = car.read_sensors(
                SensorReadRequest(
                    output_dir=Path(tmp),
                    read_id="malformed-reference",
                    image_extension="png",
                )
            )
        self.assertIsNone(car.last_capture_shadow_reference)
        self.assertEqual(car.last_simulator_frame_index, 10)
        self.assertEqual(car.last_evaluator_reference["status"], "invalid")
        self.assertEqual(
            car.last_evaluator_reference["path"],
            "evaluator.reference.actionFrameIndex",
        )
        self.assertIn(FRONT_CAMERA_SENSOR_ID, snapshot.readings)

    def test_missing_evaluator_reference_does_not_block_sensor_capture(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.3)
        capture = _atomic_capture()
        capture["evaluator"].pop("reference")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            car.client,
            "play_game_query",
            return_value=capture,
        ), mock.patch.object(
            car.client,
            "get_state",
            side_effect=[_session_state(), _session_state()],
        ), mock.patch.object(
            car.client,
            "get_play_debug",
            side_effect=[_session_debug(), _session_debug()],
        ):
            snapshot = car.read_sensors(
                SensorReadRequest(
                    output_dir=Path(tmp),
                    read_id="missing-reference",
                    image_extension="png",
                )
            )

        self.assertIsNone(car.last_capture_shadow_reference)
        self.assertEqual(car.last_evaluator_reference["status"], "unavailable")
        self.assertEqual(
            snapshot.readings[FRONT_CAMERA_SENSOR_ID].metadata["evaluator_reference"],
            {
                "status": "unavailable",
                "reason": "reference_missing",
                "path": "evaluator.reference",
            },
        )

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

        unsupported = build_chase_session_fingerprint(state={}, debug={"gameId": "chase"})
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
            return _atomic_capture()

        with mock.patch.object(car.client, "get_state", side_effect=state), mock.patch.object(
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
        self.assertEqual(len(timeouts), 5)
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
                "get_state",
                "get_play_debug",
            ],
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
        self.assertIn("control_source", result["session_preservation"]["changed_fields"])

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
            result = client.play_game_query(CHASE_ATOMIC_EVALUATION_QUERY, {"actorId": "chaser"})
        self.assertEqual(result["contractVersion"], 1)
        command.assert_called_once()

        bad = MetricsUiCommandResponse(
            message={"payload": {"queryId": "wrong", "result": {}}}
        )
        with mock.patch.object(client, "command", return_value=bad), self.assertRaises(
            MetricsUiWebSocketError
        ):
            client.play_game_query(CHASE_ATOMIC_EVALUATION_QUERY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
