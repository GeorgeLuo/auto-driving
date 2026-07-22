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
    align_candidate_with_shadow,
    build_chase_shadow_reference,
    format_chase_frame_id,
    frame_indices_strictly_increasing,
    score_shadow_alignment_batch,
    simulator_epoch_from_snapshot,
    simulator_frame_index_from_snapshot,
)
from implementations.vehicle.chase_sim.metrics_ws import (
    MetricsUiCommandResponse,
    MetricsUiWebSocketError,
    MetricsUiWsClient,
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
            "get_play_debug",
            side_effect=AssertionError("capture must not read debug"),
        ), mock.patch.object(
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
            timeout_s=0.5,
        )
        self.assertEqual(car.last_simulator_frame_index, 123)
        self.assertEqual(simulator_frame_index_from_snapshot(snapshot), 123)
        self.assertEqual(simulator_epoch_from_snapshot(snapshot), "chase-run:test")
        reading = snapshot.readings[FRONT_CAMERA_SENSOR_ID]
        self.assertEqual(reading.metadata["identity_pairing"], "atomic_evaluation_capture")
        self.assertEqual(reading.metadata["simulation_epoch"], "chase-run:test")
        self.assertTrue(image_exists)
        self.assertNotIn("shadow_reference", snapshot.metadata)
        self.assertNotIn("evaluator", str(snapshot.to_dict()))

    def test_capture_fails_closed_on_malformed_atomic_response(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.3)
        malformed = _atomic_capture(frame_index=10, action_frame_index=11)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            car.client,
            "play_game_query",
            return_value=malformed,
        ):
            with self.assertRaisesRegex(ValueError, "invalid identity"):
                car.read_sensors(
                    SensorReadRequest(
                        output_dir=Path(tmp),
                        read_id="malformed",
                        image_extension="png",
                    )
                )
        self.assertIsNone(car.last_capture_shadow_reference)
        self.assertIsNone(car.last_simulator_frame_index)

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
