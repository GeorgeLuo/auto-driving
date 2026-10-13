"""The public live commands against a running runtime host.

Every vehicle's host serves the same routes and the CLI finds it only by
``base_url``, so streams, viability and memory reset run here against an
in-process host; a PiCar differs only in where its ``base_url`` comes from.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.request import urlopen

from cli.automa_cli.streaming import _ViewFeed, _read_publication
from tests.shared.cli.automation_pipeline.pipeline_fixtures import VEHICLE_ID
from tests.shared.cli.invoke import invoke_automa
from tests.shared.integration.live_host import LiveHostTestCase, stage_runtime as _stage


class LiveCommandsTests(LiveHostTestCase):

    def test_the_perception_view_stays_live_while_the_decision_is_unavailable(self) -> None:
        self.observe()
        with patch("cli.automa_cli.streaming.runtime_view_dir", return_value=self.runtime_root / "view"):
            feed = _ViewFeed(VEHICLE_ID, base_url=self.host.base_url, timeout_s=3.0)
        self.addCleanup(feed.stop)
        feed.decision.refresh = Mock(side_effect=ConnectionError("decision route down"))
        publication = _read_publication(self.host.base_url, timeout_s=3.0)[0]
        feed.publish(publication)
        self.assertIn("ConnectionError: decision route down", feed.decision_line())
        with urlopen(f"{feed.url.rstrip('/')}/api/latest", timeout=5) as response:
            self.assertEqual(json.load(response)["frame"]["frame_id"], publication["frame"]["frame_id"])

    def automa(self, *args: str):
        return invoke_automa("vehicles", *args, "--id", VEHICLE_ID, runtime_root=self.runtime_root, check=False)

    def test_streams_are_live_only_while_a_session_runs(self) -> None:
        idle = self.automa("stream", "perception", "--once", "--json")
        self.assertEqual(idle.returncode, 2, idle.stdout)
        self.assertNotEqual(json.loads(idle.stdout)["status"], "live")

        self.observe()
        for step in ("perception", "memory"):
            with self.subTest(step=step):
                result = self.automa("stream", step, "--once", "--json")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["status"], "live")
        terminal = self.automa("stream", "perception", "--once", "--no-clear")
        self.assertEqual(terminal.returncode, 0, terminal.stdout + terminal.stderr)
        self.assertIn(f"host: {self.host.base_url}", terminal.stdout)

    def test_perception_info_reports_the_hosts_latest_observation(self) -> None:
        self.observe()
        result = self.automa("info", "perception", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        live = json.loads(result.stdout)["live_observation"]
        self.assertTrue(live["available"], live)
        self.assertEqual(live["base_url"], self.host.base_url)

    def test_viability_passes_while_the_host_applies_no_control(self) -> None:
        # A parked car's session is manual: the host runs its cycles and
        # applies none of their control.
        self.observe("manual")
        for step in ("perception", "memory"):
            with self.subTest(step=step):
                result = self.automa(step, "viability", "--duration-s", "1", "--no-record", "--json")
                report = json.loads(result.stdout)
                failed = [gate for gate in report["gates"] if not gate["passed"]]
                self.assertEqual(result.returncode, 0, failed)

    def test_viability_of_an_idle_host_exits_2_before_measuring(self) -> None:
        started = time.monotonic()
        result = self.automa("perception", "viability", "--no-record", "--json")
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"], "no_fresh_frames")
        self.assertIn("is not processing frames", payload["message"])
        self.assertIn(START_HOST, payload["message"])

    def test_memory_reset_is_confirmed_while_the_session_refills_memory(self) -> None:
        self.observe()
        result = self.automa("memory", "reset", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        epochs = [
            [plugin["epoch_id"] for plugin in payload[side]["plugins"]] for side in ("before", "after")
        ]
        self.assertNotEqual(*epochs)


START_HOST = f"Run: ./cli/automa vehicles automation run --id {VEHICLE_ID} --observe-only"


class NoRuntimeHostTests(unittest.TestCase):
    def test_live_commands_exit_2_and_name_the_command_that_starts_a_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            _stage(runtime_root)
            for args in (
                ("stream", "perception", "--once", "--json"),
                ("stream", "memory", "--once", "--json"),
                ("perception", "viability", "--no-record", "--json"),
                ("memory", "viability", "--no-record"),
                ("memory", "reset"),
            ):
                with self.subTest(args=args):
                    result = invoke_automa(
                        "vehicles", *args, "--id", VEHICLE_ID, runtime_root=runtime_root, check=False
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(START_HOST, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
