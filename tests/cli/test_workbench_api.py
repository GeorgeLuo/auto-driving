from __future__ import annotations
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from cli.automa_cli.workbench import WorkbenchServer
from tests.cli.workbench_fixtures import (
    PluginCatalogFixture,
    FixtureMapper,
    ImageReplayRunner,
    _make_images,
    _wait_until,
)


class WorkbenchTests(PluginCatalogFixture, unittest.TestCase):
    def test_loopback_api_accepts_realtime_pace_selection(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 2)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
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
                    }
                ),
                encoding="utf-8",
            )
            runner = ImageReplayRunner(
                cadence_ms=5000,
                mapper_factory=FixtureMapper,
            )
            server = WorkbenchServer(runner).start()
            self.addCleanup(server.stop)
            base = server.url
            self.assertIsNotNone(base)

            def post(payload: dict[str, object]) -> dict[str, object]:
                body = json.dumps(payload).encode("utf-8")
                return json.loads(
                    urlopen(
                        Request(
                            base + "api/action",
                            data=body,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                        timeout=3,
                    ).read()
                )

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
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            runner = ImageReplayRunner(cadence_ms=0)
            server = WorkbenchServer(runner).start()
            self.addCleanup(server.stop)
            base = server.url
            self.assertIsNotNone(base)

            plugin_root = str(self.plugin_root.resolve())

            def post(payload: dict[str, object]) -> dict[str, object]:
                body = json.dumps(payload).encode("utf-8")
                return json.loads(
                    urlopen(
                        Request(
                            base + "api/action",
                            data=body,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                        timeout=2,
                    ).read()
                )

            inspected = post({"action": "refresh_plugins", "plugin_dir": plugin_root})
            catalog = inspected["state"]["plugin_catalog"]
            self.assertEqual(
                [item["id"] for item in catalog["plugins"]],
                [
                    "classical_regions",
                    "fastsam",
                    "floor_continuity",
                    "floor_continuity_capture",
                ],
            )
            raw_selected = post({"action": "select_plugins", "active_plugin_ids": []})
            self.assertEqual(raw_selected["state"]["active_plugin_ids"], [])
            raw_started = post(
                {
                    "action": "start",
                    "source_dir": str(root),
                    "cadence_ms": 0,
                    "plugin_dir": plugin_root,
                    "active_plugin_ids": [],
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
                    "plugin_dir": plugin_root,
                    "active_plugin_ids": ["classical_regions"],
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

    def test_loopback_api_allows_live_plugin_selection_at_frame_boundary(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 3)
            runner = ImageReplayRunner(cadence_ms=5000)
            server = WorkbenchServer(runner).start()
            self.addCleanup(server.stop)
            base = server.url
            self.assertIsNotNone(base)
            plugin_root = str(self.plugin_root.resolve())

            def post(payload: dict[str, object]) -> dict[str, object]:
                body = json.dumps(payload).encode("utf-8")
                return json.loads(
                    urlopen(
                        Request(
                            base + "api/action",
                            data=body,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                        timeout=10,
                    ).read()
                )

            post({"action": "refresh_plugins", "plugin_dir": plugin_root})
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
                    "plugin_dir": plugin_root,
                    "active_plugin_ids": ["classical_regions"],
                    "cadence_ms": 5000,
                }
            )
            run_id = started["state"]["run_id"]
            _wait_until(lambda: len(runner.state()["timeline"]) >= 1)
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
            paused = post({"action": "pause", "run_id": run_id})
            self.assertEqual(paused["state"]["phase"], "paused")
            stepped = post({"action": "step", "run_id": run_id})
            first_id = stepped["state"]["timeline"][0]["frame"]["frame_id"]
            second_id = stepped["state"]["timeline"][1]["frame"]["frame_id"]
            first_detail = runner.frame_detail(first_id, run_id=run_id)
            second_detail = runner.frame_detail(second_id, run_id=run_id)

        self.assertEqual(
            [run["plugin_id"] for run in first_detail["perception"]["plugin_runs"]],
            ["classical_regions"],
        )
        self.assertEqual(
            [run["plugin_id"] for run in second_detail["perception"]["plugin_runs"]],
            ["floor_continuity"],
        )
        self.assertEqual(stepped["state"]["phase"], "paused")
        post({"action": "cancel", "run_id": run_id})

    def test_running_empty_selection_keeps_current_frame_perception(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 3)
            runner = ImageReplayRunner(
                root,
                plugin_dir=self.plugin_root,
                cadence_ms=5000,
            )
            runner.dispatch(
                "select_plugins",
                active_plugin_ids=["classical_regions"],
            )
            started = runner.start()
            run_id = started["run_id"]
            _wait_until(lambda: runner.state().get("current_frame") is not None)
            before = runner.state()
            self.assertEqual(
                [run["plugin_id"] for run in before["perception"]["plugin_runs"]],
                ["classical_regions"],
            )
            selected = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=[],
            )
            self.assertEqual(selected["phase"], "running")
            self.assertEqual(selected["run_active_plugin_ids"], [])
            self.assertEqual(
                [run["plugin_id"] for run in selected["perception"]["plugin_runs"]],
                ["classical_regions"],
            )
            paused = runner.dispatch("pause", run_id=run_id)
            self.assertEqual(
                [run["plugin_id"] for run in paused["perception"]["plugin_runs"]],
                ["classical_regions"],
            )
            stepped = runner.dispatch("step", run_id=run_id)
            self.assertEqual(list(stepped["perception"]["plugin_runs"] or ()), [])
            self.assertEqual(stepped["perception"]["status"], "empty")
            runner.dispatch("cancel", run_id=run_id)

    def test_loopback_api_persists_after_terminal_state_and_rejects_raw_argv(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
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
