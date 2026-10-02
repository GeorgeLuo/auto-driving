from __future__ import annotations
import json
import unittest
from functools import partial
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from cli.automa_cli.workbench import WorkbenchServer
from tests.cli.workbench_fixtures import (
    FixtureMapper,
    ImageReplayRunner,
    _wait_until,
    image_source,
    post_action,
    serve_workbench,
    write_manifest,
)


class WorkbenchTests(unittest.TestCase):
    def test_loopback_api_accepts_realtime_pace_selection(self) -> None:
        with image_source(2) as root:
            write_manifest(root, {
                "source_id": "api-timed.fixture",
                "frames": [
                    {
                        "frame_id": "first",
                        "timestamp_ms": 0,
                        "image_path": "frame_00.png",
                    },
                    {
                        "frame_id": "second",
                        "timestamp_ms": 20,
                        "image_path": "frame_01.png",
                    },
                ],
            })
            runner = ImageReplayRunner(
                cadence_ms=5000,
                mapper_factory=FixtureMapper,
            )
            base = serve_workbench(self, runner)

            post = partial(post_action, base, timeout=3)

            started = post(
                {
                    "action": "start",
                    "source_dir": str(root),
                    "pace": "realtime",
                    "cadence_ms": 5000,
                }
            )
            self.assertEqual(started["state"]["controls"]["pace"], "realtime")
            self.assertEqual(runner.wait(3)["phase"], "completed")

    def test_loopback_api_exposes_and_applies_plugin_selection(self) -> None:
        with image_source(1) as root:
            runner = ImageReplayRunner(cadence_ms=0)
            base = serve_workbench(self, runner)

            post = partial(post_action, base, timeout=2)

            catalog = runner.state()["plugin_catalog"]
            self.assertIn("classical_regions", [item["id"] for item in catalog["plugins"]])
            raw_selected = post({"action": "select_plugins", "active_plugin_ids": []})
            self.assertEqual(raw_selected["state"]["active_plugin_ids"], [])
            raw_started = post(
                {
                    "action": "start",
                    "source_dir": str(root),
                    "cadence_ms": 0,
                }
            )
            raw_state = runner.wait(5)
            self.assertEqual(raw_started["state"]["run_active_plugin_ids"], [])
            self.assertEqual(raw_state["phase"], "completed")
            self.assertEqual(raw_state["perception"]["status"], "empty")
            self.assertEqual(raw_state["perception"]["plugin_runs"], ())
            self.assertEqual(raw_state["perception"]["things"], ())
            selected = post(
                {
                    "action": "select_plugins",
                    "run_id": raw_started["state"]["run_id"],
                    "active_plugin_ids": ["classical_regions"],
                }
            )
            self.assertEqual(
                selected["state"]["active_plugin_ids"], ["classical_regions"]
            )
            started = post(
                {
                    "action": "start",
                    "source_dir": str(root),
                    "cadence_ms": 0,
                }
            )
            state = runner.wait(5)

        self.assertEqual(
            started["state"]["run_active_plugin_ids"], ["classical_regions"]
        )
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(
            [item["plugin_id"] for item in state["perception"]["plugin_runs"]],
            ["classical_regions"],
        )

    def test_removed_actions_and_plugin_lists_on_other_actions_are_rejected(self) -> None:
        with image_source(1) as root:
            runner = ImageReplayRunner(cadence_ms=0)
            base = serve_workbench(self, runner)
            post = partial(post_action, base, timeout=2)

            for body in (
                {"action": "set_plugins", "active_plugin_ids": []},
                {"action": "cancel"},
                {"action": "start", "source_dir": str(root), "active_plugin_ids": []},
                {"action": "validate", "source_dir": str(root), "active_plugin_ids": []},
            ):
                with self.assertRaises(HTTPError, msg=str(body)) as caught:
                    post(body)
                self.assertEqual(caught.exception.code, 400)

    def test_loopback_api_selection_reprocesses_the_displayed_frame(self) -> None:
        with image_source(3) as root:
            runner = ImageReplayRunner(cadence_ms=30000)
            base = serve_workbench(self, runner)
            post = partial(post_action, base, timeout=10)

            post(
                {
                    "action": "select_plugins",
                    "active_plugin_ids": ["classical_regions"],
                }
            )
            started = post(
                {
                    "action": "start",
                    "source_dir": str(root),
                    "cadence_ms": 30000,
                }
            )
            run_id = started["state"]["run_id"]
            _wait_until(lambda: runner.state()["position"] == 1)
            first_id = runner.state()["timeline"][0]["frame"]["frame_id"]
            selected = post(
                {
                    "action": "select_plugins",
                    "run_id": run_id,
                    "active_plugin_ids": ["floor_continuity"],
                }
            )
            self.assertEqual(
                selected["state"]["run_active_plugin_ids"], ["floor_continuity"]
            )
            # The running replay picks the displayed frame up without waiting
            # out the cadence.
            _wait_until(lambda: runner.state()["position"] == 1 and runner.state()["timeline"])
            reprocessed = runner.frame_detail(first_id, run_id=run_id)
            post({"action": "reset", "run_id": run_id})

        self.assertEqual(
            [run["plugin_id"] for run in reprocessed["perception"]["plugin_runs"]],
            ["floor_continuity"],
        )

    def test_running_empty_selection_reprocesses_the_displayed_frame(self) -> None:
        with image_source(3) as root:
            runner = ImageReplayRunner(
                root,
                cadence_ms=30000,
            )
            runner.dispatch(
                "select_plugins",
                active_plugin_ids=["classical_regions"],
            )
            started = runner.start()
            run_id = started["run_id"]
            _wait_until(lambda: runner.state()["position"] == 1)
            first_id = runner.state()["timeline"][0]["frame"]["frame_id"]
            selected = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=[],
            )
            self.assertEqual(selected["phase"], "running")
            self.assertEqual(selected["run_active_plugin_ids"], [])
            _wait_until(lambda: runner.state()["position"] == 1 and runner.state()["timeline"])
            reprocessed = runner.frame_detail(first_id, run_id=run_id)
            self.assertEqual(list(reprocessed["perception"]["plugin_runs"] or ()), [])
            self.assertEqual(reprocessed["perception"]["status"], "empty")
            runner.dispatch("reset", run_id=run_id)

    def test_loopback_api_persists_after_terminal_state_and_rejects_raw_argv(
        self,
    ) -> None:
        with image_source(1) as root:
            runner = ImageReplayRunner(cadence_ms=0)
            server = WorkbenchServer(runner).start()
            self.addCleanup(server.stop)
            base = server.url
            self.assertIsNotNone(base)

            served = urlopen(base, timeout=2)
            self.assertEqual(getattr(served, "status", 200), 200)

            start_body = json.dumps(
                {"action": "start", "source_dir": str(root), "cadence_ms": 0}
            ).encode("utf-8")
            start = json.loads(
                urlopen(
                    Request(
                        base + "api/action",
                        data=start_body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=2,
                ).read()
            )
            run_id = start["state"]["run_id"]
            state = runner.wait(5)
            self.assertEqual(state["phase"], "completed")

            health = json.loads(urlopen(base + "api/health", timeout=2).read())
            self.assertTrue(health["available"])
            self.assertTrue(health["persistent_across_terminal_state"])
            latest = json.loads(urlopen(base + "api/state", timeout=2).read())
            self.assertEqual(latest["phase"], "completed")
            frame_id = state["timeline"][0]["frame"]["frame_id"]
            query = urlencode({"run_id": run_id, "frame_id": frame_id})
            detail = json.loads(
                urlopen(base + "api/frame-detail?" + query, timeout=2).read()
            )
            self.assertEqual(detail["frame"]["frame_id"], frame_id)
            self.assertEqual(detail["perception"]["status"], "ok")
            self.assertEqual(detail["memory"]["health"], "healthy")
            frame = urlopen(
                base + "api/frame?" + query,
                timeout=2,
            )
            self.assertEqual(frame.status, 200)
            self.assertTrue(frame.read())

            second_start_body = json.dumps(
                {"action": "start", "source_dir": str(root), "cadence_ms": 0}
            ).encode("utf-8")
            second_start = json.loads(
                urlopen(
                    Request(
                        base + "api/action",
                        data=second_start_body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=2,
                ).read()
            )
            self.assertNotEqual(second_start["state"]["run_id"], run_id)
            self.assertEqual(runner.wait(5)["phase"], "completed")
            with self.assertRaises(HTTPError) as stale_detail:
                urlopen(base + "api/frame-detail?" + query, timeout=2)
            self.assertEqual(stale_detail.exception.code, 409)

            bad_body = json.dumps({"action": "start", "argv": ["--unsafe"]}).encode(
                "utf-8"
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(
                    Request(
                        base + "api/action",
                        data=bad_body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=2,
                )
            self.assertEqual(error.exception.code, 400)
            bad_payload = json.loads(error.exception.read())
            self.assertEqual(bad_payload["boundary"], "input")
            server.stop()
            self.assertIsNone(runner.state()["source"])
            self.assertEqual(runner.state()["timeline"], [])
