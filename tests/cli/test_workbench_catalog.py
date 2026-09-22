from __future__ import annotations
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from cli.automa_cli.workbench import (
    PluginCatalogError,
    ReplayActionError,
    discover_plugin_catalog,
)
from tests.cli.workbench_fixtures import (
    PluginCatalogFixture,
    ImageReplayRunner,
    _make_images,
    _wait_until,
)


class WorkbenchTests(PluginCatalogFixture, unittest.TestCase):
    def test_manifest_catalog_is_recursive_deterministic_and_explicit_about_readiness(
        self,
    ) -> None:
        catalog = discover_plugin_catalog(self.plugin_root)
        self.assertEqual(
            [item.plugin_id for item in catalog.plugins],
            [
                "classical_regions",
                "fastsam",
                "floor_continuity",
                "floor_continuity_capture",
            ],
        )
        self.assertTrue(catalog.valid)
        self.assertTrue(catalog.plugins[0].ready)
        self.assertEqual(catalog.plugins[0].inputs[0]["name"], "frame")
        self.assertEqual(
            catalog.plugins[0].output["schema"],
            "perception_text_v2",
        )
        self.assertFalse(catalog.plugins[1].ready)
        self.assertIn("isolated runtime", catalog.plugins[1].unavailable_reason or "")
        self.assertEqual(
            catalog.digest, discover_plugin_catalog(self.plugin_root).digest
        )
        self.assertEqual(
            catalog.normalize_selection(["floor_continuity", "classical_regions"]),
            ("classical_regions", "floor_continuity"),
        )
        self.assertEqual(catalog.normalize_selection([]), ())

    def test_explicit_catalog_selection_runs_only_selected_plugins(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            runner = ImageReplayRunner(
                root,
                plugin_dir=self.plugin_root,
                active_plugin_ids=["classical_regions"],
                cadence_ms=0,
            )
            started = runner.start()
            state = runner.wait(10) if started["phase"] == "running" else started

        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["run_active_plugin_ids"], ["classical_regions"])
        self.assertEqual(state["active_plugin_ids"], ["classical_regions"])
        self.assertEqual(
            [run["plugin_id"] for run in state["perception"]["plugin_runs"]],
            ["classical_regions"],
        )
        self.assertNotEqual(
            state["machine_detail"]["pipeline"]["perception_algorithm"],
            "lightweight_observer",
        )

    def test_explicit_catalog_allows_raw_capture_and_live_replacement(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 3)
            runner = ImageReplayRunner(
                root,
                plugin_dir=self.plugin_root,
                cadence_ms=1000,
            )
            raw_started = runner.start()
            self.assertEqual(raw_started["phase"], "running")
            self.assertEqual(raw_started["run_active_plugin_ids"], [])
            raw_state = runner.wait(10)
            self.assertEqual(raw_state["phase"], "completed")
            self.assertEqual(raw_state["run_active_plugin_ids"], [])
            self.assertEqual(raw_state["active_plugin_ids"], [])
            self.assertEqual(raw_state["perception"]["status"], "empty")
            self.assertEqual(raw_state["perception"]["plugin_runs"], ())
            self.assertEqual(raw_state["perception"]["things"], ())

            runner.dispatch(
                "select_plugins",
                run_id=raw_started["run_id"],
                active_plugin_ids=["classical_regions"],
            )
            started = runner.start()
            run_id = started["run_id"]
            _wait_until(lambda: len(runner.state()["timeline"]) >= 1)
            selected = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=["floor_continuity"],
            )
            self.assertEqual(selected["phase"], "running")
            self.assertEqual(selected["run_active_plugin_ids"], ["floor_continuity"])
            paused = runner.dispatch("pause", run_id=run_id)
            self.assertEqual(paused["phase"], "paused")
            first_detail = runner.frame_detail(
                paused["timeline"][0]["frame"]["frame_id"], run_id=run_id
            )
            self.assertEqual(
                [run["plugin_id"] for run in first_detail["perception"]["plugin_runs"]],
                ["classical_regions"],
            )
            with self.assertRaises(ReplayActionError):
                runner.dispatch(
                    "select_plugins",
                    run_id=run_id,
                    active_plugin_ids=["unknown"],
                )
            self.assertEqual(
                runner.state()["run_active_plugin_ids"], ["floor_continuity"]
            )
            stepped = runner.dispatch("step", run_id=run_id)
            second_detail = runner.frame_detail(
                stepped["timeline"][1]["frame"]["frame_id"], run_id=run_id
            )
            self.assertEqual(
                [
                    run["plugin_id"]
                    for run in second_detail["perception"]["plugin_runs"]
                ],
                ["floor_continuity"],
            )
            runner.dispatch("cancel", run_id=run_id)

    def test_paused_plugin_toggle_reprocesses_current_frame_evidence(self) -> None:
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
            paused = runner.dispatch("pause", run_id=run_id)
            position = paused["position"]
            timeline_len = len(paused["timeline"])
            self.assertEqual(
                [run["plugin_id"] for run in paused["perception"]["plugin_runs"]],
                ["classical_regions"],
            )

            both = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=["classical_regions", "floor_continuity"],
            )
            self.assertEqual(both["phase"], "paused")
            self.assertEqual(both["position"], position)
            self.assertEqual(len(both["timeline"]), timeline_len)
            self.assertEqual(
                [run["plugin_id"] for run in both["perception"]["plugin_runs"]],
                ["classical_regions", "floor_continuity"],
            )

            none = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=[],
            )
            self.assertEqual(none["phase"], "paused")
            self.assertEqual(list(none["perception"]["plugin_runs"] or ()), [])

            one = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=["floor_continuity"],
            )
            self.assertEqual(one["phase"], "paused")
            self.assertEqual(one["position"], position)
            self.assertEqual(
                [run["plugin_id"] for run in one["perception"]["plugin_runs"]],
                ["floor_continuity"],
            )
            runner.dispatch("cancel", run_id=run_id)

    def test_catalog_rejects_unavailable_and_duplicate_selection(self) -> None:
        catalog = discover_plugin_catalog(self.plugin_root)
        with self.assertRaises(PluginCatalogError):
            catalog.normalize_selection(["fastsam"])
        with self.assertRaises(PluginCatalogError):
            catalog.normalize_selection(["classical_regions", "classical_regions"])
