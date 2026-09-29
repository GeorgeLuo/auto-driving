from __future__ import annotations
import json
import unittest
from pathlib import Path
from autonomy.perception import PerceptionRequest
from autonomy.vehicle import SensorSnapshot
from implementations.perception.catalog import PERCEPTION_PLUGIN_SPECS
from cli.automa_cli.workbench_plugins import packaged_plugin_catalog
from cli.automa_cli.workbench import (
    PluginCatalogError,
    ReplayActionError,
    discover_plugin_catalog,
)
from tests.cli.workbench_fixtures import (
    PluginCatalogFixture,
    ImageReplayRunner,
    _wait_until,
    image_source,
)


class WorkbenchTests(PluginCatalogFixture, unittest.TestCase):
    def test_packaged_catalog_keeps_all_plugins_available_with_only_default_plugins_selected(self) -> None:
        catalog = packaged_plugin_catalog()
        self.assertEqual(set(catalog.ids), set(PERCEPTION_PLUGIN_SPECS))
        self.assertEqual(
            [item.plugin_id for item in catalog.plugins if item.default],
            ["frame", "floor_plane"],
        )
        self.assertEqual(ImageReplayRunner().state()["active_plugin_ids"], ["frame", "floor_plane"])
        mapper = catalog.build_mapper(["frame"])
        self.assertIs(mapper.plugin_manager.resolver, catalog)
        self.assertEqual(set(mapper.plugin_manager.available_ids), set(PERCEPTION_PLUGIN_SPECS))
        original = mapper.plugins[0]
        mapper.plugin_manager.add("floor_plane")
        mapper.perceive(PerceptionRequest(SensorSnapshot(
            read_id="test", readings={}, started_at_ms=100, completed_at_ms=100,
        )))
        self.assertEqual(mapper.plugin_ids, ("frame", "floor_plane"))
        self.assertIs(mapper.plugins[0], original)
        self.assertEqual(
            set(mapper.describe_schema()["configuration"]["available_plugins"]),
            set(PERCEPTION_PLUGIN_SPECS),
        )

    def test_workbench_can_run_packaged_plugin_outside_default_selection(self) -> None:
        with image_source(1) as root:
            runner = ImageReplayRunner(root, active_plugin_ids=["sim_color_targets"], cadence_ms=0)
            runner.start()
            state = runner.wait(10)
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["run_active_plugin_ids"], ["sim_color_targets"])
        self.assertEqual(
            [run["plugin_id"] for run in state["perception"]["plugin_runs"]],
            ["sim_color_targets"],
        )

    def test_manifest_runner_retains_catalog_for_later_selection_and_lazy_import(self) -> None:
        catalog = discover_plugin_catalog(self.plugin_root)
        mapper = catalog.build_mapper([])
        self.assertIs(mapper.plugin_manager.resolver, catalog)
        self.assertEqual(set(mapper.plugin_manager.available_ids), set(catalog.ready_ids))
        self.assertNotIn("fastsam", mapper.plugin_manager.available_ids)
        mapper.plugin_manager.add("classical_regions")
        mapper.perceive(PerceptionRequest(SensorSnapshot(
            read_id="test", readings={}, started_at_ms=100, completed_at_ms=100,
        )))
        self.assertEqual(mapper.plugin_ids, ("classical_regions",))
        with self.assertRaises(PluginCatalogError):
            mapper.plugin_manager.add("fastsam")
        self.assertEqual(mapper.plugin_manager.selected_ids, ("classical_regions",))

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
            ("floor_continuity", "classical_regions"),
        )
        mapper = catalog.build_mapper(["floor_continuity", "classical_regions"])
        self.assertEqual(mapper.plugin_ids, ("floor_continuity", "classical_regions"))
        perception = mapper.perceive(PerceptionRequest(SensorSnapshot(
            read_id="ordered", readings={}, started_at_ms=100, completed_at_ms=100,
        )))
        self.assertEqual(
            [run.plugin_id for run in perception.plugin_runs],
            ["floor_continuity", "classical_regions"],
        )
        self.assertEqual(catalog.normalize_selection([]), ())

    def test_explicit_catalog_selection_runs_only_selected_plugins(self) -> None:
        with image_source(1) as root:
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

    def test_memory_companion_is_selected_from_manifest(self) -> None:
        catalog = discover_plugin_catalog(
            Path(__file__).resolve().parents[2] / "lab/plugins/perception"
        )
        companion = catalog.memory_for_selection(["multi_obstruction_tracks"])
        self.assertEqual(companion["implementation_id"], "multi_obstruction_tracks")
        self.assertIsNone(catalog.memory_for_selection(["classical_regions"]))

    def test_explicit_catalog_allows_raw_capture_and_live_replacement(self) -> None:
        with image_source(3) as root:
            runner = ImageReplayRunner(
                root,
                plugin_dir=self.plugin_root,
                cadence_ms=0,
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
            started = runner.start(cadence_ms=30000)
            run_id = started["run_id"]
            _wait_until(lambda: runner.state()["position"] == 1)
            before = runner.state()
            first_id = before["timeline"][0]["frame"]["frame_id"]
            mapper = runner._mapper
            runner._shared_memory["retention-marker"] = "kept"
            selected = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=["floor_continuity"],
            )
            self.assertEqual(selected["phase"], "running")
            self.assertEqual(selected["run_active_plugin_ids"], ["floor_continuity"])
            self.assertIs(runner._mapper, mapper)
            self.assertEqual(runner._shared_memory.get("retention-marker"), "kept")
            self.assertEqual(selected["timeline"][0]["frame"]["frame_id"], first_id)
            retained = runner.frame_detail(first_id, run_id=run_id)
            self.assertEqual(
                [run["plugin_id"] for run in retained["perception"]["plugin_runs"]],
                ["classical_regions"],
            )
            paused = runner.dispatch("pause", run_id=run_id)
            self.assertEqual(paused["phase"], "paused")
            if paused["position"] == before["position"]:
                paused = runner.dispatch("step", run_id=run_id)
            latest_id = paused["timeline"][-1]["frame"]["frame_id"]
            self.assertNotEqual(latest_id, first_id)
            latest = runner.frame_detail(latest_id, run_id=run_id)
            self.assertEqual(
                [run["plugin_id"] for run in latest["perception"]["plugin_runs"]],
                ["floor_continuity"],
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
            self.assertEqual(
                [
                    run["plugin_id"]
                    for run in runner.frame_detail(first_id, run_id=run_id)["perception"][
                        "plugin_runs"
                    ]
                ],
                ["classical_regions"],
            )
            runner.dispatch("cancel", run_id=run_id)

    def test_paused_plugin_toggle_reprocesses_current_frame_evidence(self) -> None:
        with image_source(3) as root:
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
                active_plugin_ids=["floor_continuity", "classical_regions"],
            )
            self.assertEqual(both["phase"], "paused")
            self.assertEqual(both["position"], position)
            self.assertEqual(len(both["timeline"]), timeline_len)
            self.assertEqual(
                both["run_active_plugin_ids"], ["floor_continuity", "classical_regions"]
            )
            self.assertEqual(
                [run["plugin_id"] for run in both["perception"]["plugin_runs"]],
                ["floor_continuity", "classical_regions"],
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

    def test_paused_selection_retains_instances_until_reset(self) -> None:
        marker_root = self.plugin_root / "marker"
        source = marker_root / "src"
        source.mkdir(parents=True)
        (marker_root / "__init__.py").write_text("", encoding="utf-8")
        (source / "__init__.py").write_text("", encoding="utf-8")
        (source / "plugin.py").write_text(
            "from autonomy.perception import PerceptionEvidenceBatch, PerceptionPluginContract\n"
            "\n"
            "class MarkerPlugin:\n"
            "    plugin_id = 'marker-plugin-v0'\n"
            "    contract = PerceptionPluginContract()\n"
            "\n"
            "    def perceive(self, inputs):\n"
            "        return PerceptionEvidenceBatch()\n",
            encoding="utf-8",
        )
        (marker_root / "plugin.json").write_text(
            json.dumps(
                {
                    "schema": "automa_lab_perception_plugin_v0",
                    "id": "marker",
                    "name": "Marker",
                    "description": "Empty evidence used to observe selection retention.",
                    "memory": {
                        "implementation_id": "marker_memory",
                        "implementation_spec": "marker.memory:MarkerMemory",
                    },
                    "plugin": {
                        "entrypoint": "marker.src.plugin:MarkerPlugin",
                        "config": {},
                    },
                    "inputs": [
                        {
                            "name": "frame",
                            "component_id": "camera.rgb:front_camera",
                            "provider_spec": (
                                "implementations.perception.components.camera:"
                                "provide_camera_frame"
                            ),
                        }
                    ],
                    "runtime": {"python": "core"},
                    "output": {
                        "schema": "perception_text_v2",
                        "kind": "marker",
                    },
                }
            ),
            encoding="utf-8",
        )
        with image_source(3) as root:
            runner = ImageReplayRunner(
                root,
                plugin_dir=self.plugin_root,
                cadence_ms=30000,
            )
            runner.dispatch(
                "select_plugins",
                active_plugin_ids=["classical_regions"],
            )
            started = runner.start()
            run_id = started["run_id"]
            _wait_until(lambda: runner.state()["position"] == 1)
            paused = runner.dispatch("pause", run_id=run_id)
            mapper = runner._mapper
            memory_step = runner._memory_step
            engine = runner._decision_engine
            retained = dict(zip(mapper.plugin_ids, mapper.plugins))["classical_regions"]
            memory_plugin = memory_step.plugins[0]
            self.assertIn("bounded_evidence", memory_step.plugin_manager.available_ids)
            self.assertIn("marker_memory", memory_step.plugin_manager.available_ids)
            self.assertEqual(memory_step.plugin_manager.selected_ids, ("bounded_evidence",))
            runner._shared_memory["retention-marker"] = "kept"

            both = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=["marker", "classical_regions"],
            )
            self.assertEqual(both["phase"], "paused")
            self.assertEqual(both["position"], paused["position"])
            self.assertEqual(len(both["timeline"]), len(paused["timeline"]))
            self.assertIs(runner._mapper, mapper)
            self.assertIs(runner._memory_step, memory_step)
            self.assertIs(runner._decision_engine, engine)
            applied = dict(zip(mapper.plugin_ids, mapper.plugins))
            self.assertEqual(list(applied), ["marker", "classical_regions"])
            self.assertIs(applied["classical_regions"], retained)
            self.assertEqual(
                [run["plugin_id"] for run in both["perception"]["plugin_runs"]],
                ["marker", "classical_regions"],
            )
            self.assertEqual(
                both["perception"]["plugin_runs"][0]["implementation_id"],
                "marker-plugin-v0",
            )
            self.assertEqual(
                memory_step.plugin_manager.selected_ids, ("marker_memory",)
            )
            self.assertIs(memory_step.plugins[0], memory_plugin)
            self.assertEqual(
                both["machine_detail"]["pipeline"]["memory_implementation"],
                "marker_memory",
            )
            self.assertEqual(runner._shared_memory["retention-marker"], "kept")

            reset = runner.dispatch("reset", run_id=run_id)
            self.assertEqual(reset["phase"], "idle")
            self.assertEqual(reset["timeline"], [])
            self.assertEqual(runner._shared_memory, {})

    def test_catalog_rejects_unavailable_and_duplicate_selection(self) -> None:
        catalog = discover_plugin_catalog(self.plugin_root)
        with self.assertRaises(PluginCatalogError):
            catalog.normalize_selection(["fastsam"])
        with self.assertRaises(PluginCatalogError):
            catalog.normalize_selection(["classical_regions", "classical_regions"])
