from __future__ import annotations

import unittest
from pathlib import Path

from qca.experiments.workbench_probe import _normalize


class WorkbenchProbeNormalizeTests(unittest.TestCase):
    def test_replaces_host_paths_timings_and_embedded_durations(self) -> None:
        capture = Path("/var/folders/tmp/qca-workbench-probe-aaaa/capture")
        checkout = Path("/var/folders/tmp/qca-m008-bbbb/baseline")
        payload = {
            "run_id": "abc",
            "at_ms": 123,
            "timestamp_ms": 1000,
            "source_path": str(capture),
            "plugin_dir": str(checkout / "lab" / "plugins"),
            "summary": ["plugin_run id=frame status=ok duration_ms=0.385 signals=1"],
        }
        normalized = _normalize(payload, capture=capture, checkout=checkout)
        self.assertEqual(normalized["run_id"], "<volatile>")
        self.assertEqual(normalized["at_ms"], "<volatile>")
        self.assertEqual(normalized["timestamp_ms"], 1000)
        self.assertEqual(normalized["source_path"], "<synthetic-capture>")
        self.assertEqual(normalized["plugin_dir"], "<checkout>/lab/plugins")
        self.assertEqual(
            normalized["summary"],
            ["plugin_run id=frame status=ok duration_ms=<volatile> signals=1"],
        )


if __name__ == "__main__":
    unittest.main()
