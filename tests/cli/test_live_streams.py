from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from cli.automa_cli import streaming
from tests.support.cli_runner import run_automa

LIVE_SCHEMAS = {
    "perception": streaming.PERCEPTION_LIVE_SCHEMA,
    "memory": streaming.MEMORY_LIVE_SCHEMA,
}


class LiveStreamDiscoveryTests(unittest.TestCase):
    def test_both_cli_help_pages_describe_the_json_failure_contract(self) -> None:
        for step in ("perception", "memory"):
            with self.subTest(step=step):
                result = run_automa("vehicles", "stream", step, "--help")
                help_text = " ".join(result.stdout.split())
                self.assertIn(f"exit 2 unless {step} is live", help_text)
                self.assertIn(
                    "discovery failures emit an unavailable probe and exit 2", help_text
                )
                self.assertIn(
                    f"one {LIVE_SCHEMAS[step]} JSON probe per refresh in place of the "
                    "terminal view and local view",
                    help_text,
                )

    def test_both_cli_json_streams_report_an_undiscoverable_vehicle_as_one_probe(
        self,
    ) -> None:
        for step in ("perception", "memory"):
            for once in (False, True):
                with self.subTest(step=step, once=once):
                    result = run_automa(
                        "vehicles",
                        "stream",
                        step,
                        "--id",
                        "unknown-live-stream",
                        "--json",
                        *(("--once",) if once else ()),
                        check=False,
                    )
                    self.assertEqual(result.returncode, 2)
                    probe = json.loads(result.stdout)
                    self.assertEqual(len(result.stdout.splitlines()), 1)
                    self.assertEqual(probe["schema"], LIVE_SCHEMAS[step])
                    self.assertEqual(probe["vehicle_id"], "unknown-live-stream")
                    self.assertEqual(probe["status"], "unavailable")
                    self.assertIn("was not found", probe["error"])
                    self.assertIsInstance(probe["probed_at_ms"], int)
                    self.assertEqual(result.stderr, "")

    def test_json_discovery_failures_return_or_print_the_same_probe(self) -> None:
        for step, module, stream_vehicle in (
            ("perception", streaming, streaming.stream_vehicle_perception),
            ("memory", streaming, streaming.stream_vehicle_memory),
        ):
            for once in (False, True):
                for output in (None, io.StringIO()):
                    with (
                        self.subTest(step=step, once=once, output=output),
                        patch.object(
                            module,
                            "discover_active_vehicles",
                            return_value={"vehicles": []},
                        ),
                    ):
                        result = stream_vehicle(
                            vehicle_id="missing",
                            once=once,
                            json_output=True,
                            output=output,
                        )
                        self.assertEqual(result.exit_code, 2)
                        text = (
                            output.getvalue() if output is not None else result.message
                        )
                        probe = json.loads(text)
                        self.assertEqual(probe["status"], "unavailable")
                        if output is not None:
                            self.assertEqual(result.message, "")
                        self.assertNotIn("\033", text)

    def test_perception_json_reports_an_unsupported_provider_as_a_probe(self) -> None:
        vehicle = {"vehicle_id": "unsupported", "provider": "other"}
        with patch.object(
            streaming, "discover_active_vehicles", return_value={"vehicles": [vehicle]}
        ):
            result = streaming.stream_vehicle_perception(
                vehicle_id="unsupported", once=True, json_output=True
            )
        self.assertEqual(result.exit_code, 2)
        probe = json.loads(result.message)
        self.assertEqual(probe["status"], "unavailable")
        self.assertIn("other", probe["error"])
