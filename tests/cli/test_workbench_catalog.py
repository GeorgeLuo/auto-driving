from __future__ import annotations
import json
import unittest
from implementations.decision_cycle.memory.bounded_evidence.ledger import LEDGER_KEY
from pathlib import Path
from autonomy.decision_cycle.perception.components.context import PerceptionRequest
from autonomy.vehicle import SensorSnapshot
from implementations.decision_cycle.perception.catalog import PERCEPTION_PLUGIN_SPECS
from cli.automa_cli.workbench_plugins import (
    PluginCatalog,
    PluginDescriptor,
    packaged_plugin_catalog,
)
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

_BOUNDED_SPEC = "implementations.decision_cycle.memory.bounded_evidence.plugin:BoundedEvidenceLedger"
_FRAME_INPUT = {
    "name": "frame",
    "component_id": "camera.rgb:front_camera",
    "provider_spec": "implementations.decision_cycle.perception.components.camera:provide_camera_frame",
}


def _plugin_ids(perception: dict | None) -> list[str]:
    return [run["plugin_id"] for run in (perception or {}).get("plugin_runs") or ()]


def _pause_after_first_frame(runner: ImageReplayRunner) -> tuple[str, dict]:
    started = runner.start()
    run_id = started["run_id"]
    _wait_until(lambda: runner.state()["position"] == 1)
    return run_id, runner.dispatch("pause", run_id=run_id)


def _descriptor(plugin_id: str, memory: dict) -> PluginDescriptor:
    return PluginDescriptor(
        plugin_id=plugin_id,
        name=plugin_id,
        description="",
        manifest_relative_path=plugin_id,
        manifest_path=None,
        entrypoint="example.plugin:Plugin",
        config={},
        memory=memory,
        inputs=[],
        output={},
        model={},
        runtime={},
        status="ready",
    )


def _memory_descriptor(
    plugin_id: str,
    *,
    implementation_id: str = "bounded_evidence",
    spec: str = _BOUNDED_SPEC,
    config: dict | None = None,
) -> PluginDescriptor:
    return _descriptor(
        plugin_id,
        {
            "implementation_id": implementation_id,
            "implementation_spec": spec,
            "implementation_config": dict(config or {}),
        },
    )


def _catalog(*plugins: PluginDescriptor) -> PluginCatalog:
    return PluginCatalog(root=None, plugins=plugins, digest="memory-catalog")


def _install_plugin(
    root: Path,
    plugin_id: str,
    class_name: str,
    source: str,
    *,
    memory: dict | None = None,
) -> None:
    package = root / plugin_id
    source_dir = package / "src"
    source_dir.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (source_dir / "__init__.py").write_text("", encoding="utf-8")
    (source_dir / "plugin.py").write_text(source, encoding="utf-8")
    payload: dict = {
        "schema": "automa_lab_perception_plugin_v0",
        "id": plugin_id,
        "name": plugin_id,
        "description": plugin_id,
        "plugin": {
            "entrypoint": f"{plugin_id}.src.plugin:{class_name}",
            "config": {},
        },
        "inputs": [_FRAME_INPUT],
        "runtime": {"python": "core"},
        "output": {"schema": "perception_text_v2", "kind": plugin_id},
    }
    if memory is not None:
        payload["memory"] = memory
    (package / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")


def _empty_plugin_source(class_name: str, implementation_id: str) -> str:
    return (
        "from autonomy.decision_cycle.perception.evidence.values import PerceptionEvidenceBatch\n"
        "from autonomy.decision_cycle.perception.plugin import PerceptionPluginContract\n"
        "\n"
        f"class {class_name}:\n"
        f"    plugin_id = {implementation_id!r}\n"
        "    contract = PerceptionPluginContract()\n"
        "\n"
        "    def perceive(self, inputs):\n"
        "        return PerceptionEvidenceBatch()\n"
    )


def _temporal_source(implementation_id: str) -> str:
    return (
        "from autonomy.decision_cycle.perception.evidence.values import PerceptionEvidenceBatch\n"
        "from autonomy.decision_cycle.perception.plugin import PerceptionPluginContract\n"
        "\n"
        "class TemporalPlugin:\n"
        f"    plugin_id = {implementation_id!r}\n"
        "    contract = PerceptionPluginContract()\n"
        "\n"
        "    def __init__(self):\n"
        "        self.frames = []\n"
        "\n"
        "    def perceive(self, inputs):\n"
        "        self.frames.append(inputs.frame_id)\n"
        "        shared = inputs.shared_memory.setdefault('temporal.frames', [])\n"
        "        shared.append(inputs.frame_id)\n"
        "        return PerceptionEvidenceBatch()\n"
    )


def _install_memory_module(root: Path, plugin_id: str, source: str) -> None:
    (root / plugin_id / "src" / "memory.py").write_text(source, encoding="utf-8")


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

    def test_manifest_discovery_does_not_search_inside_plugin_packages(self) -> None:
        nested = self.plugin_root / "classical_regions" / "runs" / "copy"
        nested.mkdir(parents=True)
        (nested / "plugin.json").write_text(
            (self.plugin_root / "classical_regions" / "plugin.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        catalog = discover_plugin_catalog(self.plugin_root)
        self.assertEqual(
            [item.plugin_id for item in catalog.plugins].count("classical_regions"), 1
        )
        self.assertIn("classical_regions", catalog.ready_ids)

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
        selected = catalog.memory_manager(("multi_obstruction_tracks",))
        self.assertEqual(selected.selected_ids, ("multi_obstruction_tracks",))
        self.assertEqual(
            selected.selected[0].entrypoint,
            "lab.plugins.memory.multi_obstruction_tracks.plugin:MultiObstructionMemory",
        )
        self.assertEqual(
            catalog.memory_id_for_selection(("classical_regions",)),
            "bounded_evidence",
        )

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
            first_id = runner.state()["timeline"][0]["frame"]["frame_id"]
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
            # The displayed frame runs again with the new selection.
            _wait_until(
                lambda: runner.state()["position"] == 1 and runner.state()["timeline"]
            )
            reprocessed = runner.frame_detail(first_id, run_id=run_id)
            self.assertEqual(
                [run["plugin_id"] for run in reprocessed["perception"]["plugin_runs"]],
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
                ["floor_continuity"],
            )
            runner.dispatch("cancel", run_id=run_id)

    def test_paused_plugin_toggle_reprocesses_the_current_frame(self) -> None:
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
            run_id, paused = _pause_after_first_frame(runner)
            position = paused["position"]
            timeline_len = len(paused["timeline"])
            first_id = paused["current_frame"]["frame_id"]
            self.assertEqual(_plugin_ids(paused["perception"]), ["classical_regions"])
            recorded_report = runner.frame_detail(first_id, run_id=run_id)[
                "perception_plugin_report"
            ]
            self.assertEqual(recorded_report["applied_plugin_ids"], ["classical_regions"])

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
            self.assertEqual(both["current_frame"]["frame_id"], first_id)
            self.assertEqual(
                _plugin_ids(both["perception"]), ["floor_continuity", "classical_regions"]
            )
            self.assertEqual(
                _plugin_ids(runner.frame_detail(first_id, run_id=run_id)["perception"]),
                ["floor_continuity", "classical_regions"],
            )
            self.assertEqual(
                both["machine_detail"]["pipeline"]["perception_plugin_report"][
                    "applied_plugin_ids"
                ],
                ["floor_continuity", "classical_regions"],
            )

            none = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=[],
            )
            self.assertEqual(none["phase"], "paused")
            self.assertEqual(none["run_active_plugin_ids"], [])
            self.assertEqual(_plugin_ids(none["perception"]), [])

            one = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=["floor_continuity"],
            )
            self.assertEqual(one["phase"], "paused")
            self.assertEqual(one["position"], position)
            self.assertEqual(runner._mapper.plugin_ids, ("floor_continuity",))
            self.assertEqual(_plugin_ids(one["perception"]), ["floor_continuity"])

            stepped = runner.dispatch("step", run_id=run_id)
            self.assertEqual(stepped["phase"], "paused")
            self.assertEqual(_plugin_ids(stepped["perception"]), ["floor_continuity"])
            self.assertNotEqual(stepped["current_frame"]["frame_id"], first_id)
            runner.dispatch("cancel", run_id=run_id)

    def test_seek_shows_frames_with_the_current_selection(self) -> None:
        with image_source(4) as root:
            runner = ImageReplayRunner(
                root,
                plugin_dir=self.plugin_root,
                cadence_ms=30000,
            )
            runner.dispatch("select_plugins", active_plugin_ids=["classical_regions"])
            run_id, paused = _pause_after_first_frame(runner)
            first_id = paused["current_frame"]["frame_id"]
            stepped = runner.dispatch("step", run_id=run_id)
            second_id = stepped["current_frame"]["frame_id"]
            self.assertEqual(_plugin_ids(stepped["perception"]), ["classical_regions"])

            runner.dispatch(
                "select_plugins", run_id=run_id, active_plugin_ids=["floor_continuity"]
            )
            back = runner.dispatch("seek", run_id=run_id, position=0)
            self.assertEqual(back["current_frame"]["frame_id"], first_id)
            self.assertEqual(_plugin_ids(back["perception"]), ["floor_continuity"])
            self.assertEqual(
                _plugin_ids(runner.frame_detail(first_id, run_id=run_id)["perception"]),
                ["floor_continuity"],
            )

            forward = runner.dispatch("seek", run_id=run_id, position=1)
            self.assertEqual(forward["current_frame"]["frame_id"], second_id)
            self.assertEqual(_plugin_ids(forward["perception"]), ["floor_continuity"])

            again = runner.dispatch("seek", run_id=run_id, position=0)
            self.assertEqual(_plugin_ids(again["perception"]), ["floor_continuity"])

            # Past the recorded frames, from an earlier position.
            ahead = runner.dispatch("seek", run_id=run_id, position=2)
            self.assertEqual(ahead["position"], 3)
            self.assertEqual(len(ahead["timeline"]), 3)
            self.assertEqual(_plugin_ids(ahead["perception"]), ["floor_continuity"])
            runner.dispatch("cancel", run_id=run_id)

    def test_paused_selection_retains_instances_and_reprocesses(self) -> None:
        _install_plugin(
            self.plugin_root,
            "kept_marker",
            "MarkerPlugin",
            _empty_plugin_source("MarkerPlugin", "marker-plugin-v0"),
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
            run_id, paused = _pause_after_first_frame(runner)
            first_id = paused["current_frame"]["frame_id"]
            mapper = runner._mapper
            memory_step = runner._memory_step
            decision_steps = runner._decision_steps
            retained = dict(zip(mapper.plugin_ids, mapper.plugins))["classical_regions"]
            memory_plugin = memory_step.plugins[0]
            self.assertEqual(memory_step.plugin_manager.selected_ids, ("bounded_evidence",))
            runner._shared_memory["retention-marker"] = "kept"

            both = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=["kept_marker", "classical_regions"],
            )
            self.assertEqual(both["phase"], "paused")
            self.assertEqual(both["position"], paused["position"])
            self.assertEqual(len(both["timeline"]), len(paused["timeline"]))
            self.assertIs(runner._mapper, mapper)
            self.assertIs(runner._memory_step, memory_step)
            self.assertIs(runner._decision_steps, decision_steps)
            applied = dict(zip(mapper.plugin_ids, mapper.plugins))
            self.assertEqual(list(applied), ["kept_marker", "classical_regions"])
            self.assertIs(applied["classical_regions"], retained)
            self.assertEqual(
                _plugin_ids(both["perception"]), ["kept_marker", "classical_regions"]
            )
            self.assertIs(memory_step.plugins[0], memory_plugin)
            self.assertEqual(memory_step.plugin_manager.selected_ids, ("bounded_evidence",))
            self.assertEqual(
                both["machine_detail"]["pipeline"]["memory_implementation"],
                "bounded_evidence",
            )
            self.assertEqual(
                both["machine_detail"]["pipeline"]["memory_plugin_report"]["plugins"][0][
                    "implementation_id"
                ],
                "bounded_evidence",
            )
            self.assertEqual(runner._shared_memory["retention-marker"], "kept")

            stepped = runner.dispatch("step", run_id=run_id)
            self.assertEqual(stepped["phase"], "paused")
            self.assertEqual(
                _plugin_ids(stepped["perception"]),
                ["kept_marker", "classical_regions"],
            )
            self.assertEqual(
                stepped["perception"]["plugin_runs"][0]["implementation_id"],
                "marker-plugin-v0",
            )
            self.assertIs(memory_step.plugins[0], memory_plugin)
            self.assertEqual(
                _plugin_ids(runner.frame_detail(first_id, run_id=run_id)["perception"]),
                ["kept_marker", "classical_regions"],
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

    def test_memory_catalog_identities_do_not_depend_on_plugin_order(self) -> None:
        plugins = [
            _memory_descriptor("plain"),
            _memory_descriptor("explicit_default", config={"max_records": 32}),
            _memory_descriptor("narrow", config={"max_records": 7}),
            _memory_descriptor("wider", config={"max_records": 9}),
            _memory_descriptor("aaa", config={"max_records": 4}),
            _memory_descriptor("zzz", config={"max_records": 4}),
            _memory_descriptor(
                "shared_a",
                implementation_id="tracks",
                spec="example.memory:SharedMemory",
            ),
            _memory_descriptor(
                "shared_b",
                implementation_id="tracks",
                spec="example.memory:SharedMemory",
            ),
            _memory_descriptor(
                "left",
                implementation_id="split",
                spec="example.memory:SplitMemory",
                config={"max_records": 7},
            ),
            _memory_descriptor(
                "right",
                implementation_id="split",
                spec="example.memory:SplitMemory",
                config={"max_records": 9},
            ),
        ]
        forward = _catalog(*plugins).memory_catalog()
        reverse = _catalog(*reversed(plugins)).memory_catalog()
        self.assertEqual(forward, reverse)
        definitions, mapping = forward
        configs = {item.plugin_id: item.config for item in definitions}
        self.assertEqual(mapping["plain"], "bounded_evidence")
        self.assertEqual(mapping["explicit_default"], "bounded_evidence")
        self.assertEqual(mapping["narrow"], "narrow.bounded_evidence")
        self.assertEqual(mapping["wider"], "wider.bounded_evidence")
        self.assertEqual(mapping["aaa"], "aaa.zzz.bounded_evidence")
        self.assertEqual(mapping["zzz"], "aaa.zzz.bounded_evidence")
        self.assertEqual(configs["bounded_evidence"]["max_records"], 32)
        self.assertEqual(configs["narrow.bounded_evidence"]["max_records"], 7)
        self.assertEqual(configs["wider.bounded_evidence"]["max_records"], 9)
        self.assertEqual(configs["aaa.zzz.bounded_evidence"]["max_records"], 4)
        self.assertEqual(mapping["shared_a"], "tracks")
        self.assertEqual(mapping["shared_b"], "tracks")
        self.assertEqual(mapping["left"], "left.split")
        self.assertEqual(mapping["right"], "right.split")
        self.assertNotIn("split", configs)

        empty_definitions, empty_mapping = _catalog().memory_catalog()
        empty_configs = {item.plugin_id: item.config for item in empty_definitions}
        self.assertEqual(empty_mapping, {})
        self.assertEqual(empty_configs["bounded_evidence"]["max_records"], 32)
        self.assertIn("bounded_evidence", empty_configs)

        lab = discover_plugin_catalog(
            Path(__file__).resolve().parents[2] / "lab/plugins/perception"
        )
        lab_definitions, lab_mapping = lab.memory_catalog()
        self.assertEqual(
            {
                lab_mapping["multi_obstruction_tracks"],
                lab_mapping["composite_box_fusion"],
                lab_mapping["composite_box_fusion_object_separated"],
            },
            {"multi_obstruction_tracks"},
        )
        self.assertNotIn(
            "composite_box_fusion.multi_obstruction_tracks",
            {item.plugin_id for item in lab_definitions},
        )

    def test_memory_catalog_rejects_conflicting_entrypoints(self) -> None:
        with self.assertRaises(PluginCatalogError) as conflicting:
            _catalog(
                _memory_descriptor(
                    "one",
                    implementation_id="custom",
                    spec="example.memory:One",
                ),
                _memory_descriptor(
                    "two",
                    implementation_id="custom",
                    spec="example.memory:Two",
                ),
            ).memory_catalog()
        self.assertIn("conflicting entrypoints", str(conflicting.exception))
        with self.assertRaises(PluginCatalogError) as packaged:
            _catalog(
                _memory_descriptor(
                    "replacement",
                    spec="example.memory:OtherLedger",
                )
            ).memory_catalog()
        self.assertIn("conflicting entrypoints", str(packaged.exception))

    def test_unselected_companion_does_not_change_active_memory_config(self) -> None:
        _install_plugin(
            self.plugin_root,
            "narrow",
            "NarrowPlugin",
            _empty_plugin_source("NarrowPlugin", "narrow-plugin-v0"),
            memory={
                "implementation_id": "bounded_evidence",
                "implementation_spec": _BOUNDED_SPEC,
                "implementation_config": {"max_records": 7},
            },
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
            run_id, paused = _pause_after_first_frame(runner)
            first_id = paused["current_frame"]["frame_id"]
            memory_step = runner._memory_step
            configs = {
                item.plugin_id: item.config["max_records"]
                for item in memory_step.plugin_manager.available
            }
            self.assertEqual(configs["bounded_evidence"], 32)
            self.assertEqual(configs["narrow.bounded_evidence"], 7)
            self.assertEqual(
                memory_step.plugins[0].implementation.bounds.max_records, 32
            )
            self.assertEqual(
                memory_step.plugin_manager.selected_ids, ("bounded_evidence",)
            )
            runner._shared_memory["retention-marker"] = "kept"
            previous_ledger = memory_step.plugins[0]

            selected = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=["narrow"],
            )
            self.assertEqual(selected["phase"], "paused")
            self.assertEqual(_plugin_ids(selected["perception"]), ["narrow"])
            self.assertEqual(
                memory_step.plugin_manager.selected_ids, ("narrow.bounded_evidence",)
            )
            self.assertEqual(
                memory_step.plugins[0].implementation.bounds.max_records, 7
            )
            self.assertIsNot(memory_step.plugins[0], previous_ledger)
            live_memory = selected["machine_detail"]["pipeline"]["memory_plugin_report"]
            self.assertEqual(
                selected["machine_detail"]["pipeline"]["memory_implementation"],
                "bounded_evidence",
            )
            self.assertEqual(live_memory["applied_plugin_ids"], ["narrow.bounded_evidence"])
            self.assertEqual(live_memory["plugins"][0]["plugin_id"], "narrow.bounded_evidence")
            self.assertEqual(
                live_memory["plugins"][0]["implementation_id"], "bounded_evidence"
            )
            reprocessed_memory = runner.frame_detail(first_id, run_id=run_id)[
                "memory_plugin_report"
            ]
            self.assertEqual(
                reprocessed_memory["applied_plugin_ids"], ["narrow.bounded_evidence"]
            )
            self.assertEqual(runner._shared_memory["retention-marker"], "kept")

            stepped = runner.dispatch("step", run_id=run_id)
            self.assertEqual(stepped["phase"], "paused")
            self.assertEqual(_plugin_ids(stepped["perception"]), ["narrow"])
            self.assertEqual(
                memory_step.plugins[0].implementation.bounds.max_records, 7
            )
            runner.dispatch("cancel", run_id=run_id)

    def test_paused_missing_memory_companion_keeps_the_working_run(self) -> None:
        _install_plugin(
            self.plugin_root,
            "missing_marker",
            "MarkerPlugin",
            _empty_plugin_source("MarkerPlugin", "marker-plugin-v0"),
            memory={
                "implementation_id": "marker_memory",
                "implementation_spec": "missing_marker.memory:MarkerMemory",
                "implementation_config": {},
            },
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
            run_id, paused = _pause_after_first_frame(runner)
            mapper = runner._mapper
            memory_step = runner._memory_step
            retained = dict(zip(mapper.plugin_ids, mapper.plugins))["classical_regions"]
            memory_plugin = memory_step.plugins[0]
            ledger = runner._shared_memory.get(LEDGER_KEY)
            runner._shared_memory["retention-marker"] = "kept"

            with self.assertRaises(ReplayActionError) as caught:
                runner.dispatch(
                    "select_plugins",
                    run_id=run_id,
                    active_plugin_ids=["missing_marker", "classical_regions"],
                )

            self.assertEqual(caught.exception.boundary, "plugin_catalog")
            self.assertIn("missing_marker.memory", str(caught.exception))
            state = runner.state()
            self.assertEqual(state["phase"], "paused")
            self.assertEqual(state["run_active_plugin_ids"], ["classical_regions"])
            self.assertIs(runner._mapper, mapper)
            self.assertIs(runner._memory_step, memory_step)
            applied = dict(zip(mapper.plugin_ids, mapper.plugins))
            self.assertEqual(list(applied), ["classical_regions"])
            self.assertIs(applied["classical_regions"], retained)
            self.assertIs(memory_step.plugins[0], memory_plugin)
            self.assertEqual(
                memory_step.plugin_manager.selected_ids, ("bounded_evidence",)
            )
            self.assertIs(runner._shared_memory.get(LEDGER_KEY), ledger)
            self.assertEqual(runner._shared_memory["retention-marker"], "kept")

            stepped = runner.dispatch("step", run_id=run_id)
            self.assertEqual(stepped["phase"], "paused")
            self.assertEqual(_plugin_ids(stepped["perception"]), ["classical_regions"])
            self.assertIs(applied["classical_regions"], retained)
            self.assertIs(memory_step.plugins[0], memory_plugin)
            runner.dispatch("cancel", run_id=run_id)

    def test_running_perception_constructor_failure_keeps_the_working_run(self) -> None:
        _install_plugin(
            self.plugin_root,
            "broken",
            "BrokenPlugin",
            "from autonomy.decision_cycle.perception.evidence.values import PerceptionEvidenceBatch\n"
            "from autonomy.decision_cycle.perception.plugin import PerceptionPluginContract\n"
            "\n"
            "class BrokenPlugin:\n"
            "    plugin_id = 'broken-plugin-v0'\n"
            "    contract = PerceptionPluginContract()\n"
            "\n"
            "    def __init__(self):\n"
            "        raise RuntimeError('constructor failed')\n"
            "\n"
            "    def perceive(self, inputs):\n"
            "        return PerceptionEvidenceBatch()\n",
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
            before = runner.state()
            self.assertEqual(before["phase"], "running")
            mapper = runner._mapper
            memory_step = runner._memory_step
            retained = dict(zip(mapper.plugin_ids, mapper.plugins))["classical_regions"]
            memory_plugin = memory_step.plugins[0]
            first_id = before["current_frame"]["frame_id"]

            with self.assertRaises(ReplayActionError) as caught:
                runner.dispatch(
                    "select_plugins",
                    run_id=run_id,
                    active_plugin_ids=["broken"],
                )

            self.assertIn("constructor failed", str(caught.exception))
            state = runner.state()
            self.assertEqual(state["phase"], "running")
            self.assertEqual(state["run_active_plugin_ids"], ["classical_regions"])
            self.assertIs(runner._mapper, mapper)
            self.assertIs(runner._memory_step, memory_step)
            applied = dict(zip(mapper.plugin_ids, mapper.plugins))
            self.assertEqual(mapper.plugin_manager.selected_ids, ("classical_regions",))
            self.assertIs(applied["classical_regions"], retained)
            self.assertIs(memory_step.plugins[0], memory_plugin)

            paused = runner.dispatch("pause", run_id=run_id)
            self.assertEqual(paused["phase"], "paused")
            if paused["position"] == before["position"]:
                paused = runner.dispatch("step", run_id=run_id)
            self.assertEqual(paused["phase"], "paused")
            self.assertEqual(_plugin_ids(paused["perception"]), ["classical_regions"])
            self.assertNotEqual(paused["current_frame"]["frame_id"], first_id)
            self.assertEqual(
                _plugin_ids(runner.frame_detail(first_id, run_id=run_id)["perception"]),
                ["classical_regions"],
            )
            self.assertIs(applied["classical_regions"], retained)
            runner.dispatch("cancel", run_id=run_id)

    def test_multiple_memory_companions_do_not_replace_the_run(self) -> None:
        for plugin_id, max_records in (("alpha", 7), ("beta", 9)):
            _install_plugin(
                self.plugin_root,
                plugin_id,
                "CompanionPlugin",
                _empty_plugin_source("CompanionPlugin", f"{plugin_id}-plugin-v0"),
                memory={
                    "implementation_id": "bounded_evidence",
                    "implementation_spec": _BOUNDED_SPEC,
                    "implementation_config": {"max_records": max_records},
                },
            )
        with image_source(2) as root:
            runner = ImageReplayRunner(
                root,
                plugin_dir=self.plugin_root,
                cadence_ms=30000,
            )
            runner.dispatch(
                "select_plugins",
                active_plugin_ids=["classical_regions"],
            )
            run_id, _paused = _pause_after_first_frame(runner)
            mapper = runner._mapper
            retained = dict(zip(mapper.plugin_ids, mapper.plugins))["classical_regions"]

            with self.assertRaises(ReplayActionError) as caught:
                runner.dispatch(
                    "select_plugins",
                    run_id=run_id,
                    active_plugin_ids=["alpha", "beta"],
                )

            self.assertIn("multiple memory companions", str(caught.exception))
            state = runner.state()
            self.assertEqual(state["phase"], "paused")
            self.assertEqual(state["run_active_plugin_ids"], ["classical_regions"])
            applied = dict(zip(mapper.plugin_ids, mapper.plugins))
            self.assertIs(applied["classical_regions"], retained)
            self.assertEqual(
                runner._memory_step.plugins[0].implementation.bounds.max_records, 32
            )
            runner.dispatch("cancel", run_id=run_id)

    def test_paused_selection_reprocesses_the_frame_with_temporal_history(self) -> None:
        _install_plugin(
            self.plugin_root,
            "temporal",
            "TemporalPlugin",
            "from autonomy.decision_cycle.perception.evidence.values import PerceptionEvidenceBatch\n"
            "from autonomy.decision_cycle.perception.plugin import PerceptionPluginContract\n"
            "\n"
            "class TemporalPlugin:\n"
            "    plugin_id = 'temporal-plugin-v0'\n"
            "    contract = PerceptionPluginContract()\n"
            "\n"
            "    def perceive(self, inputs):\n"
            "        frames = inputs.shared_memory.setdefault('temporal.frames', [])\n"
            "        frames.append(inputs.frame_id)\n"
            "        return PerceptionEvidenceBatch()\n",
        )
        with image_source(3) as root:
            runner = ImageReplayRunner(
                root,
                plugin_dir=self.plugin_root,
                cadence_ms=30000,
            )
            runner.dispatch("select_plugins", active_plugin_ids=["temporal"])
            run_id, paused = _pause_after_first_frame(runner)
            first_id = paused["current_frame"]["frame_id"]
            frames = runner._shared_memory["temporal.frames"]
            self.assertEqual(frames, [first_id])

            selected = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=["temporal", "classical_regions"],
            )
            self.assertEqual(selected["phase"], "paused")
            self.assertIs(runner._shared_memory["temporal.frames"], frames)
            # The retained plugin sees the reprocessed frame again.
            self.assertEqual(frames, [first_id, first_id])
            self.assertEqual(
                _plugin_ids(selected["perception"]), ["temporal", "classical_regions"]
            )
            self.assertEqual(
                runner._mapper.plugin_ids, ("temporal", "classical_regions")
            )

            stepped = runner.dispatch("step", run_id=run_id)
            self.assertEqual(stepped["phase"], "paused")
            self.assertEqual(
                frames, [first_id, first_id, stepped["current_frame"]["frame_id"]]
            )
            self.assertNotEqual(stepped["current_frame"]["frame_id"], first_id)
            self.assertEqual(
                _plugin_ids(stepped["perception"]),
                ["temporal", "classical_regions"],
            )
            runner.dispatch("cancel", run_id=run_id)

    def test_memory_replacement_applies_the_prepared_instance_once(self) -> None:
        _install_plugin(
            self.plugin_root,
            "counting_temporal",
            "TemporalPlugin",
            _temporal_source("counting-temporal-v0"),
        )
        _install_plugin(
            self.plugin_root,
            "counting_companion",
            "CompanionPlugin",
            _empty_plugin_source("CompanionPlugin", "counting-companion-v0"),
            memory={
                "implementation_id": "counting_memory",
                "implementation_spec": "counting_companion.src.memory:CountingMemory",
                "implementation_config": {},
            },
        )
        _install_memory_module(
            self.plugin_root,
            "counting_companion",
            "class CountingMemory:\n"
            "    constructions = 0\n"
            "    built = None\n"
            "\n"
            "    def __init__(self, **config):\n"
            "        CountingMemory.constructions += 1\n"
            "        if CountingMemory.constructions > 1:\n"
            "            raise RuntimeError('memory constructed twice')\n"
            "        CountingMemory.built = self\n"
            "        self.plugin_id = 'counting_companion'\n"
            "        self.implementation_id = 'counting_memory'\n"
            "\n"
            "    def update(self, context, observation):\n"
            "        del context, observation\n"
            "\n"
            "    def reset(self, shared_memory):\n"
            "        del shared_memory\n",
        )
        with image_source(3) as root:
            runner = ImageReplayRunner(
                root,
                plugin_dir=self.plugin_root,
                cadence_ms=30000,
            )
            runner.dispatch(
                "select_plugins",
                active_plugin_ids=["counting_temporal"],
            )
            run_id, paused = _pause_after_first_frame(runner)
            first_id = paused["current_frame"]["frame_id"]
            mapper = runner._mapper
            retained = dict(zip(mapper.plugin_ids, mapper.plugins))["counting_temporal"]
            frames = retained.frames
            shared = runner._shared_memory["temporal.frames"]
            self.assertEqual(frames, [first_id])
            self.assertEqual(shared, [first_id])

            selected = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=["counting_temporal", "counting_companion"],
            )

            implementation = runner._memory_step.plugins[0].implementation
            self.assertEqual(selected["phase"], "paused")
            self.assertEqual(implementation.constructions, 1)
            self.assertIs(implementation.built, implementation)
            self.assertEqual(
                runner._memory_step.plugin_manager.selected_ids,
                ("counting_memory",),
            )
            self.assertIs(
                dict(zip(mapper.plugin_ids, mapper.plugins))["counting_temporal"],
                retained,
            )
            self.assertEqual(mapper.plugin_ids, ("counting_temporal", "counting_companion"))
            self.assertIs(retained.frames, frames)
            # The retained plugin sees the reprocessed frame again.
            self.assertEqual(frames, [first_id, first_id])
            self.assertIs(runner._shared_memory["temporal.frames"], shared)
            self.assertEqual(
                _plugin_ids(selected["perception"]),
                ["counting_temporal", "counting_companion"],
            )
            runner.dispatch("cancel", run_id=run_id)

    def test_failed_memory_replacement_keeps_temporal_instance_history(self) -> None:
        _install_plugin(
            self.plugin_root,
            "failing_temporal",
            "TemporalPlugin",
            _temporal_source("failing-temporal-v0"),
        )
        _install_plugin(
            self.plugin_root,
            "failing_companion",
            "CompanionPlugin",
            _empty_plugin_source("CompanionPlugin", "failing-companion-v0"),
            memory={
                "implementation_id": "failing_memory",
                "implementation_spec": "failing_companion.src.memory:FailingMemory",
                "implementation_config": {},
            },
        )
        _install_memory_module(
            self.plugin_root,
            "failing_companion",
            "class FailingMemory:\n"
            "    def __init__(self, **config):\n"
            "        del config\n"
            "        raise RuntimeError('memory constructor failed')\n",
        )
        with image_source(3) as root:
            runner = ImageReplayRunner(
                root,
                plugin_dir=self.plugin_root,
                cadence_ms=30000,
            )
            runner.dispatch(
                "select_plugins",
                active_plugin_ids=["failing_temporal"],
            )
            run_id, paused = _pause_after_first_frame(runner)
            first_id = paused["current_frame"]["frame_id"]
            mapper = runner._mapper
            memory_step = runner._memory_step
            retained = dict(zip(mapper.plugin_ids, mapper.plugins))["failing_temporal"]
            frames = retained.frames
            shared = runner._shared_memory["temporal.frames"]
            memory_plugin = memory_step.plugins[0]
            ledger = runner._shared_memory.get(LEDGER_KEY)
            self.assertEqual(frames, [first_id])
            self.assertEqual(shared, [first_id])

            with self.assertRaises(ReplayActionError) as caught:
                runner.dispatch(
                    "select_plugins",
                    run_id=run_id,
                    active_plugin_ids=["failing_companion"],
                )

            self.assertEqual(caught.exception.boundary, "plugin_catalog")
            self.assertIn("memory constructor failed", str(caught.exception))
            state = runner.state()
            self.assertEqual(state["phase"], "paused")
            self.assertEqual(state["run_active_plugin_ids"], ["failing_temporal"])
            self.assertEqual(mapper.plugin_manager.selected_ids, ("failing_temporal",))
            applied = dict(zip(mapper.plugin_ids, mapper.plugins))
            self.assertIs(applied["failing_temporal"], retained)
            self.assertIs(retained.frames, frames)
            self.assertEqual(frames, [first_id])
            self.assertIs(runner._shared_memory["temporal.frames"], shared)
            self.assertEqual(shared, [first_id])
            self.assertIs(memory_step.plugins[0], memory_plugin)
            self.assertEqual(memory_step.plugin_manager.selected_ids, ("bounded_evidence",))
            self.assertIs(runner._shared_memory.get(LEDGER_KEY), ledger)

            stepped = runner.dispatch("step", run_id=run_id)
            self.assertEqual(stepped["phase"], "paused")
            self.assertIs(applied["failing_temporal"], retained)
            second_id = stepped["current_frame"]["frame_id"]
            self.assertNotEqual(second_id, first_id)
            self.assertEqual(frames, [first_id, second_id])
            self.assertEqual(shared, [first_id, second_id])
            self.assertEqual(_plugin_ids(stepped["perception"]), ["failing_temporal"])
            runner.dispatch("cancel", run_id=run_id)
