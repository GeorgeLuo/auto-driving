"""Deterministic producer checks for the PiRacer host-boundary seam."""

from __future__ import annotations

import ast
import logging
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.manager import AutonomyManager
from implementations.runtime.donkeycar.donkey_part import AutonomyPilotPart
from implementations.runtime.donkeycar.host_telemetry import (
    HOST_TELEMETRY_BOUNDARY,
    HOST_TELEMETRY_LIMITS,
    HOST_TELEMETRY_SCHEMA,
    DriveModeTelemetryAdapter,
    HostTelemetryStore,
    identity_tuple,
    parse_records_query,
)


ROOT = Path(__file__).resolve().parents[3]
MANAGE_PATH = ROOT / "deploy" / "targets" / "donkeycar" / "app" / "manage.py"
PATCH_PATH = (
    ROOT
    / "deploy"
    / "targets"
    / "donkeycar"
    / "patches"
    / "waveshare-donkeycar-local.patch"
)


class _Clock:
    def __init__(self, now_ms: int = 10_000) -> None:
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms


def _frame(index: int, *, captured_at_ms: int = 10_000) -> dict[str, int | str]:
    return {
        "frame_id": f"donkey_frame_{index:06d}",
        "frame_index": index,
        "captured_at_ms": captured_at_ms,
        "completed_at_ms": captured_at_ms,
    }


def _store(clock: _Clock | None = None, *, max_records: int = 256) -> HostTelemetryStore:
    active_clock = clock or _Clock()
    return HostTelemetryStore(
        vehicle_id="piracer",
        source_id="donkeycar:piracer",
        run_id="donkey-run-test",
        generation_id="shadow-proposals:1000",
        activation_engine_id="shadow-proposals",
        activation_activated_at_ms=1_000,
        clock=active_clock,
        max_records=max_records,
    )


def _observe(
    store: HostTelemetryStore,
    index: int,
    *,
    observed_at_ms: int = 10_000,
    published_at_ms: int = 10_000,
    sequence: int | None = None,
    completed_at_ms: int = 10_000,
) -> dict:
    return store.observe(
        mode="user",
        user_input={"steering": 0.0, "throttle": 0.0},
        pilot_output={"steering": 0.4, "throttle": 0.2},
        host_selected_output={"steering": 0.0, "throttle": 0.0},
        source_frame=_frame(index, captured_at_ms=completed_at_ms),
        observed_at_ms=observed_at_ms,
        published_at_ms=published_at_ms,
        sequence=sequence,
    )


def _drive_mode_class():
    """Compile exactly the DriveMode class shipped in deployment source."""

    tree = ast.parse(MANAGE_PATH.read_text(encoding="utf-8"), filename=str(MANAGE_PATH))
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    nodes = [node for node in classes if node.name == "DriveMode"]
    if len(nodes) != 1:
        raise AssertionError("manage.py must contain exactly one DriveMode class")
    module = ast.Module(body=[nodes[0]], type_ignores=[])
    namespace = {"logger": logging.getLogger(__name__)}
    exec(compile(ast.fix_missing_locations(module), str(MANAGE_PATH), "exec"), namespace)
    return namespace["DriveMode"]


class HostTelemetryStoreTests(unittest.TestCase):
    def test_identity_clock_numeric_limits_and_healthy_schema(self) -> None:
        clock = _Clock()
        store = _store(clock)
        result = _observe(store, 1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["schema"], HOST_TELEMETRY_SCHEMA)
        record = result["record"]
        self.assertEqual(
            (
                record["vehicle_id"],
                record["source_id"],
                record["run_id"],
                record["generation_id"],
                record["activation"]["engine_id"],
                record["activation"]["activated_at_ms"],
                record["activation"]["generation_id"],
            ),
            (
                "piracer",
                "donkeycar:piracer",
                "donkey-run-test",
                "shadow-proposals:1000",
                "shadow-proposals",
                1_000,
                "shadow-proposals:1000",
            ),
        )
        self.assertEqual(record["source_frame"], _frame(1))
        self.assertEqual(record["host_tick"]["source_age_ms"], 0)
        self.assertIsNone(record["host_tick"]["gap_since_previous_ms"])
        self.assertIsNone(record["host_tick"]["skipped_since_previous"])
        self.assertEqual(record["limits"], HOST_TELEMETRY_LIMITS)
        self.assertEqual(
            identity_tuple(record),
            (
                "piracer",
                "donkeycar:piracer",
                "donkey-run-test",
                "shadow-proposals:1000",
                "shadow-proposals",
                1_000,
                "shadow-proposals:1000",
                "donkey_frame_000001",
                1,
                10_000,
                10_000,
            ),
        )
        self.assertEqual(
            record["application"],
            {
                "boundary": HOST_TELEMETRY_BOUNDARY,
                "actuator_feedback": "unavailable",
            },
        )
        with self.assertRaises(ValueError):
            HostTelemetryStore(
                vehicle_id="piracer",
                source_id="donkeycar:piracer",
                run_id="donkey-run-test",
                generation_id="shadow-proposals:1000",
                activation_engine_id="shadow-proposals",
                activation_activated_at_ms=1_000,
                activation_generation_id="other-generation",
            )

        mismatch = _store()
        _observe(mismatch, 1)
        mismatched_frame = _observe(
            mismatch,
            1,
            observed_at_ms=10_001,
            published_at_ms=10_001,
            completed_at_ms=9_999,
        )
        self.assertEqual(mismatched_frame["reason"], "identity_mismatch")
        self.assertIsNone(mismatch.latest(now_ms=10_001)["record"])

    def test_invalid_numeric_mode_future_and_clock_regression_fail_closed(self) -> None:
        invalid_values = (
            {"steering": True, "throttle": 0.0},
            {"steering": float("nan"), "throttle": 0.0},
            {"steering": 1.01, "throttle": 0.0},
        )
        for user_input in invalid_values:
            with self.subTest(user_input=user_input):
                store = _store()
                result = store.observe(
                    mode="user",
                    user_input=user_input,
                    pilot_output={"steering": 0.0, "throttle": 0.0},
                    host_selected_output={"steering": 0.0, "throttle": 0.0},
                    source_frame=_frame(1),
                    observed_at_ms=10_000,
                    published_at_ms=10_000,
                )
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["reason"], "field_invalid")
                self.assertEqual(store.latest(now_ms=10_000)["status"], "error")

        unknown_mode = _store().observe(
            mode="mystery",
            user_input={"steering": 0.0, "throttle": 0.0},
            pilot_output={"steering": 0.0, "throttle": 0.0},
            host_selected_output={"steering": 0.0, "throttle": 0.0},
            source_frame=_frame(1),
            observed_at_ms=10_000,
            published_at_ms=10_000,
        )
        self.assertEqual(unknown_mode["reason"], "field_invalid")

        future = _store().observe(
            mode="user",
            user_input={"steering": 0.0, "throttle": 0.0},
            pilot_output={"steering": 0.0, "throttle": 0.0},
            host_selected_output={"steering": 0.0, "throttle": 0.0},
            source_frame=_frame(1),
            observed_at_ms=10_251,
            published_at_ms=10_251,
        )
        self.assertEqual(future["reason"], "future_dated")

        clock = _Clock(10_000)
        store = _store(clock)
        self.assertTrue(_observe(store, 1)["ok"])
        clock.now_ms = 9_999
        regression = _observe(
            store,
            2,
            observed_at_ms=10_001,
            published_at_ms=10_001,
        )
        self.assertEqual(regression["reason"], "sequence_regressed")

    def test_warming_stale_publication_stopped_and_no_cached_error(self) -> None:
        warming = _store()
        self.assertEqual(warming.latest(now_ms=10_000)["status"], "warming")

        stale_clock = _Clock(12_000)
        stale = _store(stale_clock)
        result = _observe(
            stale,
            1,
            observed_at_ms=12_000,
            published_at_ms=12_000,
            completed_at_ms=10_000,
        )
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["reason"], "source_stale")
        self.assertFalse(stale.latest(now_ms=12_000)["ok"])

        publication_stale = _store()
        self.assertTrue(_observe(publication_stale, 1)["ok"])
        expired = publication_stale.latest(now_ms=11_501)
        self.assertEqual(expired["status"], "stale")
        self.assertEqual(expired["reason"], "publication_stale")
        self.assertFalse(expired["ok"])

        stopped = _store()
        _observe(stopped, 1)
        stopped.stop()
        stopped_latest = stopped.latest(now_ms=10_000)
        self.assertEqual(stopped_latest["status"], "stopped")
        self.assertEqual(stopped_latest["reason"], "producer_stopped")
        self.assertIsNone(stopped_latest["record"])

        errored = _store()
        _observe(errored, 1)
        errored.mark_unavailable("observer_error")
        failed_latest = errored.latest(now_ms=10_000)
        self.assertEqual(failed_latest["status"], "unavailable")
        self.assertEqual(failed_latest["reason"], "observer_error")
        self.assertIsNone(failed_latest["record"])

        # Read validation is side-effect free: a bad route query cannot turn a
        # previously healthy latest publication into a cached query error.
        read_only = _store()
        _observe(read_only, 1)
        self.assertEqual(
            read_only.records(after_sequence=0, limit=0, now_ms=10_000)["reason"],
            "query_invalid",
        )
        self.assertTrue(read_only.latest(now_ms=10_000)["ok"])
        self.assertEqual(
            read_only.latest(now_ms=-1)["reason"],
            "field_invalid",
        )
        self.assertTrue(read_only.latest(now_ms=10_000)["ok"])

    def test_sequence_gap_time_gap_skip_arithmetic_and_history_bounds(self) -> None:
        store = _store()
        self.assertTrue(_observe(store, 1, sequence=1)["ok"])

        sequence_gap = _observe(
            store,
            2,
            sequence=3,
            observed_at_ms=10_100,
            published_at_ms=10_100,
        )
        self.assertEqual(sequence_gap["status"], "unavailable")
        self.assertEqual(sequence_gap["reason"], "sequence_gap")
        self.assertEqual(sequence_gap["record"]["host_tick"]["skipped_since_previous"], 1)
        self.assertEqual(sequence_gap["record"]["host_tick"]["gap_since_previous_ms"], 100)

        time_gap_store = _store(_Clock(11_001))
        _observe(time_gap_store, 1)
        time_gap = _observe(
            time_gap_store,
            2,
            observed_at_ms=11_001,
            published_at_ms=11_001,
            completed_at_ms=11_001,
        )
        self.assertEqual(time_gap["status"], "unavailable")
        self.assertEqual(time_gap["reason"], "coverage_gap")
        self.assertEqual(time_gap["record"]["host_tick"]["gap_since_previous_ms"], 1_001)
        self.assertEqual(time_gap["record"]["host_tick"]["skipped_since_previous"], 0)

        skipped_store = _store()
        _observe(skipped_store, 1)
        skipped_store.mark_skipped(2)
        skipped = _observe(
            skipped_store,
            2,
            observed_at_ms=10_100,
            published_at_ms=10_100,
        )
        self.assertEqual(skipped["record"]["host_tick"]["sequence"], 4)
        self.assertEqual(skipped["record"]["host_tick"]["skipped_since_previous"], 2)

        query_cases = (
            ({"after_sequence": "4", "limit": "8"}, (4, 8)),
            ({"after_sequence": 0, "limit": 1, "extra": 1}, None),
            ({"after_sequence": -1, "limit": 1}, None),
            ({"after_sequence": 0, "limit": 0}, None),
            ({"after_sequence": 0, "limit": 129}, None),
            ({"after_sequence": True, "limit": 1}, None),
        )
        for query, expected in query_cases:
            with self.subTest(query=query):
                if expected is None:
                    with self.assertRaises(ValueError):
                        parse_records_query(query)
                else:
                    self.assertEqual(parse_records_query(query), expected)

        bounded = _store()
        for index in range(257):
            _observe(bounded, index)
        evicted = bounded.records(after_sequence=0, limit=128, now_ms=10_000)
        self.assertFalse(evicted["ok"])
        self.assertEqual(evicted["coverage_reason"], "history_evicted")
        self.assertEqual(evicted["records"], [])
        limited = bounded.records(after_sequence=1, limit=128, now_ms=10_000)
        self.assertTrue(limited["ok"])
        self.assertLessEqual(len(limited["records"]), 128)

        # An unpublished host tick reserves coverage, so the next complete
        # record cannot silently look contiguous with the prior success.
        failed_tick = _store()
        _observe(failed_tick, 1)
        failed_tick.mark_unavailable("observer_error")
        resumed = _observe(
            failed_tick,
            2,
            observed_at_ms=10_001,
            published_at_ms=10_001,
        )
        self.assertEqual(resumed["record"]["host_tick"]["sequence"], 3)
        self.assertEqual(resumed["record"]["host_tick"]["skipped_since_previous"], 1)
        self.assertEqual(resumed["reason"], "sequence_gap")

    def test_implicit_publication_timestamp_is_taken_after_append(self) -> None:
        class SteppingClock:
            def __init__(self) -> None:
                self.values = iter((10_000, 10_001))

            def __call__(self) -> int:
                return next(self.values)

        store = _store(SteppingClock())
        result = store.observe(
            mode="user",
            user_input={"steering": 0.0, "throttle": 0.0},
            pilot_output={"steering": 0.0, "throttle": 0.0},
            host_selected_output={"steering": 0.0, "throttle": 0.0},
            source_frame=_frame(1),
            observed_at_ms=10_000,
        )
        self.assertEqual(result["record"]["host_tick"]["published_at_ms"], 10_001)

    def test_latest_and_history_are_atomic_detached_snapshots(self) -> None:
        store = _store()
        for index in range(3):
            _observe(store, index)

        latest = store.latest(now_ms=10_000)
        history = store.records(after_sequence=0, limit=128, now_ms=10_000)
        self.assertEqual(latest["record"]["host_tick"]["sequence"], 3)
        self.assertEqual(
            [record["host_tick"]["sequence"] for record in history["records"]],
            [1, 2, 3],
        )
        latest["record"]["source_frame"]["frame_id"] = "mutated"
        history["records"][0]["host_tick"]["sequence"] = 99
        fresh_latest = store.latest(now_ms=10_000)
        fresh_history = store.records(after_sequence=0, limit=128, now_ms=10_000)
        self.assertEqual(fresh_latest["record"]["source_frame"]["frame_id"], "donkey_frame_000002")
        self.assertEqual(fresh_history["records"][0]["host_tick"]["sequence"], 1)


class DriveModeBoundaryTests(unittest.TestCase):
    def test_real_drive_mode_receives_handed_off_frame_and_selected_output(self) -> None:
        clock = _Clock()
        store = _store(clock)
        adapter = DriveModeTelemetryAdapter(store)
        with (
            patch.object(
                __import__(
                    "implementations.runtime.donkeycar.donkey_part",
                    fromlist=["timestamp_ms"],
                ),
                "timestamp_ms",
                return_value=10_000,
            ),
            patch("autonomy.decision.cycle.timestamp_ms", return_value=10_000),
            patch("autonomy.runtime.manager.timestamp_ms", return_value=10_000),
        ):
            part = AutonomyPilotPart(
                host=AutonomyCycleHost(manager=AutonomyManager()),
                min_interval_s=0.0,
                host_telemetry=adapter,
            )
            part.run(image_array=object(), mode="user")
            selected = _drive_mode_class()(host_telemetry=adapter).run(
                "user",
                0.42,
                0.37,
                0.80,
                0.60,
            )

        self.assertEqual(selected, (0.42, 0.37))
        latest = adapter.latest(now_ms=10_000)
        self.assertTrue(latest["ok"])
        record = latest["record"]
        self.assertEqual(record["source_frame"], _frame(0))
        self.assertEqual(record["mode"], "user")
        self.assertEqual(record["user_input"], {"steering": 0.42, "throttle": 0.37})
        self.assertEqual(record["pilot_output"], {"steering": 0.80, "throttle": 0.60})
        self.assertEqual(
            record["host_selected_output"],
            {"steering": 0.42, "throttle": 0.37},
        )
        self.assertNotEqual(record["pilot_output"], record["host_selected_output"])

    def test_held_cadence_frame_is_repeated_identity_not_a_new_frame(self) -> None:
        monotonic = _Clock(0)
        store = _store()
        adapter = DriveModeTelemetryAdapter(store)
        module = __import__(
            "implementations.runtime.donkeycar.donkey_part",
            fromlist=["timestamp_ms"],
        )
        with (
            patch.object(module, "timestamp_ms", return_value=10_000),
            patch("autonomy.decision.cycle.timestamp_ms", return_value=10_000),
            patch("autonomy.runtime.manager.timestamp_ms", return_value=10_000),
        ):
            part = AutonomyPilotPart(
                host=AutonomyCycleHost(),
                min_interval_s=0.5,
                monotonic=lambda: monotonic.now_ms / 1000.0,
                host_telemetry=adapter,
            )
            part.run(image_array=object(), mode="user")
            drive_mode = _drive_mode_class()(host_telemetry=adapter)
            drive_mode.run("user", 0.1, 0.2, 0.0, 0.0)
            monotonic.now_ms = 200
            part.run(image_array=object(), mode="user")
            drive_mode.run("user", 0.3, 0.4, 0.0, 0.0)

        records = adapter.records(after_sequence=0, limit=128, now_ms=10_000)
        self.assertTrue(records["ok"])
        self.assertEqual(len(records["records"]), 2)
        self.assertEqual(records["records"][0]["source_frame"], records["records"][1]["source_frame"])
        self.assertEqual(part.latest_snapshot.frame_index, 0)
        self.assertEqual(records["records"][1]["host_tick"]["skipped_since_previous"], 0)

    def test_observer_failure_preserves_drive_mode_output_and_shutdown_stops_store(self) -> None:
        class RaisingStore:
            def observe(self, **_kwargs):
                raise RuntimeError("telemetry-store-failed")

            def mark_unavailable(self, *_args, **_kwargs):
                raise RuntimeError("telemetry-store-failed")

        drive_mode = _drive_mode_class()(
            host_telemetry=DriveModeTelemetryAdapter(RaisingStore())  # type: ignore[arg-type]
        )
        self.assertEqual(
            drive_mode.run("user", 0.61, 0.29, 0.0, 0.0),
            (0.61, 0.29),
        )

        store = _store()
        stopped_drive_mode = _drive_mode_class()(
            host_telemetry=DriveModeTelemetryAdapter(store)
        )
        stopped_drive_mode.shutdown()
        self.assertEqual(store.status()["status"], "stopped")
        self.assertEqual(store.latest(now_ms=10_000)["reason"], "producer_stopped")


class VendorRouteShapeTests(unittest.TestCase):
    def test_telemetry_routes_are_read_only_exact_and_bounded(self) -> None:
        patch_text = PATCH_PATH.read_text(encoding="utf-8")
        self.assertIn(
            '+            (r"/autonomy/telemetry/latest", AutonomyTelemetryLatestAPI),',
            patch_text,
        )
        self.assertIn(
            '+            (r"/autonomy/telemetry/records", AutonomyTelemetryRecordsAPI),',
            patch_text,
        )
        start = patch_text.index("+class AutonomyTelemetryLatestAPI")
        end = patch_text.index(" class WsTest", start)
        handlers = patch_text[start:end]
        methods = [
            method
            for method in re.findall(r"^\+\s+def\s+(\w+)\(", handlers, re.MULTILINE)
            if method in {"get", "head", "post", "put", "patch", "delete", "options"}
        ]
        self.assertEqual(methods, ["get", "head", "get", "head"])
        self.assertNotRegex(handlers, r"^\+\s+def\s+(post|put|patch|delete|options)\(")
        self.assertIn("parse_records_query", Path(ROOT / "implementations/runtime/donkeycar/host_telemetry.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
