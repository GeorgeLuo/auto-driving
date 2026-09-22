from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from implementations.vehicle.chase_sim.car import ChaseSimCar
from implementations.vehicle.chase_sim.frame_identity import (
    ChaseCaptureValidationError,
    align_candidate_with_shadow,
    build_chase_shadow_reference,
    evaluate_chase_evaluator_reference,
    format_chase_frame_id,
    frame_indices_strictly_increasing,
    score_shadow_alignment_batch,
    validate_chase_sensor_capture,
)
from tests.implementations.vehicle.chase_frame_identity_fixtures import (
    ChaseFrameIdentityFixture,
    _PNG_DATA_URL,
    _atomic_capture,
)


class ChaseFrameIdentityTests(ChaseFrameIdentityFixture, unittest.TestCase):
    def test_sensor_image_envelope_rejects_dimensions_mime_and_content_type_mismatches(
        self,
    ) -> None:
        cases = (
            ("width", 2, "sensor.image"),
            ("height", 2, "sensor.image"),
            (
                "dataUrl",
                _PNG_DATA_URL.replace("image/png", "image/jpeg"),
                "sensor.image.dataUrl",
            ),
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

    def test_sensor_image_envelope_rejects_raster_format_and_unlisted_mime(
        self,
    ) -> None:
        capture = _atomic_capture()
        capture["sensor"]["image"]["dataUrl"] = self._raster_data_url(
            "JPEG", "image/png"
        )
        capture["sensor"]["image"]["width"] = 2
        capture["sensor"]["image"]["height"] = 3
        with self.assertRaises(ChaseCaptureValidationError) as raised:
            validate_chase_sensor_capture(capture)
        self.assertEqual(raised.exception.code, "capture_image_invalid")
        self.assertEqual(raised.exception.path, "sensor.image.dataUrl")

        capture = _atomic_capture()
        capture["sensor"]["image"]["dataUrl"] = self._raster_data_url(
            "BMP", "image/bmp"
        )
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
        self.assertEqual(
            shadow["chaser_action"]["selectedActionProposalId"], "proposal-1"
        )
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

    def test_sensor_capture_is_independent_from_optional_evaluator_reference(
        self,
    ) -> None:
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
                "evaluator_reference": {
                    "status": "unavailable",
                    "reason": "reference_missing",
                },
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
