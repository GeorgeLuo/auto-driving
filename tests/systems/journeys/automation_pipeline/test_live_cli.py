"""The public live commands against a running runtime host.

Every vehicle's host serves the same routes and the CLI finds it only by
``base_url``, so streams, viability and memory reset run here against an
in-process host; a PiCar differs only in where its ``base_url`` comes from.
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from tests.shared.cli.automation_pipeline.pipeline_fixtures import VEHICLE_ID
from tests.shared.integration.live_host import LiveHostTestCase, stage_runtime as _stage, wait_until as _wait
from tests.support.cli_runner import run_automa, start_automa

START_HOST = f"Run: ./cli/automa vehicles automation run --id {VEHICLE_ID} --observe-only"


class LiveCommandsTests(LiveHostTestCase):
    def automa(self, *args: str):
        return run_automa("vehicles", *args, "--id", VEHICLE_ID, runtime_root=self.runtime_root, check=False)

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

    def test_a_running_step_stream_hosts_the_decision_view(self) -> None:
        # How each proposal arose is on the decision page: the host's latest
        # decision beside its frame and the evidence its proposals read.
        self.observe()
        stream = start_automa(
            "vehicles", "stream", "proposal", "--id", VEHICLE_ID, "--no-clear", runtime_root=self.runtime_root,
        )
        self.addCleanup(stream.stdout.close)
        self.addCleanup(stream.wait, 10)
        self.addCleanup(stream.terminate)
        lines: list[str] = []

        def collect() -> None:
            for line in stream.stdout:
                lines.append(line.rstrip("\n"))

        threading.Thread(target=collect, daemon=True).start()
        page = _wait(
            lambda: next((line.split()[2] for line in lines if line.startswith("action view: http")), None),
            timeout_s=20, message="the proposal stream printed no action view",
        )
        with urlopen(page, timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), "text/html")
        view_url, _, generation = page.partition("/decision?")

        def latest():
            try:
                with urlopen(f"{view_url}/api/decision/latest?{generation}", timeout=5) as response:
                    return json.load(response)
            except HTTPError:  # warming, or the newest cycle expired between refreshes
                return None

        decision = _wait(latest, message="the decision view never served a current decision")
        self.assertEqual(decision["decision"]["frame_id"], decision["current_image"]["frame_id"])
        self.assertIsNotNone(decision["decision"]["cycle"]["proposal"])
        self.assertIn("evidence", decision)

    def test_a_stream_poll_during_cycle_publication_receives_a_coherent_decision(self) -> None:
        self.observe()
        loop = self.host.loop
        publishing = threading.Event()
        release = threading.Event()
        reading = threading.Event()
        returned = threading.Event()
        capture = loop._capture_decision_publication
        publish = loop.publish_decision_latest

        def pause_publication(**kwargs):
            if not publishing.is_set():
                publishing.set()
                if not release.wait(10):
                    raise TimeoutError("the test never released the completed cycle")
            return capture(**kwargs)

        def read_publication(**kwargs):
            reading.set()
            result = publish(**kwargs)
            returned.set()
            return result

        with patch.object(loop, "_capture_decision_publication", side_effect=pause_publication), \
                patch.object(loop, "publish_decision_latest", side_effect=read_publication):
            try:
                self.assertTrue(publishing.wait(10), "the host never completed another cycle")
                stream = start_automa(
                    "vehicles", "stream", "proposal", "--id", VEHICLE_ID,
                    "--once", "--json", runtime_root=self.runtime_root,
                )
                self.addCleanup(stream.stdout.close)
                self.addCleanup(stream.wait, 10)
                self.addCleanup(stream.terminate)
                self.assertTrue(reading.wait(10), "the CLI never polled the decision route")
                self.assertFalse(returned.wait(0.1), "the decision route exposed a partly published cycle")
            finally:
                release.set()
            output, _ = stream.communicate(timeout=10)
        self.assertEqual(stream.returncode, 0, output)
        decision = json.loads(output)
        self.assertEqual(decision["frame_id"], decision["record"]["frame_id"])
        self.assertEqual(decision["plugins"], ["avoid_recent_obstruction"])


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
                    result = run_automa(
                        "vehicles", *args, "--id", VEHICLE_ID, runtime_root=runtime_root, check=False
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(START_HOST, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
