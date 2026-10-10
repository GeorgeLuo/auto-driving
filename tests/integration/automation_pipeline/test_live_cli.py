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
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import urlopen

from autonomy.runtime.client import RuntimeClient
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.bundles import controller_bundle_paths
from cli.automa_cli.streaming import _read_publication, _ViewFeed, probe_live_memory
from implementations.decision_cycle.catalog import packaged_activation
from tests.integration.automation_pipeline.pipeline_fixtures import (
    VEHICLE_ID,
    _write_activations,
    chase_runtime,
    staged_runners,
)
from tests.support.cli_runner import run_automa, start_automa

START_HOST = f"Run: ./cli/automa vehicles automation run --id {VEHICLE_ID} --observe-only"


def _wait(predicate, *, timeout_s: float = 10.0, message: str):
    deadline = time.monotonic() + timeout_s
    while not (value := predicate()):
        if time.monotonic() > deadline:
            raise AssertionError(message)
        time.sleep(0.05)
    return value


def _stage(runtime_root: Path) -> None:
    bundle = controller_bundle_paths(runtime_root / VEHICLE_ID)
    _write_activations(bundle)
    path = Path(bundle["memory_runtime_dir"]) / "active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packaged_activation("memory", ["bounded_evidence"]).to_payload()), encoding="utf-8")


class LiveCommandsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime_root = Path(tmp.name) / "vehicles"
        _stage(self.runtime_root)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(staged_runners())
        self.host = stack.enter_context(chase_runtime(self.runtime_root))
        self.client = RuntimeClient(self.host.base_url, timeout_s=3.0)

    def automa(self, *args: str):
        return run_automa("vehicles", *args, "--id", VEHICLE_ID, runtime_root=self.runtime_root, check=False)

    def observe(self, mode: str = "observe_only") -> None:
        self.client.start(RunConfiguration(mode=mode, interval_s=0.1, num_decisions=0))
        self.addCleanup(self.client.stop)
        _wait(
            lambda: any(plugin["record_count"] for plugin in probe_live_memory(vehicle_id=VEHICLE_ID)["plugins"]),
            message="the session never filled memory",
        )

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
            lambda: next((line.split()[2] for line in lines if line.startswith("decision view: http")), None),
            timeout_s=20, message="the proposal stream printed no decision view",
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
