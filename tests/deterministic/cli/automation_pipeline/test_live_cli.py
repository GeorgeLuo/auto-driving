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
from urllib.error import HTTPError
from urllib.request import urlopen

from cli.automa_cli.host_publications import fetch_observation_sample
from cli.automa_cli.streaming import _ViewFeed, _read_publication
from tests.shared.cli.automation_pipeline.pipeline_fixtures import HOST_NAME
from tests.shared.cli.invoke import invoke_automa
from tests.shared.integration.live_host import LiveHostTestCase, stage_runtime as _stage, wait_until
from tests.support.fake_hosts import closed_url
from tests.support.runtime_fixtures import write_host_fixture


class LiveCommandsTests(LiveHostTestCase):

    def test_the_perception_view_stays_live_while_the_decision_is_unavailable(self) -> None:
        self.observe()
        with patch("cli.automa_cli.streaming.runtime_view_dir", return_value=self.runtime_root / "view"):
            feed = _ViewFeed(HOST_NAME, base_url=self.host.base_url, timeout_s=3.0)
        self.addCleanup(feed.stop)
        feed.decision.refresh = Mock(side_effect=ConnectionError("decision route down"))
        publication = _read_publication(self.host.base_url, timeout_s=3.0)[0]
        samples = []

        def sample(*args, **kwargs):
            pair = fetch_observation_sample(*args, **kwargs)
            samples.append(pair)
            return pair

        with patch("cli.automa_cli.streaming.fetch_observation_sample", side_effect=sample):
            feed.publish(publication)
        self.assertIn("ConnectionError: decision route down", feed.decision_line())
        with urlopen(f"{feed.url.rstrip('/')}/api/latest", timeout=5) as response:
            latest = json.load(response)
        # The host may advance after the terminal's publication. The view must
        # use its transaction's findings and image even when decision is down.
        matched, jpeg = samples[0]
        self.assertEqual(latest["frame"]["frame_id"], matched["frame"]["frame_id"])
        self.assertEqual(latest["overlay"]["source_frame_id"], latest["frame"]["frame_id"])
        self.assertEqual(latest["overlay"]["status"], "current")
        with urlopen(f"{feed.url.rstrip('/')}{latest['frame']['url']}", timeout=5) as response:
            self.assertEqual(response.read(), jpeg)

    def automa(self, *args: str):
        return invoke_automa("hosts", *args, "--name", HOST_NAME, runtime_root=self.runtime_root, check=False)

    def test_streams_are_live_only_while_a_session_runs(self) -> None:
        idle = self.automa("stream", "perception", "--once", "--json")
        self.assertEqual(idle.returncode, 2, idle.stdout)
        self.assertNotEqual(json.loads(idle.stdout)["status"], "live")

        self.observe()
        for step in ("perception", "memory"):
            with self.subTest(step=step):
                result = self.automa("stream", step, "--once", "--json")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                live = json.loads(result.stdout)
                self.assertEqual(live["status"], "live")
                if step == "memory":
                    # The memory stream reads the live step probe. Perception's
                    # stream is the observation publication.
                    self.assertIsInstance(live["last_duration_ms"], float)
        terminal = self.automa("stream", "perception", "--once", "--no-clear")
        self.assertEqual(terminal.returncode, 0, terminal.stdout + terminal.stderr)
        self.assertIn(f"host: {self.host.base_url}", terminal.stdout)

    def viewer(self, url: str):
        return invoke_automa("hosts", "viewer", "--host-url", url, runtime_root=self.runtime_root, check=False)

    def view_once(self, url: str):
        """The viewer's result, its view's URL, and the view's /api/latest status and body
        after one refresh from the host, when Ctrl-C stops it."""
        opened, served = [], []

        def interrupt(*args):
            try:
                with urlopen(f"{opened[0].rstrip('/')}/api/latest", timeout=5) as response:
                    served.append((response.status, json.load(response)))
            except HTTPError as exc:
                served.append((exc.code, json.load(exc)))
            raise KeyboardInterrupt

        with patch("cli.automa_cli.streaming.open_view_message",
                   side_effect=lambda url: opened.append(url) or f"Browser opened: {url}"), \
             patch("cli.automa_cli.streaming._pause_s", side_effect=interrupt):
            result = self.viewer(url)
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        return result, opened[0], served[0]

    def test_the_viewer_serves_the_host_at_its_status_url(self) -> None:
        self.observe()
        result, view_url, (status, latest) = self.view_once(self.host.base_url + "/")

        self.assertEqual(result.stdout,
                         f"Viewing host {HOST_NAME} at {self.host.base_url}\nBrowser opened: {view_url}\n")
        self.assertEqual((status, latest["overlay"]["status"]), (200, "current"))
        with self.assertRaises(OSError):
            urlopen(view_url, timeout=1)

    def test_the_viewer_says_why_a_host_whose_run_stopped_is_not_live(self) -> None:
        self.observe()
        self.client.stop()
        wait_until(lambda: _read_publication(self.host.base_url, timeout_s=3.0)[0]["health"] == "stale",
                   message="the stopped host's last frame never went stale")
        result, view_url, served = self.view_once(self.host.base_url)

        reason = "The observation publication health is 'stale'."
        self.assertEqual(result.stdout, f"Viewing host {HOST_NAME} at {self.host.base_url}\n"
                                        f"Not live: {reason}\nBrowser opened: {view_url}\n")
        self.assertEqual(served, (503, {"error": reason}))

    def test_the_viewer_refuses_a_url_without_an_answering_host(self) -> None:
        unknown = self.viewer("http://localhost:5050")
        self.assertEqual((unknown.returncode, unknown.stdout),
                         (2, f"No host at http://localhost:5050. Hosts: {HOST_NAME} at {self.host.base_url}.\n"))

        url = closed_url()
        write_host_fixture(self.runtime_root, "piracer", provider="picar", url=url)
        with patch("cli.automa_cli.streaming.open_view_message") as open_view:
            silent = self.viewer(url)
        self.assertEqual(silent.returncode, 2, silent.stdout + silent.stderr)
        self.assertTrue(silent.stdout.startswith(f"Cannot view host piracer at {url}: "), silent.stdout)
        open_view.assert_not_called()

    def test_info_reports_the_hosts_live_state(self) -> None:
        self.observe()
        result = self.automa("info", "perception", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        live = json.loads(result.stdout)["live_observation"]
        self.assertTrue(live["available"], live)
        self.assertEqual(live["base_url"], self.host.base_url)
        plan = self.automa("info", "plan", "--json")
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertIsInstance(json.loads(plan.stdout)["live"]["last_duration_ms"], float)

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
        self.assertIn("start a run if none is active", payload["message"])

    def test_memory_reset_is_confirmed_while_the_session_refills_memory(self) -> None:
        self.observe()
        result = self.automa("memory", "reset", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        epochs = [
            [plugin["epoch_id"] for plugin in payload[side]["plugins"]] for side in ("before", "after")
        ]
        self.assertNotEqual(*epochs)


START_HOST = "Host 'chase-sim-chaser' is not running."


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
                        "hosts", *args, "--name", HOST_NAME, runtime_root=runtime_root, check=False
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(START_HOST, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
