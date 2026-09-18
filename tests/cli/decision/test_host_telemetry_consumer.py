"""Deterministic consumer checks for the PR #202 host telemetry seam."""

from __future__ import annotations

import copy
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cli.automa_cli.decision_live import (
    PhysicalDecisionViewAdapter,
    _accepted_pair,
    read_host_telemetry_panel,
)
from cli.automa_cli.decision import DecisionSurfaceError
from cli.automa_cli.decision import render_decision_exact_frame_html
from cli.automa_cli.physical_observation import (
    DECISION_PUBLICATION_SCHEMA,
    HOST_TELEMETRY_SCHEMA,
    build_host_telemetry_capture,
    join_host_telemetry_to_decision,
    normalize_host_telemetry_record,
    normalize_host_telemetry_records,
    normalize_physical_decision_publication,
    physical_decision_identity,
)


NOW_MS = 10_000


def _record(
    *,
    sequence: int = 1,
    frame_id: str = "frame-1",
    frame_index: int = 1,
    captured_at_ms: int = 8_000,
    completed_at_ms: int = 8_500,
    observed_at_ms: int = 9_000,
    published_at_ms: int = 9_500,
    gap_since_previous_ms: int | None = None,
    skipped_since_previous: int | None = None,
) -> dict:
    return {
        "schema": HOST_TELEMETRY_SCHEMA,
        "status": "healthy",
        "vehicle_id": "piracer",
        "source_id": "donkeycar:piracer",
        "run_id": "run-1",
        "generation_id": "shadow-proposals:1000",
        "activation": {
            "engine_id": "shadow-proposals",
            "activated_at_ms": 1_000,
            "generation_id": "shadow-proposals:1000",
        },
        "source_frame": {
            "frame_id": frame_id,
            "frame_index": frame_index,
            "captured_at_ms": captured_at_ms,
            "completed_at_ms": completed_at_ms,
        },
        "host_tick": {
            "sequence": sequence,
            "observed_at_ms": observed_at_ms,
            "published_at_ms": published_at_ms,
            "source_age_ms": observed_at_ms - completed_at_ms,
            "gap_since_previous_ms": gap_since_previous_ms,
            "skipped_since_previous": skipped_since_previous,
        },
        "mode": "user",
        "user_input": {"steering": 0.0, "throttle": 0.0},
        "pilot_output": {"steering": 0.25, "throttle": 0.0},
        "host_selected_output": {"steering": 0.0, "throttle": 0.0},
        "application": {
            "boundary": "post_drive_mode_pre_drivetrain",
            "actuator_feedback": "unavailable",
        },
        "limits": {
            "max_source_age_ms": 1_500,
            "max_publication_age_ms": 1_500,
            "max_gap_ms": 1_000,
            "future_skew_tolerance_ms": 250,
        },
    }


def _decision(*, source_frame: dict | None = None) -> dict:
    frame = source_frame or {
        "frame_id": "frame-1",
        "frame_index": 1,
        "captured_at_ms": 8_000,
        "completed_at_ms": 8_500,
    }


    return {
        "decision": {
            "vehicle_id": "piracer",
            "source_id": "donkeycar:piracer",
            "run_id": "run-1",
            "activation_engine_id": "shadow-proposals",
            "activation_activated_at_ms": 1_000,
            "generation_id": "shadow-proposals:1000",
            "frame_id": frame["frame_id"],
            "frame_index": frame["frame_index"],
            "timestamp_ms": frame["captured_at_ms"],
            "published_at_ms": 9_500,
            "activation": {
                "engine_id": "shadow-proposals",
                "activated_at_ms": 1_000,
                "generation_id": "shadow-proposals:1000",
            },
            "source_frame": copy.deepcopy(frame),
            "cycle": {"source": {"source_frame": copy.deepcopy(frame)}},
        }
    }


def _physical_publication() -> dict:
    decision = _decision()["decision"]
    decision["activation"]["engine_config"] = {}
    decision["cycle"] = {
        "schema": "shadow_decision_cycle_result_v0",
        "status": "ok",
        "frame_id": "frame-1",
        "source": {
            "frame_id": "frame-1",
            "frame_index": 1,
            "timestamp_ms": 8_000,
        },
    }
    return {
        "schema": DECISION_PUBLICATION_SCHEMA,
        "status": "ready",
        "ok": True,
        "reason": "",
        "read_at_ms": 9_500,
        "result_age_ms": 500,
        "stale_after_ms": 1_500,
        "decision": decision,
    }


class HostTelemetryConsumerTests(unittest.TestCase):
    def test_physical_decision_adapter_preserves_provider_identity(self) -> None:
        normalized = normalize_physical_decision_publication(
            _physical_publication(),
            vehicle_id="piracer",
            now_ms=NOW_MS,
        )

        self.assertEqual(normalized["frame_id"], "frame-1")
        self.assertEqual(normalized["result_age_ms"], 500)
        self.assertEqual(physical_decision_identity(normalized)["source_frame"]["frame_id"], "frame-1")

    def test_live_view_adapter_joins_against_physical_publication(self) -> None:
        normalized = normalize_physical_decision_publication(
            _physical_publication(),
            vehicle_id="piracer",
            now_ms=NOW_MS,
        )

        class FakeDecisionView:
            def __init__(self) -> None:
                self.kwargs = None

            def publish_provider_transaction(self, **kwargs):
                self.kwargs = kwargs
                return True

        class FakePerceptionView:
            def publish_frame(self, **_kwargs) -> None:
                return None

            def publish_perception(self, **_kwargs) -> None:
                return None

        class FakeRuntimeView:
            def __init__(self, automation_dir: Path) -> None:
                self.automation_dir = automation_dir
                self.decision = FakeDecisionView()
                self.perception = FakePerceptionView()

        joined = join_host_telemetry_to_decision(
            normalize_host_telemetry_record(_record(), now_ms=NOW_MS),
            _decision(),
        )
        with TemporaryDirectory() as temporary:
            view = FakeRuntimeView(Path(temporary))
            with patch(
                "cli.automa_cli.decision_live.read_host_telemetry_panel",
                return_value=joined,
            ) as read_panel:
                adapter = PhysicalDecisionViewAdapter(
                    vehicle_id="piracer",
                    base_url="http://piracer.local:8887",
                    view_server=view,
                    timeout_s=1.0,
                )
                self.assertTrue(adapter.publish_snapshot(normalized, (b"jpeg", "image/jpeg")))

        read_panel.assert_called_once()
        self.assertIs(read_panel.call_args.kwargs["normalized_decision"], normalized)
        self.assertEqual(view.decision.kwargs["frame_record"]["host_telemetry"], joined)

    def test_live_view_uses_matching_history_when_latest_frame_has_advanced(self) -> None:
        normalized = normalize_physical_decision_publication(
            _physical_publication(),
            vehicle_id="piracer",
            now_ms=NOW_MS,
        )
        latest = _record(
            sequence=2,
            frame_id="frame-2",
            frame_index=2,
            gap_since_previous_ms=50,
            skipped_since_previous=0,
        )
        matching = _record(sequence=1)
        with patch(
            "cli.automa_cli.decision_live.fetch_host_telemetry_latest",
            return_value=latest,
        ), patch(
            "cli.automa_cli.decision_live.fetch_host_telemetry_records",
            return_value={
                "schema": "automa_host_boundary_telemetry_records_v0",
                "status": "healthy",
                "records": [matching],
            },
        ) as records:
            panel = read_host_telemetry_panel(
                "http://piracer.local:8887",
                normalized_decision=normalized,
                vehicle_id="piracer",
                timeout_s=1.0,
                now_ms=NOW_MS,
            )

        self.assertTrue(panel["joined"])
        self.assertEqual(panel["source_frame"]["frame_id"], "frame-1")
        self.assertEqual(records.call_args.kwargs["after_sequence"], 0)

    def test_live_view_exposes_contiguous_history_coverage_separately(self) -> None:
        normalized = normalize_physical_decision_publication(
            _physical_publication(),
            vehicle_id="piracer",
            now_ms=NOW_MS,
        )
        second = _record(
            sequence=2,
            frame_id="frame-2",
            frame_index=2,
            captured_at_ms=8_800,
            completed_at_ms=9_000,
            observed_at_ms=9_500,
            published_at_ms=9_600,
            gap_since_previous_ms=500,
            skipped_since_previous=0,
        )
        with patch(
            "cli.automa_cli.decision_live.fetch_host_telemetry_latest",
            return_value=_record(),
        ), patch(
            "cli.automa_cli.decision_live.fetch_host_telemetry_records",
            return_value={
                "schema": "automa_host_boundary_telemetry_records_v0",
                "status": "healthy",
                "records": [_record(), second],
                "coverage": {"complete": True},
            },
        ) as records:
            panel = read_host_telemetry_panel(
                "http://piracer.local:8887",
                normalized_decision=normalized,
                vehicle_id="piracer",
                timeout_s=1.0,
                now_ms=NOW_MS,
            )

        self.assertTrue(panel["joined"])
        self.assertTrue(panel["coverage"]["interval_covered"])
        self.assertEqual(panel["coverage"]["first_sequence"], 1)
        self.assertEqual(panel["coverage"]["last_sequence"], 2)
        self.assertEqual(panel["coverage"]["sequence"], 1)
        self.assertEqual(panel["details"]["point_coverage"]["status"], "point")
        self.assertEqual(records.call_args.kwargs["after_sequence"], 0)

    def test_live_view_retries_history_when_latest_point_has_not_arrived(self) -> None:
        normalized = normalize_physical_decision_publication(
            _physical_publication(),
            vehicle_id="piracer",
            now_ms=NOW_MS,
        )
        with patch(
            "cli.automa_cli.decision_live.fetch_host_telemetry_latest",
            return_value=_record(),
        ), patch(
            "cli.automa_cli.decision_live.fetch_host_telemetry_records",
            side_effect=[
                {
                    "schema": "automa_host_boundary_telemetry_records_v0",
                    "status": "healthy",
                    "records": [],
                    "coverage": {"complete": True},
                },
                {
                    "schema": "automa_host_boundary_telemetry_records_v0",
                    "status": "healthy",
                    "records": [_record()],
                    "coverage": {"complete": True},
                },
            ],
        ) as records:
            panel = read_host_telemetry_panel(
                "http://piracer.local:8887",
                normalized_decision=normalized,
                vehicle_id="piracer",
                timeout_s=1.0,
                now_ms=NOW_MS,
            )

        self.assertTrue(panel["joined"])
        self.assertTrue(panel["coverage"]["interval_covered"])
        self.assertEqual(records.call_count, 2)

    def test_live_view_retries_a_small_provider_clock_skew(self) -> None:
        future_error = DecisionSurfaceError(
            "physical_decision_unavailable",
            "future",
            details={"reason": "future_dated"},
        )
        normalized = {"frame_id": "frame-1"}
        with patch(
            "cli.automa_cli.decision_live.fetch_observation_frame",
            return_value=(b"jpeg", {"x-frame-id": "frame-1", "content-type": "image/jpeg"}),
        ), patch(
            "cli.automa_cli.decision_live.fetch_decision_publication",
            side_effect=[{}, {}],
        ), patch(
            "cli.automa_cli.decision_live.accept_physical_decision_publication",
            side_effect=[future_error, normalized],
        ), patch(
            "cli.automa_cli.decision_live.time.time",
            return_value=10.0,
        ), patch(
            "cli.automa_cli.decision_live.time.sleep",
        ) as sleep:
            normalized, _ = _accepted_pair(
                "http://piracer.local:8887",
                vehicle_id="piracer",
                timeout_s=1.0,
            )

        self.assertEqual(normalized["frame_id"], "frame-1")
        sleep.assert_called_once_with(0.04)

    def test_normalizes_point_with_exact_identity_and_freshness(self) -> None:
        normalized = normalize_host_telemetry_record(
            _record(), now_ms=NOW_MS, vehicle_id="piracer"
        )

        self.assertEqual(normalized["status"], "healthy")
        self.assertEqual(normalized["freshness"]["source_age_ms"], 500)
        self.assertEqual(normalized["freshness"]["publication_age_ms"], 500)
        self.assertEqual(normalized["coverage"]["status"], "point")
        self.assertFalse(normalized["coverage"]["interval_covered"])
        self.assertEqual(
            normalized["identity"]["source_frame"],
            {
                "frame_id": "frame-1",
                "frame_index": 1,
                "captured_at_ms": 8_000,
                "completed_at_ms": 8_500,
            },
        )
        self.assertEqual(normalized["host_selected_output"], {"steering": 0.0, "throttle": 0.0})

    def test_join_requires_complete_composite_identity_and_keeps_authority_separate(self) -> None:
        point = normalize_host_telemetry_record(_record(), now_ms=NOW_MS)
        joined = join_host_telemetry_to_decision(point, _decision())

        self.assertTrue(joined["joined"])
        self.assertEqual(joined["status"], "healthy")
        self.assertEqual(joined["identity"], physical_decision_identity(_decision()))
        self.assertNotIn("authority", joined)
        self.assertNotIn("host_application", joined)

        mismatched = copy.deepcopy(_record())
        mismatched["source_frame"]["captured_at_ms"] += 1
        mismatched_point = normalize_host_telemetry_record(mismatched, now_ms=NOW_MS)
        with self.assertRaisesRegex(ValueError, "exactly match") as raised:
            join_host_telemetry_to_decision(mismatched_point, _decision())
        self.assertEqual(raised.exception.reason, "identity_mismatch")

    def test_skipped_or_unavailable_point_fails_closed_before_join(self) -> None:
        skipped = _record(
            sequence=2,
            gap_since_previous_ms=100,
            skipped_since_previous=1,
        )
        skipped_point = normalize_host_telemetry_record(
            skipped,
            now_ms=NOW_MS,
            vehicle_id="piracer",
        )
        self.assertEqual(skipped_point["status"], "limited")
        self.assertEqual(skipped_point["reason"], "sequence_gap")
        with self.assertRaises(ValueError) as skipped_error:
            join_host_telemetry_to_decision(skipped_point, _decision())
        self.assertEqual(skipped_error.exception.reason, "sequence_gap")

        unavailable = copy.deepcopy(normalize_host_telemetry_record(
            _record(),
            now_ms=NOW_MS,
            vehicle_id="piracer",
        ))
        unavailable["status"] = "unavailable"
        unavailable["reason"] = "observer_error"
        with self.assertRaises(ValueError) as unavailable_error:
            join_host_telemetry_to_decision(unavailable, _decision())
        self.assertEqual(unavailable_error.exception.reason, "observer_error")

    def test_freshness_and_future_skew_rules_are_deterministic(self) -> None:
        within_skew = _record(published_at_ms=10_100, observed_at_ms=9_000)
        normalized = normalize_host_telemetry_record(within_skew, now_ms=NOW_MS)
        self.assertEqual(normalized["freshness"]["future_skew_ms"], 100)
        self.assertEqual(normalized["freshness"]["publication_age_ms"], -100)

        future = _record(published_at_ms=10_251, observed_at_ms=9_000)
        with self.assertRaises(ValueError) as raised:
            normalize_host_telemetry_record(future, now_ms=NOW_MS)
        self.assertEqual(raised.exception.reason, "future_dated")

        source_stale = _record(
            captured_at_ms=6_500,
            completed_at_ms=7_000,
            observed_at_ms=9_000,
        )
        with self.assertRaises(ValueError) as raised:
            normalize_host_telemetry_record(source_stale, now_ms=NOW_MS)
        self.assertEqual(raised.exception.reason, "source_stale")

        publication_stale = _record(
            captured_at_ms=7_000,
            completed_at_ms=7_500,
            observed_at_ms=8_000,
            published_at_ms=8_000,
        )
        with self.assertRaises(ValueError) as raised:
            normalize_host_telemetry_record(publication_stale, now_ms=NOW_MS)
        self.assertEqual(raised.exception.reason, "publication_stale")

    def test_decision_timestamp_is_part_of_the_exact_source_identity(self) -> None:
        point = normalize_host_telemetry_record(_record(), now_ms=NOW_MS)
        decision = _decision()
        decision["decision"]["timestamp_ms"] += 1
        with self.assertRaises(ValueError) as raised:
            join_host_telemetry_to_decision(point, decision)
        self.assertEqual(raised.exception.reason, "identity_mismatch")

    def test_invalid_schema_numbers_and_mode_fail_closed(self) -> None:
        invalid_schema = _record()
        invalid_schema["schema"] = "wrong"
        with self.assertRaises(ValueError) as raised:
            normalize_host_telemetry_record(invalid_schema, now_ms=NOW_MS)
        self.assertEqual(raised.exception.reason, "schema_invalid")

        nonfinite = _record()
        nonfinite["pilot_output"]["steering"] = math.inf
        with self.assertRaises(ValueError) as raised:
            normalize_host_telemetry_record(nonfinite, now_ms=NOW_MS)
        self.assertEqual(raised.exception.reason, "field_invalid")

        invalid_mode = _record()
        invalid_mode["mode"] = "remote"
        with self.assertRaises(ValueError) as raised:
            normalize_host_telemetry_record(invalid_mode, now_ms=NOW_MS)
        self.assertEqual(raised.exception.reason, "field_invalid")

        bad_boolean = _record()
        bad_boolean["user_input"]["throttle"] = False
        with self.assertRaises(ValueError) as raised:
            normalize_host_telemetry_record(bad_boolean, now_ms=NOW_MS)
        self.assertEqual(raised.exception.reason, "field_invalid")

        invalid_sequence = _record()
        invalid_sequence["host_tick"]["sequence"] = 0
        with self.assertRaises(ValueError) as raised:
            normalize_host_telemetry_record(invalid_sequence, now_ms=NOW_MS)
        self.assertEqual(raised.exception.reason, "field_invalid")

    def test_records_route_distinguishes_contiguous_coverage_from_gap(self) -> None:
        second = _record(
            sequence=2,
            frame_id="frame-2",
            frame_index=2,
            captured_at_ms=8_800,
            completed_at_ms=9_000,
            observed_at_ms=9_500,
            published_at_ms=9_600,
            gap_since_previous_ms=500,
            skipped_since_previous=0,
        )
        payload = {
            "schema": "automa_host_boundary_telemetry_records_v0",
            "status": "healthy",
            "records": [_record(), second],
            "coverage": {"complete": True, "baseline": True},
        }
        contiguous = normalize_host_telemetry_records(
            payload, now_ms=NOW_MS, after_sequence=0, limit=128, vehicle_id="piracer"
        )
        self.assertEqual(contiguous["status"], "healthy")
        self.assertTrue(contiguous["coverage"]["interval_covered"])
        self.assertEqual(contiguous["coverage"]["last_sequence"], 2)

        gap = copy.deepcopy(second)
        gap["host_tick"]["sequence"] = 3
        gap["host_tick"]["skipped_since_previous"] = 1
        gap_payload = copy.deepcopy(payload)
        gap_payload["records"] = [_record(), gap]
        limited = normalize_host_telemetry_records(
            gap_payload, now_ms=NOW_MS, after_sequence=0, limit=128, vehicle_id="piracer"
        )
        self.assertEqual(limited["status"], "limited")
        self.assertEqual(limited["reason"], "sequence_gap")
        self.assertFalse(limited["coverage"]["interval_covered"])

        evicted = copy.deepcopy(payload)
        evicted["coverage"] = {"complete": False, "reason": "history_evicted", "baseline": True}
        evicted_result = normalize_host_telemetry_records(
            evicted, now_ms=NOW_MS, after_sequence=0, limit=128, vehicle_id="piracer"
        )
        self.assertEqual(evicted_result["reason"], "coverage_gap")
        self.assertEqual(evicted_result["coverage"]["coverage_reason"], "history_evicted")

    def test_capture_and_render_keep_telemetry_out_of_authority(self) -> None:
        point = normalize_host_telemetry_record(_record(), now_ms=NOW_MS)
        joined = join_host_telemetry_to_decision(point, _decision())
        records = normalize_host_telemetry_records(
            {
                "schema": "automa_host_boundary_telemetry_records_v0",
                "status": "healthy",
                "records": [_record()],
                "coverage": {"complete": True, "baseline": True},
            },
            now_ms=NOW_MS,
            vehicle_id="piracer",
        )
        capture = build_host_telemetry_capture(
            joined_point=joined,
            records_result=records,
            vehicle_id="piracer",
        )
        self.assertEqual(capture["schema"], "automa_host_boundary_telemetry_capture_v0")
        self.assertIn("host_telemetry", capture)
        self.assertNotIn("authority", capture)
        self.assertNotIn("host_application", capture["host_telemetry"])

        cycle = {"frame_id": "frame-1", "status": "ok", "source": {}, "plan": None, "authority": {}}
        existing_html = render_decision_exact_frame_html(
            vehicle_id="piracer",
            frame_id="frame-1",
            cycle_result=cycle,
            source_image_rel=None,
        )
        telemetry_html = render_decision_exact_frame_html(
            vehicle_id="piracer",
            frame_id="frame-1",
            cycle_result=cycle,
            source_image_rel=None,
            host_telemetry=joined,
        )
        self.assertNotIn('id="host_telemetry"', existing_html)
        self.assertIn('id="host_telemetry"', telemetry_html)
        self.assertIn("Host telemetry", telemetry_html)
        self.assertIn("host_application", telemetry_html)


if __name__ == "__main__":
    unittest.main()
