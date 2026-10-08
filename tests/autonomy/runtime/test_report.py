from __future__ import annotations

import json
import unittest

from autonomy.runtime.report import REPORT_SCHEMA, VehicleReport


def _report(**overrides: object) -> dict:
    payload = {
        "schema": REPORT_SCHEMA,
        "vehicle_id": "chase-sim-chaser",
        "run_id": "run-1",
        "generation_id": "generation-0123456789abcdef",
        "frame_id": "chase_frame_000001",
        "frame_index": 1,
        "timestamp_ms": 1_700_000_000_000,
        "published_at_ms": 1_700_000_000_040,
        "cycle": {
            "proposal": {"frame_id": "chase_frame_000001"},
            "plan": {"selected": "follow"},
            "action": {"status": "ok"},
        },
        "application": {
            "applied": True,
            "mode": "autonomy",
            "reason": "follow",
            "steering": 0.25,
            "throttle": 0.5,
        },
        "values": {},
    }
    payload.update(overrides)
    return payload


class VehicleReportTests(unittest.TestCase):
    def test_round_trip_keeps_the_required_fields(self) -> None:
        payload = _report(
            values={"worker_pid": 42, "source_id": "picar-front"},
        )

        report = VehicleReport.from_dict(payload)

        self.assertEqual(report.to_dict(), payload)
        json.dumps(report.to_dict())

    def test_values_may_be_empty_and_are_detached(self) -> None:
        payload = _report()
        report = VehicleReport.from_dict(payload)

        payload["values"]["worker_pid"] = 7
        payload["cycle"]["action"]["status"] = "changed"

        self.assertEqual(report.values, {})
        self.assertEqual(report.cycle["action"], {"status": "ok"})

    def test_missing_or_extra_report_keys_are_rejected(self) -> None:
        missing = _report()
        del missing["application"]
        with self.assertRaisesRegex(ValueError, "application"):
            VehicleReport.from_dict(missing)

        extra = _report()
        extra["worker_pid"] = 1
        with self.assertRaisesRegex(ValueError, "worker_pid"):
            VehicleReport.from_dict(extra)

    def test_application_has_no_optional_fields(self) -> None:
        application = _report()["application"]
        del application["applied"]
        with self.assertRaisesRegex(ValueError, "applied"):
            VehicleReport.from_dict(_report(application=application))

        application = _report()["application"]
        application["receipt"] = {"boundary": "ws"}
        with self.assertRaisesRegex(ValueError, "receipt"):
            VehicleReport.from_dict(_report(application=application))

    def test_application_mode_is_the_execution_mode(self) -> None:
        application = _report()["application"]
        application["mode"] = "local"
        with self.assertRaisesRegex(ValueError, "execution mode"):
            VehicleReport.from_dict(_report(application=application))

    def test_command_must_be_a_finite_unit_interval(self) -> None:
        for field, value in (
            ("steering", True),
            ("steering", 1.5),
            ("throttle", float("nan")),
        ):
            application = _report()["application"]
            application[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ValueError, field):
                    VehicleReport.from_dict(_report(application=application))

    def test_cycle_is_proposal_plan_and_action(self) -> None:
        cycle = _report()["cycle"]
        del cycle["plan"]
        with self.assertRaisesRegex(ValueError, "plan"):
            VehicleReport.from_dict(_report(cycle=cycle))

        cycle = _report()["cycle"]
        cycle["memory"] = {}
        with self.assertRaisesRegex(ValueError, "memory"):
            VehicleReport.from_dict(_report(cycle=cycle))

    def test_values_reject_non_json_data(self) -> None:
        with self.assertRaisesRegex(ValueError, "values"):
            VehicleReport.from_dict(_report(values={"clock": object()}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
