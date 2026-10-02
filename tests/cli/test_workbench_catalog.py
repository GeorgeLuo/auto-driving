from __future__ import annotations
import unittest
from autonomy.decision_cycle.perception.feeds.context import PerceptionRequest
from autonomy.vehicle import SensorSnapshot
from implementations.decision_cycle.catalog import step_plugins
from cli.automa_cli.workbench_plugins import packaged_plugin_catalog
from cli.automa_cli.workbench import PluginCatalogError, ReplayActionError
from tests.cli.workbench_fixtures import (
    ImageReplayRunner,
    _wait_until,
    image_source,
)


def _plugin_ids(perception: dict | None) -> list[str]:
    return [run["plugin_id"] for run in (perception or {}).get("plugin_runs") or ()]


def _pause_after_first_frame(runner: ImageReplayRunner) -> tuple[str, dict]:
    started = runner.start()
    run_id = started["run_id"]
    _wait_until(lambda: runner.state()["position"] == 1)
    return run_id, runner.dispatch("pause", run_id=run_id)


class WorkbenchTests(unittest.TestCase):
    def test_packaged_catalog_keeps_all_plugins_available_with_only_default_plugins_selected(self) -> None:
        catalog = packaged_plugin_catalog()
        packaged = set(step_plugins("perception"))
        self.assertEqual(set(catalog.ids), packaged)
        self.assertEqual(catalog.ids[:2], ("frame", "floor_plane"))
        self.assertEqual(catalog.default_ids, ("frame", "floor_plane"))
        self.assertEqual(catalog.digest, packaged_plugin_catalog().digest)
        self.assertEqual(ImageReplayRunner().state()["active_plugin_ids"], ["frame", "floor_plane"])
        mapper = catalog.build_mapper(["frame"])
        self.assertEqual(set(mapper.plugin_manager.available_ids), packaged)
        original = mapper.plugins[0]
        mapper.plugin_manager.add("floor_plane")
        mapper.perceive(PerceptionRequest(SensorSnapshot(
            read_id="test", readings={}, started_at_ms=100, completed_at_ms=100,
        )))
        self.assertEqual(mapper.plugin_ids, ("frame", "floor_plane"))
        self.assertIs(mapper.plugins[0], original)

    def test_selection_keeps_order_and_rejects_unknown_or_repeated_ids(self) -> None:
        catalog = packaged_plugin_catalog()
        self.assertEqual(
            catalog.normalize_selection(["floor_continuity", "classical_regions"]),
            ("floor_continuity", "classical_regions"),
        )
        self.assertEqual(catalog.normalize_selection([]), ())
        with self.assertRaisesRegex(PluginCatalogError, "unknown perception plugin\(s\) missing"):
            catalog.normalize_selection(["missing"])
        with self.assertRaisesRegex(PluginCatalogError, "duplicates"):
            catalog.normalize_selection(["frame", "frame"])
        mapper = catalog.build_mapper(["floor_continuity", "classical_regions"])
        perception = mapper.perceive(PerceptionRequest(SensorSnapshot(
            read_id="ordered", readings={}, started_at_ms=100, completed_at_ms=100,
        )))
        self.assertEqual(
            [run.plugin_id for run in perception.plugin_runs],
            ["floor_continuity", "classical_regions"],
        )

    def test_workbench_can_run_packaged_plugin_outside_default_selection(self) -> None:
        with image_source(1) as root:
            runner = ImageReplayRunner(root, active_plugin_ids=["classical_regions"], cadence_ms=0)
            runner.start()
            state = runner.wait(10)
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["run_active_plugin_ids"], ["classical_regions"])
        self.assertEqual(_plugin_ids(state["perception"]), ["classical_regions"])
        self.assertEqual(
            state["machine_detail"]["pipeline"]["perception_preset"], "custom"
        )

    def test_empty_selection_replays_raw_capture_and_allows_live_replacement(self) -> None:
        with image_source(3) as root:
            runner = ImageReplayRunner(root, active_plugin_ids=[], cadence_ms=0)
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
            runner.dispatch("reset", run_id=run_id)

    def test_paused_plugin_toggle_reprocesses_the_current_frame(self) -> None:
        with image_source(3) as root:
            runner = ImageReplayRunner(
                root,
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
            runner.dispatch("reset", run_id=run_id)

    def test_seek_shows_frames_with_the_current_selection(self) -> None:
        with image_source(4) as root:
            runner = ImageReplayRunner(
                root,
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
            runner.dispatch("reset", run_id=run_id)

    def test_paused_selection_retains_instances_and_reprocesses(self) -> None:
        with image_source(3) as root:
            runner = ImageReplayRunner(
                root,
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
                active_plugin_ids=["floor_continuity", "classical_regions"],
            )
            self.assertEqual(both["phase"], "paused")
            self.assertEqual(both["position"], paused["position"])
            self.assertEqual(len(both["timeline"]), len(paused["timeline"]))
            self.assertIs(runner._mapper, mapper)
            self.assertIs(runner._memory_step, memory_step)
            self.assertIs(runner._decision_steps, decision_steps)
            applied = dict(zip(mapper.plugin_ids, mapper.plugins))
            self.assertEqual(list(applied), ["floor_continuity", "classical_regions"])
            self.assertIs(applied["classical_regions"], retained)
            self.assertEqual(
                _plugin_ids(both["perception"]), ["floor_continuity", "classical_regions"]
            )
            self.assertIs(memory_step.plugins[0], memory_plugin)
            self.assertEqual(memory_step.plugin_manager.selected_ids, ("bounded_evidence",))
            self.assertEqual(
                both["machine_detail"]["pipeline"]["memory_plugin_id"],
                "bounded_evidence",
            )
            self.assertEqual(runner._shared_memory["retention-marker"], "kept")

            stepped = runner.dispatch("step", run_id=run_id)
            self.assertEqual(stepped["phase"], "paused")
            self.assertEqual(
                _plugin_ids(stepped["perception"]),
                ["floor_continuity", "classical_regions"],
            )
            self.assertIs(memory_step.plugins[0], memory_plugin)
            self.assertEqual(
                _plugin_ids(runner.frame_detail(first_id, run_id=run_id)["perception"]),
                ["floor_continuity", "classical_regions"],
            )
            self.assertEqual(runner._shared_memory["retention-marker"], "kept")

            reset = runner.dispatch("reset", run_id=run_id)
            self.assertEqual(reset["phase"], "idle")
            self.assertEqual(reset["timeline"], [])
            self.assertEqual(runner._shared_memory, {})


class WorkbenchMatchesInspectTests(unittest.TestCase):
    def test_selection_results_equal_inspect_results(self) -> None:
        import json

        from cli.automa_cli.perception_runs import inspect_perception

        def canonical(value):
            return json.loads(json.dumps(value, sort_keys=True, default=str))

        with image_source(1) as root:
            inspected = json.loads(
                inspect_perception(root, plugins=["classical_regions"], json_output=True).message
            )["frames"][0]["perception"]
            runner = ImageReplayRunner(root, active_plugin_ids=["classical_regions"], cadence_ms=0)
            runner.start()
            shown = runner.wait(10)["perception"]
        for key in ("status", "signals", "things", "limits"):
            self.assertEqual(canonical(shown[key]), canonical(inspected[key]), key)
