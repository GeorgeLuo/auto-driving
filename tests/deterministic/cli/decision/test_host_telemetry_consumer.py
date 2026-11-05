"""Deterministic checks for the latest-point host telemetry consumer."""

from __future__ import annotations

import copy
import math
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from autonomy.runtime.frame_loop import DECISION_PUBLICATION_SCHEMA
from autonomy.runtime.report import HostReport
from autonomy.runtime.routes import TELEMETRY_SCHEMA
import cli.automa_cli.hosts.publications.host_publications as host_publications
import cli.automa_cli.hosts.publications.host_telemetry as telemetry_api
import cli.automa_cli.hosts.views.decision_live as decision_live
from cli.automa_cli.hosts.views.decision_live import (
    DecisionViewAdapter,
    _accepted_pair,
    read_host_telemetry_panel,
)
from cli.automa_cli.hosts.publications.decision import DecisionSurfaceError
from cli.automa_cli.hosts.publications.decision_records import DecisionRunners
from cli.automa_cli.hosts.publications.decision_publication import normalize_decision_publication
from cli.automa_cli.hosts.publications.host_telemetry import (
    decision_publication_identity,
    join_host_telemetry_to_decision,
    normalize_host_telemetry_record,
)
from tests.shared.cli.decision.decision_surfaces_fixtures import (
    frame_inputs,
    left_obstruction_frame,
    packaged_decision_steps,
    packaged_identity,
    published_cycle,
    vehicle_report_for_records,
)


NOW_MS = 10_000
_PACKAGED_IDENTITY = packaged_identity("hold")
GENERATION_ID = _PACKAGED_IDENTITY["generation_id"]


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
        "schema": TELEMETRY_SCHEMA,
        "status": "healthy",
        "host_name": "piracer",
        "source_id": "donkeycar:piracer",
        "run_id": "run-1",
        "generation_id": GENERATION_ID,
        "activation": {"generation_id": GENERATION_ID},
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
    recorded = left_obstruction_frame()
    records = DecisionRunners.from_payloads(packaged_decision_steps("hold")).run(
        frame_id=frame["frame_id"],
        frame_index=frame["frame_index"],
        timestamp_ms=frame["captured_at_ms"],
        **frame_inputs(recorded),
    )
    observation, memory = published_cycle(recorded)
    report = vehicle_report_for_records(
        records,
        host_name="piracer",
        run_id="run-1",
        generation_id=GENERATION_ID,
        frame_id=frame["frame_id"],
        frame_index=frame["frame_index"],
        timestamp_ms=frame["captured_at_ms"],
        published_at_ms=9_500,
        observation=observation,
        memory=memory,
        values={
            "source_id": "donkeycar:piracer",
            "activation": {
                "generation_id": GENERATION_ID,
                "steps": _PACKAGED_IDENTITY["steps"],
            },
            "source_frame": {
                "frame_id": frame["frame_id"],
                "frame_index": frame["frame_index"],
                "captured_at_ms": frame["captured_at_ms"],
                "completed_at_ms": frame["completed_at_ms"],
            },
            "stale_after_ms": 1_500,
        },
    )
    return {"report": HostReport.from_dict(report)}


def _physical_publication() -> dict:
    decision = _decision()["report"].to_dict()
    return {
        "schema": DECISION_PUBLICATION_SCHEMA,
        "status": "ready",
        "ok": True,
        "reason": "",
        "read_at_ms": 9_500,
        "result_age_ms": 500,
        "stale_after_ms": decision["values"]["stale_after_ms"],
        "decision": decision,
    }


def _normalized_decision() -> dict:
    """The publication join and identity actually read: one HostReport."""

    return normalize_decision_publication(
        _physical_publication(),
        host_name="piracer",
        now_ms=NOW_MS,
    )


class HostTelemetryConsumerTests(unittest.TestCase):
    def test_physical_decision_adapter_preserves_provider_identity(self) -> None:
        normalized = normalize_decision_publication(
            _physical_publication(),
            host_name="piracer",
            now_ms=NOW_MS,
        )

        self.assertEqual(normalized["frame_id"], "frame-1")
        self.assertEqual(normalized["result_age_ms"], 500)
        self.assertEqual(
            decision_publication_identity(normalized)["source_frame"]["frame_id"],
            "frame-1",
        )

    def test_live_view_adapter_joins_against_physical_publication(self) -> None:
        normalized = normalize_decision_publication(
            _physical_publication(),
            host_name="piracer",
            now_ms=NOW_MS,
        )

        class FakeDecisionView:
            def __init__(self) -> None:
                self.kwargs = None
                self.provider = None

            def adopt(self, identity) -> None:
                self.provider = identity

            def publish(self, **kwargs):
                self.kwargs = kwargs
                return True

        class FakePerceptionView:
            def publish(self, **_kwargs) -> None:
                return None

        class FakeRuntimeView:
            def __init__(self, automation_dir: Path) -> None:
                self.automation_dir = automation_dir
                self.decision = FakeDecisionView()
                self.perception = FakePerceptionView()

        joined = join_host_telemetry_to_decision(
            normalize_host_telemetry_record(_record(), now_ms=NOW_MS),
            normalized,
        )
        with TemporaryDirectory() as temporary:
            view = FakeRuntimeView(Path(temporary))
            with patch(
                "cli.automa_cli.hosts.views.decision_live.read_host_telemetry_panel",
                return_value=joined,
            ) as read_panel:
                adapter = DecisionViewAdapter(
                    host_name="piracer",
                    base_url="http://piracer.local:8887",
                    view_server=view,
                    timeout_s=1.0,
                )
                self.assertTrue(
                    adapter.publish_frame(normalized, (b"jpeg", "image/jpeg"))
                )

        read_panel.assert_called_once()
        self.assertIs(read_panel.call_args.kwargs["normalized_decision"], normalized)
        self.assertEqual(view.decision.kwargs["host_telemetry"], joined)

    def _read_panel(self, payload, *, normalized):
        with patch(
            "cli.automa_cli.hosts.views.decision_live.fetch_host_telemetry_latest",
            return_value=payload,
        ) as fetched, patch(
            "cli.automa_cli.hosts.views.decision_live.time.sleep",
            side_effect=AssertionError("telemetry refresh slept"),
        ):
            panel = read_host_telemetry_panel(
                "http://piracer.local:8887",
                normalized_decision=normalized,
                host_name="piracer",
                timeout_s=1.0,
                now_ms=NOW_MS,
            )
        return panel, fetched

    def test_matching_latest_point_is_useful_after_one_read(self) -> None:
        normalized = normalize_decision_publication(
            _physical_publication(),
            host_name="piracer",
            now_ms=NOW_MS,
        )
        panel, fetched = self._read_panel(_record(), normalized=normalized)

        self.assertEqual(fetched.call_count, 1)
        self.assertTrue(panel["joined"])
        self.assertEqual(panel["status"], "healthy")
        self.assertEqual(panel["source_frame"]["frame_id"], "frame-1")
        self.assertEqual(panel["host_tick"]["observed_at_ms"], 9_000)
        self.assertEqual(panel["host_tick"]["published_at_ms"], 9_500)
        self.assertEqual(panel["freshness"]["source_age_ms"], 500)
        self.assertEqual(panel["freshness"]["publication_age_ms"], 500)
        self.assertEqual(panel["pilot_output"], {"steering": 0.25, "throttle": 0.0})
        self.assertEqual(
            panel["host_selected_output"], {"steering": 0.0, "throttle": 0.0}
        )
        self.assertEqual(panel["application"]["actuator_feedback"], "unavailable")
        self.assertNotIn("authority", panel)
        self.assertNotIn("coverage", panel)
        self.assertNotIn("interval_covered", str(panel))
        self.assertFalse(hasattr(decision_live, "fetch_host_telemetry_records"))
        self.assertFalse(hasattr(host_publications, "fetch_host_telemetry_records"))
        self.assertFalse(hasattr(telemetry_api, "normalize_host_telemetry_records"))

    def test_latest_point_mismatch_stays_explicit_without_history(self) -> None:
        normalized = normalize_decision_publication(
            _physical_publication(),
            host_name="piracer",
            now_ms=NOW_MS,
        )

        def mismatch_frame(record: dict) -> None:
            record["source_frame"]["frame_id"] = "frame-2"
            record["source_frame"]["frame_index"] = 2

        def mismatch_run(record: dict) -> None:
            record["run_id"] = "run-2"

        def mismatch_generation(record: dict) -> None:
            record["generation_id"] = "other-gen"
            record["activation"] = {"generation_id": "other-gen"}

        for name, mutate in (
            ("frame", mismatch_frame),
            ("run", mismatch_run),
            ("generation", mismatch_generation),
        ):
            with self.subTest(mismatch=name):
                latest = _record(
                    sequence=2,
                    gap_since_previous_ms=50,
                    skipped_since_previous=0,
                )
                latest["host_selected_output"] = {"steering": 0.4, "throttle": 0.2}
                mutate(latest)
                panel, fetched = self._read_panel(latest, normalized=normalized)

                self.assertEqual(fetched.call_count, 1)
                self.assertFalse(panel["joined"])
                self.assertEqual(panel["status"], "mismatched")
                self.assertEqual(panel["reason"], "identity_mismatch")
                self.assertIsNone(panel["host_selected_output"])
                self.assertIsNone(panel["pilot_output"])
                self.assertIsNone(panel["application"])
                self.assertNotIn("coverage", panel)
                self.assertNotIn("interval_covered", str(panel))

    def test_bad_latest_samples_stay_on_that_read(self) -> None:
        normalized = normalize_decision_publication(
            _physical_publication(),
            host_name="piracer",
            now_ms=NOW_MS,
        )
        malformed = _record()
        malformed["schema"] = "wrong"
        unavailable = _record()
        unavailable["status"] = "unavailable"
        unavailable["reason"] = "publisher_missing"
        reported_gap = _record(
            sequence=2,
            gap_since_previous_ms=100,
            skipped_since_previous=1,
        )
        reported_gap["status"] = "unavailable"
        reported_gap["reason"] = "sequence_gap"
        cases = (
            (
                "source_stale",
                _record(
                    captured_at_ms=6_500,
                    completed_at_ms=7_000,
                    observed_at_ms=9_000,
                ),
                "stale",
                "source_stale",
            ),
            (
                "publication_stale",
                _record(
                    captured_at_ms=7_000,
                    completed_at_ms=7_500,
                    observed_at_ms=8_000,
                    published_at_ms=8_000,
                ),
                "stale",
                "publication_stale",
            ),
            (
                "future_dated",
                _record(published_at_ms=10_251, observed_at_ms=9_000),
                "stale",
                "future_dated",
            ),
            ("malformed", malformed, "error", "schema_invalid"),
            ("unavailable", unavailable, "unavailable", "publisher_missing"),
            ("producer_sequence_gap", reported_gap, "limited", "sequence_gap"),
        )
        for name, payload, status, reason in cases:
            with self.subTest(sample=name):
                panel, fetched = self._read_panel(payload, normalized=normalized)
                self.assertEqual(fetched.call_count, 1)
                self.assertFalse(panel["joined"])
                self.assertEqual(panel["status"], status)
                self.assertEqual(panel["reason"], reason)
                self.assertIsNone(panel["host_selected_output"])
                self.assertIsNone(panel["pilot_output"])
                self.assertNotIn("interval_covered", str(panel))

        with patch(
            "cli.automa_cli.hosts.views.decision_live.fetch_host_telemetry_latest",
            side_effect=ConnectionError("down"),
        ) as fetched, patch(
            "cli.automa_cli.hosts.views.decision_live.time.sleep",
            side_effect=AssertionError("telemetry refresh slept"),
        ):
            panel = read_host_telemetry_panel(
                "http://piracer.local:8887",
                normalized_decision=normalized,
                host_name="piracer",
                timeout_s=1.0,
                now_ms=NOW_MS,
            )
        self.assertEqual(fetched.call_count, 1)
        self.assertFalse(panel["joined"])
        self.assertEqual(panel["status"], "unavailable")
        self.assertEqual(panel["reason"], "publisher_missing")
        self.assertIsNone(panel["record"])
        self.assertNotIn("interval_covered", str(panel))

    def test_a_later_failure_does_not_reuse_the_previous_success(self) -> None:
        normalized = normalize_decision_publication(
            _physical_publication(),
            host_name="piracer",
            now_ms=NOW_MS,
        )
        with patch(
            "cli.automa_cli.hosts.views.decision_live.fetch_host_telemetry_latest",
            side_effect=[_record(), ConnectionError("down")],
        ) as fetched:
            first = read_host_telemetry_panel(
                "http://piracer.local:8887",
                normalized_decision=normalized,
                host_name="piracer",
                timeout_s=1.0,
                now_ms=NOW_MS,
            )
            second = read_host_telemetry_panel(
                "http://piracer.local:8887",
                normalized_decision=normalized,
                host_name="piracer",
                timeout_s=1.0,
                now_ms=NOW_MS,
            )

        self.assertEqual(fetched.call_count, 2)
        self.assertTrue(first["joined"])
        self.assertEqual(
            first["host_selected_output"], {"steering": 0.0, "throttle": 0.0}
        )
        self.assertFalse(second["joined"])
        self.assertEqual(second["status"], "unavailable")
        self.assertEqual(second["reason"], "publisher_missing")
        self.assertIsNone(second["host_selected_output"])
        self.assertIsNone(second["record"])

    def test_telemetry_failure_still_publishes_the_decision_image(self) -> None:
        normalized = normalize_decision_publication(
            _physical_publication(),
            host_name="piracer",
            now_ms=NOW_MS,
        )

        class FakeDecisionView:
            def __init__(self) -> None:
                self.kwargs = None
                self.invalidated = False

            def adopt(self, identity) -> None:
                self.provider = identity

            def publish(self, **kwargs):
                self.kwargs = kwargs
                return True

            def invalidate_latest(self) -> None:
                self.invalidated = True

        class FakeRuntimeView:
            def __init__(self) -> None:
                self.decision = FakeDecisionView()

        view = FakeRuntimeView()
        with patch(
            "cli.automa_cli.hosts.views.decision_live.fetch_host_telemetry_latest",
            side_effect=ConnectionError("down"),
        ):
            adapter = DecisionViewAdapter(
                host_name="piracer",
                base_url="http://piracer.local:8887",
                view_server=view,
                timeout_s=1.0,
            )
            published = adapter.publish_frame(normalized, (b"jpeg-bytes", "image/jpeg"))

        self.assertTrue(published)
        self.assertFalse(view.decision.invalidated)
        self.assertEqual(view.decision.kwargs["image"], (b"jpeg-bytes", "image/jpeg"))
        self.assertEqual(view.decision.kwargs["report"].frame_id, "frame-1")
        panel = view.decision.kwargs["host_telemetry"]
        self.assertEqual(panel["status"], "unavailable")
        self.assertEqual(panel["reason"], "publisher_missing")
        self.assertFalse(panel["joined"])
        self.assertNotIn("authority", panel)

    def test_live_view_retries_a_small_provider_clock_skew(self) -> None:
        future_error = DecisionSurfaceError(
            "decision_publication_unavailable",
            "future",
            details={"reason": "future_dated"},
        )
        normalized = {"frame_id": "frame-1"}
        with patch(
            "cli.automa_cli.hosts.views.decision_live.fetch_decision_transaction",
            side_effect=[
                {"publication": {}, "image_base64": "anBlZw==", "image_content_type": "image/jpeg"},
                {"publication": {}, "image_base64": "anBlZw==", "image_content_type": "image/jpeg"},
            ],
        ), patch(
            "cli.automa_cli.hosts.views.decision_live.accept_decision_publication",
            side_effect=[future_error, normalized],
        ), patch(
            "cli.automa_cli.hosts.views.decision_live.time.time",
            return_value=10.0,
        ), patch(
            "cli.automa_cli.hosts.views.decision_live.time.sleep",
        ) as sleep:
            normalized, _ = _accepted_pair(
                "http://piracer.local:8887",
                host_name="piracer",
                timeout_s=1.0,
            )

        self.assertEqual(normalized["frame_id"], "frame-1")
        sleep.assert_called_once_with(0.04)

    def test_normalizes_point_with_exact_identity_and_freshness(self) -> None:
        normalized = normalize_host_telemetry_record(
            _record(), now_ms=NOW_MS, host_name="piracer"
        )

        self.assertEqual(normalized["status"], "healthy")
        self.assertEqual(normalized["freshness"]["source_age_ms"], 500)
        self.assertEqual(normalized["freshness"]["publication_age_ms"], 500)
        self.assertIsNone(normalized["host_tick"]["gap_since_previous_ms"])
        self.assertIsNone(normalized["host_tick"]["skipped_since_previous"])
        self.assertNotIn("coverage", normalized)
        self.assertNotIn("interval_covered", str(normalized))
        self.assertEqual(
            normalized["identity"]["source_frame"],
            {
                "frame_id": "frame-1",
                "frame_index": 1,
                "captured_at_ms": 8_000,
                "completed_at_ms": 8_500,
            },
        )
        self.assertEqual(
            normalized["host_selected_output"], {"steering": 0.0, "throttle": 0.0}
        )

    def test_join_requires_complete_composite_identity_and_keeps_authority_separate(
        self,
    ) -> None:
        point = normalize_host_telemetry_record(_record(), now_ms=NOW_MS)
        decision = _normalized_decision()
        joined = join_host_telemetry_to_decision(point, decision)

        self.assertTrue(joined["joined"])
        self.assertEqual(joined["status"], "healthy")
        self.assertEqual(joined["identity"], decision_publication_identity(decision))
        self.assertNotIn("authority", joined)
        self.assertNotIn("host_application", joined)

        mismatched = copy.deepcopy(_record())
        mismatched["source_frame"]["captured_at_ms"] += 1
        mismatched_point = normalize_host_telemetry_record(mismatched, now_ms=NOW_MS)
        with self.assertRaisesRegex(ValueError, "exactly match") as raised:
            join_host_telemetry_to_decision(mismatched_point, _normalized_decision())
        self.assertEqual(raised.exception.reason, "identity_mismatch")

    def test_gap_facts_remain_visible_without_an_interval_claim(self) -> None:
        skipped = _record(
            sequence=2,
            gap_since_previous_ms=2_500,
            skipped_since_previous=3,
        )
        skipped_point = normalize_host_telemetry_record(
            skipped,
            now_ms=NOW_MS,
            host_name="piracer",
        )
        self.assertEqual(skipped_point["status"], "healthy")
        self.assertEqual(skipped_point["host_tick"]["gap_since_previous_ms"], 2_500)
        self.assertEqual(skipped_point["host_tick"]["skipped_since_previous"], 3)
        self.assertNotIn("coverage", skipped_point)
        self.assertNotIn("interval_covered", str(skipped_point))
        self.assertNotIn("contiguous", str(skipped_point))
        joined = join_host_telemetry_to_decision(skipped_point, _normalized_decision())
        self.assertTrue(joined["joined"])
        self.assertEqual(joined["host_tick"]["skipped_since_previous"], 3)
        self.assertEqual(
            joined["host_selected_output"], {"steering": 0.0, "throttle": 0.0}
        )
        self.assertNotIn("coverage", joined)
        self.assertNotIn("interval_covered", str(joined))

    def test_unhealthy_point_fails_closed_before_join(self) -> None:
        unavailable = copy.deepcopy(
            normalize_host_telemetry_record(
                _record(),
                now_ms=NOW_MS,
                host_name="piracer",
            )
        )
        unavailable["status"] = "unavailable"
        unavailable["reason"] = "observer_error"
        with self.assertRaises(ValueError) as unavailable_error:
            join_host_telemetry_to_decision(unavailable, _normalized_decision())
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
        decision["report"] = replace(decision["report"], timestamp_ms=decision["report"].timestamp_ms + 1)
        with self.assertRaises(ValueError) as raised:
            join_host_telemetry_to_decision(point, decision)
        self.assertEqual(raised.exception.reason, "identity_mismatch")
        self.assertIn("timestamp", raised.exception.message_text)

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

    def test_a_negative_count_reports_an_integer_not_an_epoch(self) -> None:
        for path, key in (("limits", "max_gap_ms"), ("host_tick", "source_age_ms")):
            record = _record()
            record[path][key] = -1
            with self.subTest(field=f"{path}.{key}"):
                with self.assertRaises(ValueError) as raised:
                    normalize_host_telemetry_record(record, now_ms=NOW_MS)
                self.assertEqual(raised.exception.reason, "field_invalid")
                self.assertEqual(
                    raised.exception.message_text,
                    f"Host telemetry {path}.{key} must be a non-negative integer.",
                )


if __name__ == "__main__":
    unittest.main()
