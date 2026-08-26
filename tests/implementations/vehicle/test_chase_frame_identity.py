from __future__ import annotations

import tempfile
import unittest
import base64
from io import BytesIO
from pathlib import Path
from unittest import mock

from PIL import Image

from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReadRequest
from implementations.vehicle.chase_sim.car import (
    CHASE_ATOMIC_EVALUATION_QUERY,
    CHASE_PASSIVE_CAMERA_ID,
    ChasePassiveCaptureError,
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


def _with_passive_receipt(
    capture: dict,
    *,
    control_input: dict | None = None,
) -> dict:
    identity = capture["frameIdentity"]
    fingerprint = {
        "gameId": identity["gameId"],
        "scenarioId": "chaser-depth-obstacles",
        "simulationEpoch": identity["simulationEpoch"],
        "playback": {
            "frameIndex": identity["frameIndex"],
            "phase": "running",
            "pendingAction": False,
        },
        "controlSource": "programmatic",
        "controlInput": control_input,
        "actorId": capture["actorId"],
        "cameraId": CHASE_PASSIVE_CAMERA_ID,
    }
    capture["passiveObservation"] = {
        "supported": True,
        "queryId": CHASE_ATOMIC_EVALUATION_QUERY,
        "actorId": capture["actorId"],
        "cameraId": CHASE_PASSIVE_CAMERA_ID,
        "preservedFields": [
            "gameId",
            "scenarioId",
            "simulationEpoch",
            "playback",
            "controlSource",
            "controlInput",
            "actorId",
            "cameraId",
        ],
        "preservation": {
            "preserved": True,
            "before": fingerprint,
            "after": dict(fingerprint),
        },
    }
    return capture


class ChaseFrameIdentityTests(unittest.TestCase):
    def _raster_data_url(self, image_format: str, mime: str) -> str:
        output = BytesIO()
        Image.new("RGB", (2, 3), (20, 40, 60)).save(output, format=image_format)
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def test_sensor_image_envelope_rejects_dimensions_mime_and_content_type_mismatches(self) -> None:
        cases = (
            ("width", 2, "sensor.image"),
            ("height", 2, "sensor.image"),
            ("dataUrl", _PNG_DATA_URL.replace("image/png", "image/jpeg"), "sensor.image.dataUrl"),
            ("contentType", "image/jpeg", "sensor.image.contentType"),
            ("contentType", None, "sensor.image.contentType"),
            ("contentType", "image/png; charset=utf-8", "sensor.image.contentType"),
        )
        for field, value, path in cases:
            with self.subTest(field=field, value=value):
                capture = _atomic_capture()
                capture["sensor"]["image"][field] = value
                with self.assertRaises(ChaseCaptureValidationError) as raised:
                    validate_chase_sensor_capture(capture)
                self.assertEqual(raised.exception.code, "capture_image_invalid")
                self.assertEqual(raised.exception.path, path)

    def test_sensor_image_envelope_rejects_raster_format_and_unlisted_mime(self) -> None:
        capture = _atomic_capture()
        capture["sensor"]["image"]["dataUrl"] = self._raster_data_url("JPEG", "image/png")
        capture["sensor"]["image"]["width"] = 2
        capture["sensor"]["image"]["height"] = 3
        with self.assertRaises(ChaseCaptureValidationError) as raised:
            validate_chase_sensor_capture(capture)
        self.assertEqual(raised.exception.code, "capture_image_invalid")
        self.assertEqual(raised.exception.path, "sensor.image.dataUrl")

        capture = _atomic_capture()
        capture["sensor"]["image"]["dataUrl"] = self._raster_data_url("BMP", "image/bmp")
        with self.assertRaises(ChaseCaptureValidationError) as raised:
            validate_chase_sensor_capture(capture)
        self.assertEqual(raised.exception.code, "capture_image_invalid")
        self.assertEqual(raised.exception.path, "sensor.image.dataUrl")

    def test_sensor_image_envelope_accepts_case_and_omitted_content_type(self) -> None:
        capture = _atomic_capture()
        capture["sensor"]["image"].pop("contentType")
        capture["sensor"]["image"]["dataUrl"] = _PNG_DATA_URL.replace(
            "data:image/png;", "data:IMAGE/PNG;charset=utf-8;"
        )
        sensor = validate_chase_sensor_capture(capture, include_validated_bytes=True)
        self.assertEqual(sensor["image"]["content_type"], "image/png")
        self.assertEqual(sensor["image"]["raster_format"], "PNG")
        self.assertIsInstance(sensor["image"]["validated_bytes"], bytes)

    def test_sensor_image_envelope_accepts_all_supported_raster_mappings(self) -> None:
        for image_format, mime in (
            ("PNG", "image/png"),
            ("JPEG", "image/jpeg"),
            ("GIF", "image/gif"),
            ("WEBP", "image/webp"),
        ):
            with self.subTest(image_format=image_format):
                capture = _atomic_capture()
                capture["sensor"]["image"]["width"] = 2
                capture["sensor"]["image"]["height"] = 3
                capture["sensor"]["image"]["dataUrl"] = self._raster_data_url(
                    image_format,
                    mime.upper(),
                )
                capture["sensor"]["image"]["contentType"] = mime.upper()
                sensor = validate_chase_sensor_capture(
                    capture,
                    include_validated_bytes=True,
                )
                self.assertEqual(sensor["image"]["content_type"], mime)
                self.assertEqual(sensor["image"]["raster_format"], image_format)
                self.assertIsInstance(sensor["image"]["validated_bytes"], bytes)

    def test_sensor_image_envelope_accepts_empty_content_type_as_omitted(self) -> None:
        capture = _atomic_capture()
        capture["sensor"]["image"]["contentType"] = "   "
        sensor = validate_chase_sensor_capture(capture)
        self.assertEqual(sensor["image"]["content_type"], "image/png")

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

    def test_protocol_frame_indexes_are_type_strict_on_the_wire(self) -> None:
        """Wire fields reject string/bool coercion; JSON integers remain valid."""

        # Valid JSON integer still accepted.
        sensor = validate_chase_sensor_capture(_atomic_capture(frame_index=42))
        self.assertEqual(sensor["simulator_frame_index"], 42)
        evaluator = evaluate_chase_evaluator_reference(
            _atomic_capture(frame_index=42, action_frame_index=42),
            sensor=sensor,
        )
        self.assertEqual(evaluator["status"], "available")
        self.assertEqual(evaluator["reference"]["action_frame_index"], 42)

        for bad_value in ("42", True, 42.0, -1, None):
            with self.subTest(field="frameIdentity.frameIndex", bad_value=bad_value):
                capture = _atomic_capture()
                capture["frameIdentity"]["frameIndex"] = bad_value
                with self.assertRaises(ChaseCaptureValidationError) as raised:
                    validate_chase_sensor_capture(capture)
                self.assertEqual(raised.exception.code, "capture_identity_invalid")
                self.assertEqual(raised.exception.path, "frameIdentity.frameIndex")

            with self.subTest(
                field="evaluator.reference.actionFrameIndex",
                bad_value=bad_value,
            ):
                capture = _atomic_capture(frame_index=42, action_frame_index=42)
                capture["evaluator"]["reference"]["actionFrameIndex"] = bad_value
                result = evaluate_chase_evaluator_reference(
                    capture,
                    sensor=validate_chase_sensor_capture(capture),
                )
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(
                    result["path"],
                    "evaluator.reference.actionFrameIndex",
                )
                self.assertIsNone(result["reference"])

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

        # Valid base64 that is not image bytes must fail at the sensor boundary.
        non_image = _atomic_capture()
        non_image["sensor"]["image"]["dataUrl"] = "data:image/png;base64,aGVsbG8="
        with self.assertRaises(ChaseCaptureValidationError) as raised:
            validate_chase_sensor_capture(non_image)
        self.assertEqual(raised.exception.code, "capture_image_invalid")
        self.assertEqual(raised.exception.path, "sensor.image.dataUrl")
        self.assertIn("not a valid image", raised.exception.detail)

        # SVG-only (valid or garbage) is not consumable by the worker .png path.
        valid_svg = _atomic_capture()
        valid_svg["sensor"]["image"].pop("dataUrl", None)
        valid_svg["sensor"]["image"]["svg"] = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">'
            '<rect width="1" height="1"/></svg>'
        )
        with self.assertRaises(ChaseCaptureValidationError) as raised:
            validate_chase_sensor_capture(valid_svg)
        self.assertEqual(raised.exception.code, "capture_image_invalid")
        self.assertEqual(raised.exception.path, "sensor.image.svg")
        self.assertIn("SVG-only", raised.exception.detail)

        malformed_svg = _atomic_capture()
        malformed_svg["sensor"]["image"].pop("dataUrl", None)
        malformed_svg["sensor"]["image"]["svg"] = "not-actually-svg"
        with self.assertRaises(ChaseCaptureValidationError) as raised:
            validate_chase_sensor_capture(malformed_svg)
        self.assertEqual(raised.exception.code, "capture_image_invalid")
        self.assertEqual(raised.exception.path, "sensor.image.svg")

    def test_png_worker_path_rejects_svg_only_capture_payload(self) -> None:
        """Worker write path for .png must raise capture_image_invalid, not ValueError."""

        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.5)
        svg_only = {
            "dataUrl": None,
            "svg": (
                '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">'
                '<rect width="1" height="1"/></svg>'
            ),
        }
        with mock.patch.object(
            car,
            "inspect_passive_capture",
            return_value={
                "status": "available",
                "sensor": {
                    "capture_id": "cap-1",
                    "simulation_epoch": "chase-run:test",
                    "simulator_frame_index": 7,
                    "image": {"width": 1, "height": 1},
                },
                "image": svg_only,
                "evaluator_reference": {"status": "unavailable", "reason": "reference_missing"},
                "session_preservation": {"preserved": True},
            },
        ), tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.png"
            with self.assertRaises(ChaseCaptureValidationError) as raised:
                car._capture_front_camera(path, endpoint="atomic-evaluation-capture")
        self.assertEqual(raised.exception.code, "capture_image_invalid")
        self.assertEqual(raised.exception.path, "sensor.image.svg")
        self.assertIn(".png", raised.exception.detail)

    def test_invalid_capture_is_rejected_before_output_write(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.5)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "frame.png"
            with mock.patch.object(
                car,
                "inspect_passive_capture",
                side_effect=ChaseCaptureValidationError(
                    code="capture_image_invalid",
                    path="sensor.image",
                    message="decoded dimensions do not match declaration",
                ),
            ), self.assertRaises(ChaseCaptureValidationError):
                car._capture_front_camera(path, endpoint="atomic-evaluation-capture")
            self.assertFalse(path.exists())
            self.assertFalse(path.parent.exists())

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
            image_path = Path(snapshot.readings[FRONT_CAMERA_SENSOR_ID].path or "")
            image_bytes = image_path.read_bytes()

        query.assert_called_once_with(
            CHASE_ATOMIC_EVALUATION_QUERY,
            {
                "actorId": "chaser",
                "cameraId": CHASE_PASSIVE_CAMERA_ID,
                "width": 640,
                "height": 480,
            },
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
        self.assertEqual(
            image_bytes,
            base64.b64decode(_PNG_DATA_URL.split(",", 1)[1]),
        )
        self.assertEqual(
            snapshot.readings[FRONT_CAMERA_SENSOR_ID].metadata["content_type"],
            "image/png",
        )
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
            return _with_passive_receipt(_atomic_capture())

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
                ), self.assertRaises(ChasePassiveCaptureError) as raised:
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
