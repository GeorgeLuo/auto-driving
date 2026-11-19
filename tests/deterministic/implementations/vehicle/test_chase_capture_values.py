"""Capture values retain validated identity, pixels and protocol serialization."""
from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from unittest import mock

import numpy as np

from implementations.vehicle.chase_sim.frame_identity import (
    ChaseCaptureValidationError,
    ChaseReferenceFrame,
    evaluate_chase_evaluator_reference,
    score_reference_alignment_batch,
    validate_chase_sensor_capture,
)
from implementations.vehicle.chase_sim.session import (
    build_chase_session_fingerprint,
    normalize_passive_receipt_fingerprint,
)
from tests.deterministic.implementations.vehicle.chase_frame_identity_fixtures import (
    _atomic_capture, _frame, _session_state,
)


class ChaseCaptureValueTests(unittest.TestCase):
    def test_consumption_retains_validated_identity_and_image_after_the_wire_object_changes(self):
        wire = _atomic_capture()
        sensor = validate_chase_sensor_capture(wire)
        wire["frameIdentity"]["frameIndex"] = 99
        wire["sensor"]["image"]["dataUrl"] = "invalid"
        self.assertEqual(sensor.identity.simulator_frame_index, 42)
        self.assertTrue(sensor.image.validated_bytes.startswith(b"\x89PNG"))
        np.testing.assert_array_equal(sensor.image.image_array(), [[[255, 0, 0]]])
        with self.assertRaises(FrozenInstanceError):
            sensor.identity.simulator_frame_index = 99
        with self.assertRaises(FrozenInstanceError):
            sensor.image.width = 99

    def test_evaluator_and_alignment_use_validated_identity_without_revalidating_image(self):
        wire = _atomic_capture()
        sensor = validate_chase_sensor_capture(wire)
        wire["sensor"] = None
        with mock.patch("implementations.vehicle.chase_sim.frame_identity.validate_chase_sensor_capture",
                        side_effect=AssertionError("capture was parsed twice")):
            evaluator = evaluate_chase_evaluator_reference(wire, sensor=sensor)
            score = score_reference_alignment_batch([
                ChaseReferenceFrame(sensor, evaluator.reference),
            ], min_frames=1)
        self.assertEqual(evaluator.status, "available")
        self.assertEqual(score.aligned_count, 1)
        self.assertEqual(score.mismatched, 0)

    def test_deferred_pixel_failure_keeps_the_capture_diagnostic(self):
        wire = _atomic_capture()
        # A complete JPEG header with a truncated pixel payload can enter the
        # waiting slot, but must fail when selected for decoding.
        wire["sensor"] = _frame()["sensor"]
        sensor = validate_chase_sensor_capture(wire, validate_pixels=False)
        import base64
        wire["sensor"]["image"]["dataUrl"] = (
            "data:image/jpeg;base64," + base64.b64encode(sensor.image.validated_bytes[:-2]).decode("ascii")
        )
        deferred = validate_chase_sensor_capture(wire, validate_pixels=False)
        with self.assertRaises(ChaseCaptureValidationError) as eager:
            validate_chase_sensor_capture(wire)
        with self.assertRaises(ChaseCaptureValidationError) as selected:
            deferred.image.image_array()
        self.assertEqual(selected.exception.to_dict(), eager.exception.to_dict())

    def test_sensor_and_evaluator_json_keep_the_complete_bounded_payload(self):
        wire = _atomic_capture()
        sensor = validate_chase_sensor_capture(wire)
        self.assertEqual(sensor.to_dict(), {
            "schema": "chase_sensor_capture", "capture_id": "chase:evaluation:chase-run:test:chaser:42",
            "actor_id": "chaser", "game_id": "chase", "simulation_epoch": "chase-run:test",
            "simulator_frame_index": 42, "frame_id": "chase_frame_000042", "playback_advanced": False,
            "image": {"encoding": "data_url", "width": 1, "height": 1,
                      "content_type": "image/png", "raster_format": "PNG"},
        })
        evaluator = evaluate_chase_evaluator_reference(wire, sensor=sensor)
        self.assertEqual(evaluator.to_dict(), {
            "status": "available", "reason": None, "path": None,
            "reference": {
                "schema": "chaser_reference", "evaluator_only": True,
                "capture_id": "chase:evaluation:chase-run:test:chaser:42", "actor_id": "chaser",
                "game_id": "chase", "simulation_epoch": "chase-run:test", "simulator_frame_index": 42,
                "frame_id": "chase_frame_000042", "scenario": "chaser-depth-obstacles",
                "chaser_control_source": "programmatic", "phase": "after-actions", "action_frame_index": 42,
                "chaser_input": {"source": "programmatic", "forward": True, "reverse": False, "steering": .25},
                "chaser_action": {"source": "programmatic", "forward": True, "reverse": False,
                                  "steering": .2, "selectedActionProposalId": "proposal-1"},
            },
        })
        self.assertEqual(evaluator.status_dict(), {"status": "available"})
        wire.pop("evaluator")
        self.assertEqual(evaluate_chase_evaluator_reference(wire, sensor=sensor).to_dict(), {
            "status": "unavailable", "reason": "reference_missing", "path": "evaluator.reference", "reference": None,
        })
        wire["evaluator"] = []
        self.assertEqual(evaluate_chase_evaluator_reference(wire, sensor=sensor).to_dict(), {
            "status": "invalid", "reason": "reference_invalid", "path": "evaluator",
            "message": "expected an object", "reference": None,
        })

    def test_shared_controls_preserve_session_and_evaluator_acceptance_rules(self):
        for steering in (True, "0", float("nan"), float("inf"), -2, -1, 0, 1, 2):
            with self.subTest(steering=steering):
                control = {"source": " programmatic ", "forward": True, "reverse": False, "steering": steering}
                session = build_chase_session_fingerprint(
                    state=_session_state(), debug={"actions": {"chaserInput": control}},
                )
                receipt = normalize_passive_receipt_fingerprint({"controlInput": control})
                finite = type(steering) is int
                self.assertEqual(session["control_input"] is not None, finite)
                self.assertEqual("control_input" not in receipt["unknown_fields"], finite)
                # Receipts retain the original wire value, including whitespace.
                self.assertIs(receipt["control_input"], control)
                wire = _atomic_capture()
                wire["evaluator"]["reference"]["input"] = control
                evaluator = evaluate_chase_evaluator_reference(wire, sensor=validate_chase_sensor_capture(wire))
                self.assertEqual(evaluator.status == "available", finite and -1 <= steering <= 1)

        motion_only = {"source": " keyboard ", "motion": " reverse ", "forward": "invalid", "steering": 2}
        session = build_chase_session_fingerprint(
            state=_session_state(), debug={"actions": {"chaserInput": motion_only}},
        )
        self.assertEqual(session["control_input"], {"source": "keyboard", "motion": "reverse", "steering": 2.0})
        receipt = normalize_passive_receipt_fingerprint({"controlInput": motion_only})
        self.assertIn("control_input", receipt["unknown_fields"])
