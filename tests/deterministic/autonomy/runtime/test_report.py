"""One host report accepts partial cycles and carries explicit cycle errors."""
from __future__ import annotations

import unittest

from autonomy.decision_cycle.cycle import CYCLE_RECORD_KEYS
from autonomy.decision_cycle.perception.interface import PerceptionText
from autonomy.runtime.report import HostReport, error_application


class HostReportTests(unittest.TestCase):
    def report(self, **fields):
        return HostReport(
            host_name="fixture", run_id="run", generation_id="decision-test",
            frame_id="frame", frame_index=0, timestamp_ms=100, published_at_ms=110,
            cycle={step: None for step in CYCLE_RECORD_KEYS},
            application=error_application("observe_only"), values={}, **fields,
        )

    def test_six_nullable_steps_and_required_status_round_trip(self):
        for fields in ({}, {"status": "error", "error": "proposal failed"}):
            with self.subTest(fields=fields):
                report = self.report(**fields)
                payload = report.to_dict()
                self.assertEqual(tuple(payload["cycle"]), CYCLE_RECORD_KEYS)
                self.assertTrue(all(value is None for value in payload["cycle"].values()))
                self.assertEqual(HostReport.from_dict(payload), report)
                for key in ("status", "error", "application"):
                    with self.subTest(missing=key), self.assertRaises(ValueError):
                        HostReport.from_dict({name: value for name, value in payload.items() if name != key})

    def test_error_status_requires_an_error_and_ok_requires_null(self):
        for fields in ({"status": "error"}, {"status": "ok", "error": "failed"}, {"status": "failed"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.report(**fields)

    def test_perception_storage_keeps_evidence_and_text_is_derived(self):
        perception = PerceptionText(
            schema="perception_text", plugin_id="fixture", status="ok", signals=(), things=(),
            measurements={"fixture": {"count": 0}}, limits=("image relative",),
        )
        stored = perception.to_dict()
        self.assertNotIn("lines", stored)
        self.assertNotIn("text", stored)
        self.assertEqual(PerceptionText.from_dict(stored).text, perception.text)
        self.assertEqual(stored["measurements"], {"fixture": {"count": 0}})
