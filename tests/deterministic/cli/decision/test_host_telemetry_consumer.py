"""Deterministic checks for the latest-point host telemetry consumer."""

from __future__ import annotations

import copy
import json
import math
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from autonomy.decision_cycle.activation import step_activation
from autonomy.runtime.frame_loop import FRAME_PUBLICATION_SCHEMA
from autonomy.runtime.report import HostReport
from autonomy.runtime.routes import TELEMETRY_SCHEMA
from cli.automa_cli.hosts.views.decision_live import (
    DecisionViewAdapter,
    read_host_telemetry_panel,
)
from cli.automa_cli.hosts.views.observation import ObservationPublisher
from cli.automa_cli.hosts.publications.decision_records import DecisionRunners
from cli.automa_cli.hosts.publications.frame_publication import read_frame_publication
from cli.automa_cli.hosts.publications.host_telemetry import (
    decision_publication_identity,
    join_host_telemetry_to_decision,
    normalize_host_telemetry_record,
)
from cli.automa_cli.hosts.step_activations import decision_generation_id
from tests.shared.cli.decision.decision_surfaces_fixtures import (
    frame_inputs,
    left_obstruction_frame,
    published_cycle,
    vehicle_report_for_records,
)
from tests.support.action_fixtures import FIRST_ACTIVE_PLAN_SPEC, INACTIVE_PROPOSAL_SPEC


NOW_MS = 10_000
# The decision steps the reports name: test plugins and the framework's hold action.
_STEPS = {
    "proposal": step_activation("proposal", ["inactive"], {"inactive": INACTIVE_PROPOSAL_SPEC}),
    "plan": step_activation("plan", ["first_active"], {"first_active": FIRST_ACTIVE_PLAN_SPEC}),
    "action": step_activation("action", ["hold"], {"hold": "autonomy.decision_cycle.action.hold:HoldAction"}),
}
GENERATION_ID = decision_generation_id(_STEPS)


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
    records = DecisionRunners.from_payloads({step: activation.to_payload() for step, activation in _STEPS.items()}).run(
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
        published_at_ms=frame["completed_at_ms"],
        observation=observation,
        memory=memory,
        values={
            "source_id": "donkeycar:piracer",
            "activation": {
                "generation_id": GENERATION_ID,
                "steps": {step: activation.to_payload() for step, activation in _STEPS.items()},
            },
            "stale_after_ms": 1_500,
        },
    )
    return {"report": HostReport.from_dict(report)}


def _physical_publication() -> dict:
    decision = _decision()["report"].to_dict()
    return {
        "schema": FRAME_PUBLICATION_SCHEMA,
        "health": "healthy",
        "ok": True,
        "read_at_ms": 9_500,
        "result_age_ms": 500,
        "stale_after_ms": decision["values"]["stale_after_ms"],
        "report": decision,
    }


def _normalized_decision() -> dict:
    """The publication join and identity actually read: one HostReport."""

    return read_frame_publication(
        _physical_publication(),
        host_name="piracer",
        now_ms=NOW_MS,
    )


FETCH = "cli.automa_cli.hosts.views.decision_live.fetch_host_telemetry_latest"
READ_PANEL = "cli.automa_cli.hosts.views.decision_live.read_host_telemetry_panel"


class HostTelemetryConsumerTests(unittest.TestCase):
    def test_the_adapter_publishes_the_frame_then_joins_its_telemetry(self) -> None:
        from cli.automa_cli.frontend.perception_view import PerceptionView

        normalized = _normalized_decision()
        view = PerceptionView(host_name="piracer")
        joined = join_host_telemetry_to_decision(normalize_host_telemetry_record(_record(), now_ms=NOW_MS), normalized)
        adapter = DecisionViewAdapter(
            host_name="piracer", base_url="http://piracer.local:8887",
            publisher=ObservationPublisher(view), timeout_s=1.0,
        )

        seen = []

        def read_panel(*_args, **kwargs):
            seen.append((json.loads(view.latest_json())["report"], kwargs["normalized_decision"]))
            return joined

        with patch(READ_PANEL, side_effect=read_panel):
            adapter.publish_frame(normalized, None)

        # The completed frame was visible before telemetry answered.
        self.assertEqual(len(seen), 1)
        published, joined_against = seen[0]
        self.assertEqual(published, normalized["report"].to_dict())
        self.assertIs(joined_against, normalized)
        self.assertEqual(adapter.publication["host_telemetry"], joined)

    def test_telemetry_failure_still_publishes_the_decision_image(self) -> None:
        perception = Mock()
        with patch(FETCH, side_effect=ConnectionError("down")):
            adapter = DecisionViewAdapter(
                host_name="piracer", base_url="http://piracer.local:8887",
                publisher=ObservationPublisher(perception), timeout_s=1.0,
            )
            adapter.publish_frame(_normalized_decision(), b"jpeg-bytes")

        self.assertEqual(perception.publish.call_args.kwargs["frame_bytes"], b"jpeg-bytes")
        self.assertEqual(adapter.publication["report"]["frame_id"], "frame-1")
        panel = adapter.publication["host_telemetry"]
        self.assertEqual(
            (panel["status"], panel["reason"], panel["joined"]), ("unavailable", "publisher_missing", False)
        )
        self.assertNotIn("authority", panel)

    def _read_panel(self, fetched):
        """Read the panel once against the healthy decision; ``fetched`` is the latest record or the read's error."""

        answer = {"side_effect": fetched} if isinstance(fetched, Exception) else {"return_value": fetched}
        with patch(FETCH, **answer) as fetch, \
                patch("cli.automa_cli.hosts.views.decision_live.time.sleep",
                      side_effect=AssertionError("telemetry refresh slept")):
            panel = read_host_telemetry_panel(
                "http://piracer.local:8887",
                normalized_decision=_normalized_decision(),
                host_name="piracer",
                timeout_s=1.0,
                now_ms=NOW_MS,
            )
        self.assertEqual(fetch.call_count, 1)
        return panel

    def test_matching_latest_point_is_useful_after_one_read(self) -> None:
        panel = self._read_panel(_record())

        self.assertTrue(panel["joined"])
        self.assertEqual(panel["status"], "healthy")
        self.assertEqual(panel["identity"]["source_frame"], {
            "frame_id": "frame-1", "frame_index": 1, "captured_at_ms": 8_000, "completed_at_ms": 8_500,
        })
        self.assertEqual(panel["host_tick"]["observed_at_ms"], 9_000)
        self.assertEqual(panel["host_tick"]["published_at_ms"], 9_500)
        self.assertIsNone(panel["host_tick"]["gap_since_previous_ms"])
        self.assertIsNone(panel["host_tick"]["skipped_since_previous"])
        self.assertEqual(panel["freshness"]["source_age_ms"], 500)
        self.assertEqual(panel["freshness"]["publication_age_ms"], 500)
        self.assertEqual(panel["pilot_output"], {"steering": 0.25, "throttle": 0.0})
        self.assertEqual(panel["host_selected_output"], {"steering": 0.0, "throttle": 0.0})
        self.assertEqual(panel["application"]["actuator_feedback"], "unavailable")
        self.assertNotIn("authority", panel)
        self.assertNotIn("coverage", panel)
        self.assertNotIn("interval_covered", str(panel))

    def test_latest_point_mismatch_stays_explicit_without_history(self) -> None:
        def mismatch_frame(record: dict) -> None:
            record["source_frame"]["frame_id"] = "frame-2"
            record["source_frame"]["frame_index"] = 2

        def mismatch_run(record: dict) -> None:
            record["run_id"] = "run-2"

        def mismatch_generation(record: dict) -> None:
            record["generation_id"] = "other-gen"
            record["activation"] = {"generation_id": "other-gen"}

        for name, mutate in (("frame", mismatch_frame), ("run", mismatch_run), ("generation", mismatch_generation)):
            with self.subTest(mismatch=name):
                latest = _record(sequence=2, gap_since_previous_ms=50, skipped_since_previous=0)
                latest["host_selected_output"] = {"steering": 0.4, "throttle": 0.2}
                mutate(latest)
                panel = self._read_panel(latest)

                self.assertFalse(panel["joined"])
                self.assertEqual((panel["status"], panel["reason"]), ("mismatched", "identity_mismatch"))
                self.assertIsNone(panel["host_selected_output"])
                self.assertIsNone(panel["pilot_output"])
                self.assertIsNone(panel["application"])

    def test_bad_latest_samples_stay_on_that_read(self) -> None:
        malformed = _record()
        malformed["schema"] = "wrong"
        unavailable = _record()
        unavailable["status"] = "unavailable"
        unavailable["reason"] = "publisher_missing"
        reported_gap = _record(sequence=2, gap_since_previous_ms=100, skipped_since_previous=1)
        reported_gap["status"] = "unavailable"
        reported_gap["reason"] = "sequence_gap"
        cases = (
            ("source_stale", _record(captured_at_ms=6_500, completed_at_ms=7_000, observed_at_ms=9_000),
             "stale", "source_stale"),
            ("publication_stale",
             _record(captured_at_ms=7_000, completed_at_ms=7_500, observed_at_ms=8_000, published_at_ms=8_000),
             "stale", "publication_stale"),
            ("future_dated", _record(published_at_ms=10_251, observed_at_ms=9_000), "stale", "future_dated"),
            ("malformed", malformed, "error", "schema_invalid"),
            ("unavailable", unavailable, "unavailable", "publisher_missing"),
            ("producer_sequence_gap", reported_gap, "limited", "sequence_gap"),
            ("read_failed", ConnectionError("down"), "unavailable", "publisher_missing"),
        )
        for name, fetched, status, reason in cases:
            with self.subTest(sample=name):
                panel = self._read_panel(fetched)
                self.assertFalse(panel["joined"])
                self.assertEqual((panel["status"], panel["reason"]), (status, reason))
                self.assertIsNone(panel["host_selected_output"])
                self.assertIsNone(panel["pilot_output"])
        self.assertIsNone(self._read_panel(ConnectionError("down"))["record"])

    def test_a_later_failure_does_not_reuse_the_previous_success(self) -> None:
        with patch(FETCH, side_effect=[_record(), ConnectionError("down")]) as fetched:
            first, second = (
                read_host_telemetry_panel(
                    "http://piracer.local:8887",
                    normalized_decision=_normalized_decision(),
                    host_name="piracer",
                    timeout_s=1.0,
                    now_ms=NOW_MS,
                )
                for _ in range(2)
            )

        self.assertEqual(fetched.call_count, 2)
        self.assertTrue(first["joined"])
        self.assertEqual(first["host_selected_output"], {"steering": 0.0, "throttle": 0.0})
        self.assertFalse(second["joined"])
        self.assertEqual((second["status"], second["reason"]), ("unavailable", "publisher_missing"))
        self.assertIsNone(second["host_selected_output"])
        self.assertIsNone(second["record"])

    def test_join_requires_complete_composite_identity_and_keeps_authority_separate(self) -> None:
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
        later_decision = _decision()
        report = later_decision["report"]
        later_decision["report"] = replace(report, timestamp_ms=report.timestamp_ms + 1)
        # The decision's timestamp is part of the source identity, like the frame's capture time.
        for point, decision in (
            (normalize_host_telemetry_record(mismatched, now_ms=NOW_MS), _normalized_decision()),
            (point, later_decision),
        ):
            with self.assertRaisesRegex(ValueError, "exactly match") as raised:
                join_host_telemetry_to_decision(point, decision)
            self.assertEqual(raised.exception.reason, "identity_mismatch")

    def test_gap_facts_remain_visible_without_an_interval_claim(self) -> None:
        skipped = _record(sequence=2, gap_since_previous_ms=2_500, skipped_since_previous=3)
        skipped_point = normalize_host_telemetry_record(skipped, now_ms=NOW_MS, host_name="piracer")
        self.assertEqual(skipped_point["status"], "healthy")
        self.assertEqual(skipped_point["host_tick"]["gap_since_previous_ms"], 2_500)
        self.assertEqual(skipped_point["host_tick"]["skipped_since_previous"], 3)
        self.assertNotIn("coverage", skipped_point)
        self.assertNotIn("interval_covered", str(skipped_point))
        self.assertNotIn("contiguous", str(skipped_point))
        joined = join_host_telemetry_to_decision(skipped_point, _normalized_decision())
        self.assertTrue(joined["joined"])
        self.assertEqual(joined["host_tick"]["skipped_since_previous"], 3)
        self.assertEqual(joined["host_selected_output"], {"steering": 0.0, "throttle": 0.0})
        self.assertNotIn("coverage", joined)
        self.assertNotIn("interval_covered", str(joined))

    def test_a_publication_within_the_skew_tolerance_reads_with_a_negative_age(self) -> None:
        # Past the tolerance it is future_dated: test_bad_latest_samples_stay_on_that_read.
        within_skew = _record(published_at_ms=10_100, observed_at_ms=9_000)
        normalized = normalize_host_telemetry_record(within_skew, now_ms=NOW_MS)
        self.assertEqual(normalized["freshness"]["future_skew_ms"], 100)
        self.assertEqual(normalized["freshness"]["publication_age_ms"], -100)

    def test_invalid_schema_numbers_and_mode_fail_closed(self) -> None:
        def field(path: tuple[str, ...], value):
            def mutate(record: dict) -> None:
                target = record
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
            return mutate

        cases = (
            ("schema", field(("schema",), "wrong"), "schema_invalid"),
            ("nonfinite steering", field(("pilot_output", "steering"), math.inf), "field_invalid"),
            ("unknown mode", field(("mode",), "remote"), "field_invalid"),
            ("boolean throttle", field(("user_input", "throttle"), False), "field_invalid"),
            ("zero sequence", field(("host_tick", "sequence"), 0), "field_invalid"),
        )
        for name, mutate, reason in cases:
            with self.subTest(name):
                record = _record()
                mutate(record)
                with self.assertRaises(ValueError) as raised:
                    normalize_host_telemetry_record(record, now_ms=NOW_MS)
                self.assertEqual(raised.exception.reason, reason)

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
