from __future__ import annotations
import json
import unittest
from autonomy.decision_cycle.perception.feeds.context import PerceptionRequest
from autonomy.vehicle import SensorFrame
from implementations.decision_cycle.catalog import step_plugins
from implementations.decision_cycle.memory.presets import MEMORY_PRESETS
from cli.automa_cli.memory import update_vehicle_memory
from cli.automa_cli.workbench_plugins import packaged_plugin_catalog
from cli.automa_cli.workbench import PluginCatalogError, ReplayActionError
from tests.cli.workbench_fixtures import (
    DecisionFixtureMapper,
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
        catalog = packaged_plugin_catalog("perception")
        packaged = set(step_plugins("perception"))
        self.assertEqual(set(catalog.ids), packaged)
        self.assertEqual(catalog.ids[:2], ("frame", "floor_plane"))
        self.assertEqual(catalog.default_ids, ("frame", "floor_plane"))
        self.assertEqual(catalog.digest, packaged_plugin_catalog("perception").digest)
        self.assertEqual(ImageReplayRunner().state()["active_plugin_ids"], ["frame", "floor_plane"])
        mapper = catalog.build(["frame"])
        self.assertEqual(set(mapper.plugin_manager.available_ids), packaged)
        original = mapper.plugins[0]
        mapper.plugin_manager.add("floor_plane")
        mapper.perceive(PerceptionRequest(SensorFrame(
            read_id="test", readings={}, started_at_ms=100, completed_at_ms=100,
        )))
        self.assertEqual(mapper.plugin_ids, ("frame", "floor_plane"))
        self.assertIs(mapper.plugins[0], original)

    def test_selection_keeps_order_and_rejects_unknown_or_repeated_ids(self) -> None:
        catalog = packaged_plugin_catalog("perception")
        self.assertEqual(
            catalog.normalize_selection(["floor_continuity", "classical_regions"]),
            ("floor_continuity", "classical_regions"),
        )
        self.assertEqual(catalog.normalize_selection([]), ())
        with self.assertRaisesRegex(PluginCatalogError, "unknown perception plugin\(s\) missing"):
            catalog.normalize_selection(["missing"])
        with self.assertRaisesRegex(PluginCatalogError, "duplicates"):
            catalog.normalize_selection(["frame", "frame"])
        mapper = catalog.build(["floor_continuity", "classical_regions"])
        perception = mapper.perceive(PerceptionRequest(SensorFrame(
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
        self.assertEqual(_plugin_ids(state["steps"]["perception"]), ["classical_regions"])
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
            self.assertEqual(raw_state["steps"]["perception"]["status"], "empty")
            self.assertEqual(raw_state["steps"]["perception"]["plugin_runs"], ())
            self.assertEqual(raw_state["steps"]["perception"]["things"], ())

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
            reprocessed = runner.state()
            self.assertEqual(
                [run["plugin_id"] for run in reprocessed["steps"]["perception"]["plugin_runs"]],
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
                    for run in runner.state()["steps"]["perception"]["plugin_runs"]
                ],
                ["floor_continuity"],
            )
            runner.dispatch("reset", run_id=run_id)

    def test_steps_revision_changes_only_when_step_payloads_change(self) -> None:
        with image_source(3) as root:
            runner = ImageReplayRunner(root, cadence_ms=30000)
            runner.dispatch("select_plugins", active_plugin_ids=["classical_regions"])
            run_id, paused = _pause_after_first_frame(runner)
            revision = paused["steps_revision"]
            self.assertEqual(runner.state()["steps_revision"], revision)
            reselected = runner.dispatch(
                "select_plugins", active_plugin_ids=["floor_continuity"], run_id=run_id
            )
            self.assertGreater(reselected["steps_revision"], revision)
            self.assertEqual(runner.state()["steps_revision"], reselected["steps_revision"])
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
            self.assertEqual(_plugin_ids(paused["steps"]["perception"]), ["classical_regions"])
            recorded_report = paused["machine_detail"]["pipeline"]["perception_plugin_report"]
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
                _plugin_ids(both["steps"]["perception"]), ["floor_continuity", "classical_regions"]
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
            self.assertEqual(_plugin_ids(none["steps"]["perception"]), [])

            one = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=["floor_continuity"],
            )
            self.assertEqual(one["phase"], "paused")
            self.assertEqual(one["position"], position)
            self.assertEqual(runner._mapper.plugin_ids, ("floor_continuity",))
            self.assertEqual(_plugin_ids(one["steps"]["perception"]), ["floor_continuity"])

            stepped = runner.dispatch("step", run_id=run_id)
            self.assertEqual(stepped["phase"], "paused")
            self.assertEqual(_plugin_ids(stepped["steps"]["perception"]), ["floor_continuity"])
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
            self.assertEqual(_plugin_ids(stepped["steps"]["perception"]), ["classical_regions"])

            runner.dispatch(
                "select_plugins", run_id=run_id, active_plugin_ids=["floor_continuity"]
            )
            back = runner.dispatch("seek", run_id=run_id, position=0)
            self.assertEqual(back["current_frame"]["frame_id"], first_id)
            self.assertEqual(_plugin_ids(back["steps"]["perception"]), ["floor_continuity"])

            forward = runner.dispatch("seek", run_id=run_id, position=1)
            self.assertEqual(forward["current_frame"]["frame_id"], second_id)
            self.assertEqual(_plugin_ids(forward["steps"]["perception"]), ["floor_continuity"])

            again = runner.dispatch("seek", run_id=run_id, position=0)
            self.assertEqual(_plugin_ids(again["steps"]["perception"]), ["floor_continuity"])

            # Past the recorded frames, from an earlier position.
            ahead = runner.dispatch("seek", run_id=run_id, position=2)
            self.assertEqual(ahead["position"], 3)
            self.assertEqual(len(ahead["timeline"]), 3)
            self.assertEqual(_plugin_ids(ahead["steps"]["perception"]), ["floor_continuity"])
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
                _plugin_ids(both["steps"]["perception"]), ["floor_continuity", "classical_regions"]
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
                _plugin_ids(stepped["steps"]["perception"]),
                ["floor_continuity", "classical_regions"],
            )
            self.assertIs(memory_step.plugins[0], memory_plugin)
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
            shown = runner.wait(10)["steps"]["perception"]
        for key in ("status", "signals", "things", "limits"):
            self.assertEqual(canonical(shown[key]), canonical(inspected[key]), key)


class WorkbenchMemorySelectionTests(unittest.TestCase):
    def _runner(self, root, **kwargs) -> ImageReplayRunner:
        return ImageReplayRunner(
            root, cadence_ms=kwargs.pop("cadence_ms", 30000),
            mapper_factory=DecisionFixtureMapper, **kwargs,
        )

    def test_memory_catalog_lists_packaged_plugins_with_the_default_selected(self) -> None:
        state = ImageReplayRunner().state()
        catalog = state["memory_plugin_catalog"]
        self.assertEqual({item["id"] for item in catalog["plugins"]}, set(step_plugins("memory")))
        self.assertEqual(state["active_memory_plugin_ids"], ["bounded_evidence"])
        self.assertEqual(
            [item["id"] for item in catalog["plugins"] if item["active"]], ["bounded_evidence"]
        )
        self.assertEqual(catalog["digest"], packaged_plugin_catalog("memory").digest)
        self.assertNotEqual(catalog["digest"], state["plugin_catalog"]["digest"])

    def test_unknown_memory_plugins_and_other_steps_are_rejected(self) -> None:
        runner = ImageReplayRunner()
        with self.assertRaises(ReplayActionError) as unknown:
            runner.dispatch("select_plugins", step="memory", active_plugin_ids=["missing"])
        self.assertEqual(unknown.exception.status_code, 422)
        self.assertEqual(runner.state()["active_memory_plugin_ids"], ["bounded_evidence"])
        with self.assertRaises(ReplayActionError) as repeated:
            runner.dispatch(
                "select_plugins", step="memory", active_plugin_ids=["bounded_evidence"] * 2
            )
        self.assertEqual(repeated.exception.status_code, 422)
        for step in ("decision", "proposal", ""):
            with self.assertRaises(ReplayActionError, msg=step) as other:
                runner.dispatch("select_plugins", step=step, active_plugin_ids=[])
            self.assertEqual(other.exception.status_code, 400)
        with self.assertRaises(ReplayActionError) as misplaced:
            runner.dispatch("validate", step="memory")
        self.assertEqual(misplaced.exception.status_code, 400)
        # Perception stays the default step.
        runner.dispatch("select_plugins", active_plugin_ids=["classical_regions"])
        state = runner.state()
        self.assertEqual(state["active_plugin_ids"], ["classical_regions"])
        self.assertEqual(state["active_memory_plugin_ids"], ["bounded_evidence"])

    def test_memory_activation_equals_update_memory_manifest(self) -> None:
        with image_source(3) as root:
            for preset, entry in MEMORY_PRESETS.items():
                plugins = list(entry["plugins"])
                manifest = json.loads(update_vehicle_memory(
                    vehicle_id="workbench-parity", preset=preset,
                    dry_run=True, json_output=True,
                ).message)["manifest"]
                runner = self._runner(root)
                self.assertEqual(
                    runner.memory_plugin_catalog.activation(plugins).to_payload(), manifest, plugins
                )
                run_id, _ = _pause_after_first_frame(runner)
                runner.dispatch(
                    "select_plugins", run_id=run_id, step="memory", active_plugin_ids=plugins
                )
                self.assertEqual(runner._memory_step.activation.to_payload(), manifest, plugins)
                runner.dispatch("reset", run_id=run_id)

    def test_paused_memory_selection_rebuilds_memory_from_the_first_frame(self) -> None:
        with image_source(6) as root:
            runner = self._runner(root)
            run_id, _ = _pause_after_first_frame(runner)
            shown = runner.dispatch("seek", run_id=run_id, position=3)
            frame_id = shown["current_frame"]["frame_id"]
            self.assertEqual(runner._memory_step.update_count, 4)
            before = runner._memory_step

            selected = runner.dispatch(
                "select_plugins", run_id=run_id, step="memory",
                active_plugin_ids=["multi_obstruction_tracks"],
            )
            self.assertIsNot(runner._memory_step, before)
            # Memory ran over frames 0..3 under the new plugin, not only the displayed one.
            self.assertEqual(runner._memory_step.update_count, 4)
            self.assertEqual(runner._memory_step.plugin_ids, ("multi_obstruction_tracks",))
            self.assertEqual(selected["phase"], "paused")
            self.assertEqual(selected["position"], 4)
            self.assertEqual(len(selected["timeline"]), 4)
            self.assertEqual(selected["current_frame"]["frame_id"], frame_id)
            self.assertEqual(selected["active_memory_plugin_ids"], ["multi_obstruction_tracks"])
            self.assertEqual(
                [item["id"] for item in selected["memory_plugin_catalog"]["plugins"] if item["active"]],
                ["multi_obstruction_tracks"],
            )
            self.assertEqual(selected["steps"]["memory"]["plugin_id"], "multi_obstruction_tracks")
            self.assertEqual(
                selected["machine_detail"]["pipeline"]["memory_plugin_report"]["applied_plugin_ids"],
                ["multi_obstruction_tracks"],
            )
            # The perception selection is untouched.
            self.assertEqual(selected["active_plugin_ids"], shown["active_plugin_ids"])

            unchanged = runner.dispatch(
                "select_plugins", run_id=run_id, step="memory",
                active_plugin_ids=["multi_obstruction_tracks"],
            )
            self.assertEqual(runner._memory_step.update_count, 4)
            self.assertEqual(unchanged["position"], 4)

            empty = runner.dispatch(
                "select_plugins", run_id=run_id, step="memory", active_plugin_ids=[]
            )
            self.assertEqual(empty["position"], 4)
            self.assertEqual(runner._memory_step.plugin_ids, ())
            runner.dispatch("reset", run_id=run_id)

    def test_memory_selection_before_start_applies_to_the_run(self) -> None:
        with image_source(2) as root:
            runner = self._runner(root, cadence_ms=0)
            runner.dispatch(
                "select_plugins", step="memory", active_plugin_ids=["multi_obstruction_tracks"]
            )
            runner.start()
            state = runner.wait(10)
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["steps"]["memory"]["plugin_id"], "multi_obstruction_tracks")

    def test_running_memory_selection_restarts_the_pass_and_the_replay_finishes(self) -> None:
        with image_source(12) as root:
            runner = self._runner(root, cadence_ms=40)
            started = runner.start()
            _wait_until(lambda: runner.state()["position"] >= 3)
            runner.dispatch(
                "select_plugins", run_id=started["run_id"], step="memory",
                active_plugin_ids=["multi_obstruction_tracks"],
            )
            state = runner.wait(10)
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(len(state["timeline"]), 12)
        self.assertEqual(state["steps"]["memory"]["plugin_id"], "multi_obstruction_tracks")
        self.assertEqual(
            state["machine_detail"]["pipeline"]["memory_plugin_report"]["applied_plugin_ids"],
            ["multi_obstruction_tracks"],
        )

    def test_memory_selection_that_cannot_be_built_leaves_the_replay_as_it_was(self) -> None:
        from cli.automa_cli.workbench_frames import default_memory_step

        builds = []

        def memory_step_factory():
            builds.append(1)
            if len(builds) > 1:
                raise RuntimeError("cannot build memory")
            return default_memory_step()

        with image_source(4) as root:
            runner = self._runner(root, memory_step_factory=memory_step_factory)
            run_id, paused = _pause_after_first_frame(runner)
            before = runner._memory_step
            with self.assertRaises(ReplayActionError) as caught:
                runner.dispatch(
                    "select_plugins", run_id=run_id, step="memory",
                    active_plugin_ids=["multi_obstruction_tracks"],
                )
            state = runner.state()
            self.assertEqual(caught.exception.status_code, 422)
            self.assertEqual(state["phase"], "paused")
            self.assertEqual(state["position"], paused["position"])
            self.assertEqual(state["timeline"], paused["timeline"])
            self.assertEqual(state["active_memory_plugin_ids"], ["bounded_evidence"])
            self.assertIs(runner._memory_step, before)
            runner.dispatch("reset", run_id=run_id)
