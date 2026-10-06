from __future__ import annotations
import json
import unittest
from unittest import mock
from autonomy.decision_cycle.memory.interface import MEMORY_REPORT_SCHEMA
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.perception.feeds.context import PerceptionRequest
from autonomy.vehicle import SensorFrame
from implementations.decision_cycle.catalog import selection_activation, step_plugins
from implementations.decision_cycle.memory.presets import MEMORY_PRESETS
from implementations.decision_cycle.perception.presets import PERCEPTION_PRESETS
from cli.automa_cli.memory import update_vehicle_memory
from cli.automa_cli.workbench_plugins import packaged_plugin_catalog
from cli.automa_cli.workbench import PluginCatalogError, ReplayActionError
from tests.cli.workbench_fixtures import (
    DecisionFixtureMapper,
    ImageReplayRunner,
    _wait_until,
    image_source,
    perception_activations,
)


def _empty_memory_report() -> dict:
    return {"schema": MEMORY_REPORT_SCHEMA, "plugins": [], "evidence_publisher": None}


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
        self.assertEqual(ImageReplayRunner().state()["active_perception_plugin_ids"], ["frame", "floor_plane"])
        perception_step = catalog.build(catalog.activation(["frame"]))
        self.assertEqual(set(perception_step.plugin_manager.available_ids), packaged)
        original = perception_step.plugins["frame"]
        perception_step.plugin_manager.add("floor_plane")
        perception_step.perceive(PerceptionRequest(SensorFrame(
            read_id="test", readings={}, started_at_ms=100, completed_at_ms=100,
        )))
        self.assertEqual(perception_step.plugin_ids, ("frame", "floor_plane"))
        self.assertIs(perception_step.plugins["frame"], original)

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
        perception_step = catalog.build(
            catalog.activation(["floor_continuity", "classical_regions"])
        )
        perception = perception_step.perceive(PerceptionRequest(SensorFrame(
            read_id="ordered", readings={}, started_at_ms=100, completed_at_ms=100,
        )))
        self.assertEqual(
            [run.plugin_id for run in perception.plugin_runs],
            ["floor_continuity", "classical_regions"],
        )

    def test_workbench_can_run_packaged_plugin_outside_default_selection(self) -> None:
        with image_source(1) as root:
            runner = ImageReplayRunner(
                root, activations=perception_activations("classical_regions"), cadence_ms=0
            )
            runner.start()
            state = runner.wait(10)
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["active_perception_plugin_ids"], ["classical_regions"])
        self.assertEqual(_plugin_ids(state["steps"]["perception"]), ["classical_regions"])
        self.assertEqual(
            state["machine_detail"]["pipeline"]["perception_preset"], "custom"
        )

    def test_empty_selection_replays_raw_capture_and_allows_live_replacement(self) -> None:
        with image_source(3) as root:
            runner = ImageReplayRunner(root, activations=perception_activations(), cadence_ms=0)
            raw_started = runner.start()
            self.assertEqual(raw_started["phase"], "running")
            self.assertEqual(raw_started["active_perception_plugin_ids"], [])
            raw_state = runner.wait(10)
            self.assertEqual(raw_state["phase"], "completed")
            self.assertEqual(raw_state["active_perception_plugin_ids"], [])
            self.assertEqual(raw_state["active_perception_plugin_ids"], [])
            self.assertEqual(raw_state["steps"]["perception"]["status"], "empty")
            self.assertEqual(raw_state["steps"]["perception"]["plugin_runs"], ())
            self.assertEqual(raw_state["steps"]["perception"]["things"], ())

            runner.dispatch(
                "select_plugins",
                step="perception",
                run_id=raw_started["run_id"],
                active_plugin_ids=["classical_regions"],
            )
            started = runner.start(cadence_ms=30000)
            run_id = started["run_id"]
            _wait_until(lambda: runner.state()["position"] == 1)
            first_id = runner.state()["timeline"][0]["frame"]["frame_id"]
            perception_step = runner._steps["perception"]
            runner._shared_memory["retention-marker"] = "dropped"
            selected = runner.dispatch(
                "select_plugins",
                step="perception",
                run_id=run_id,
                active_plugin_ids=["floor_continuity"],
            )
            self.assertEqual(selected["phase"], "running")
            self.assertEqual(selected["active_perception_plugin_ids"], ["floor_continuity"])
            # The pass starts over and the displayed frame runs again before
            # the action returns.
            self.assertIsNot(runner._steps["perception"], perception_step)
            self.assertNotIn("retention-marker", runner._shared_memory)
            self.assertEqual(selected["position"], 1)
            self.assertEqual(selected["current_frame"]["frame_id"], first_id)
            self.assertEqual(_plugin_ids(selected["steps"]["perception"]), ["floor_continuity"])
            with self.assertRaises(ReplayActionError):
                runner.dispatch(
                    "select_plugins",
                    step="perception",
                    run_id=run_id,
                    active_plugin_ids=["unknown"],
                )
            self.assertEqual(
                runner.state()["active_perception_plugin_ids"], ["floor_continuity"]
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
            runner.dispatch("select_plugins", step="perception", active_plugin_ids=["classical_regions"])
            run_id, paused = _pause_after_first_frame(runner)
            revision = paused["steps_revision"]
            self.assertEqual(runner.state()["steps_revision"], revision)
            reselected = runner.dispatch(
                "select_plugins", step="perception", active_plugin_ids=["floor_continuity"], run_id=run_id
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
                step="perception",
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
                step="perception",
                run_id=run_id,
                active_plugin_ids=["floor_continuity", "classical_regions"],
            )
            self.assertEqual(both["phase"], "paused")
            self.assertEqual(both["position"], position)
            self.assertEqual(len(both["timeline"]), timeline_len)
            self.assertEqual(
                both["active_perception_plugin_ids"], ["floor_continuity", "classical_regions"]
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
                step="perception",
                run_id=run_id,
                active_plugin_ids=[],
            )
            self.assertEqual(none["phase"], "paused")
            self.assertEqual(none["active_perception_plugin_ids"], [])
            self.assertEqual(_plugin_ids(none["steps"]["perception"]), [])

            one = runner.dispatch(
                "select_plugins",
                step="perception",
                run_id=run_id,
                active_plugin_ids=["floor_continuity"],
            )
            self.assertEqual(one["phase"], "paused")
            self.assertEqual(one["position"], position)
            self.assertEqual(runner._steps["perception"].plugin_ids, ("floor_continuity",))
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
            runner.dispatch("select_plugins", step="perception", active_plugin_ids=["classical_regions"])
            run_id, paused = _pause_after_first_frame(runner)
            first_id = paused["current_frame"]["frame_id"]
            stepped = runner.dispatch("step", run_id=run_id)
            second_id = stepped["current_frame"]["frame_id"]
            self.assertEqual(_plugin_ids(stepped["steps"]["perception"]), ["classical_regions"])

            runner.dispatch(
                "select_plugins", step="perception", run_id=run_id, active_plugin_ids=["floor_continuity"]
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

    def test_paused_perception_selection_rebuilds_the_pass_from_the_first_frame(self) -> None:
        with image_source(6) as root:
            runner = ImageReplayRunner(root, cadence_ms=30000)
            runner.dispatch("select_plugins", step="perception", active_plugin_ids=["classical_regions"])
            run_id, _ = _pause_after_first_frame(runner)
            shown = runner.dispatch("seek", run_id=run_id, position=3)
            frame_id = shown["current_frame"]["frame_id"]
            perception_step = runner._steps["perception"]
            memory_step = runner._steps["memory"]
            self.assertEqual(memory_step.update_count, 4)
            runner._shared_memory["retention-marker"] = "dropped"

            both = runner.dispatch(
                "select_plugins",
                step="perception",
                run_id=run_id,
                active_plugin_ids=["floor_continuity", "classical_regions"],
            )
            self.assertIsNot(runner._steps["perception"], perception_step)
            self.assertIsNot(runner._steps["memory"], memory_step)
            self.assertNotIn("retention-marker", runner._shared_memory)
            # Frames 0..3 ran under the new selection, not only the displayed one.
            self.assertEqual(runner._steps["memory"].update_count, 4)
            self.assertEqual(both["phase"], "paused")
            self.assertEqual(both["position"], 4)
            self.assertEqual(len(both["timeline"]), 4)
            self.assertEqual(both["current_frame"]["frame_id"], frame_id)
            self.assertEqual(
                _plugin_ids(both["steps"]["perception"]), ["floor_continuity", "classical_regions"]
            )
            self.assertEqual(
                both["machine_detail"]["pipeline"]["perception_plugin_report"]["applied_plugin_ids"],
                ["floor_continuity", "classical_regions"],
            )
            # The memory selection is untouched.
            self.assertEqual(both["active_memory_plugin_ids"], shown["active_memory_plugin_ids"])
            self.assertEqual(runner._steps["memory"].plugin_ids, ("bounded_evidence",))

            rebuilt = runner._steps["perception"]
            unchanged = runner.dispatch(
                "select_plugins",
                step="perception",
                run_id=run_id,
                active_plugin_ids=["floor_continuity", "classical_regions"],
            )
            self.assertIs(runner._steps["perception"], rebuilt)
            self.assertEqual(unchanged["position"], 4)

            back = runner.dispatch("seek", run_id=run_id, position=0)
            self.assertEqual(
                _plugin_ids(back["steps"]["perception"]), ["floor_continuity", "classical_regions"]
            )
            reset = runner.dispatch("reset", run_id=run_id)
            self.assertEqual(reset["phase"], "idle")
            self.assertEqual(reset["timeline"], [])
            self.assertEqual(runner._shared_memory, {})

    def test_running_perception_selection_restarts_the_pass_and_the_replay_finishes(self) -> None:
        with image_source(12) as root:
            runner = ImageReplayRunner(
                root, activations=perception_activations("classical_regions"), cadence_ms=40
            )
            started = runner.start()
            _wait_until(lambda: runner.state()["position"] >= 3)
            memory_step = runner._steps["memory"]
            runner.dispatch(
                "select_plugins", step="perception", run_id=started["run_id"], active_plugin_ids=["floor_continuity"]
            )
            rebuilt = runner._steps["memory"]
            state = runner.wait(10)
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(len(state["timeline"]), 12)
        self.assertIsNot(rebuilt, memory_step)
        # Every frame ran once on the rebuilt pipelines.
        self.assertEqual(rebuilt.update_count, 12)
        self.assertEqual(_plugin_ids(state["steps"]["perception"]), ["floor_continuity"])
        self.assertEqual(state["active_perception_plugin_ids"], ["floor_continuity"])

    def test_running_perception_selection_that_cannot_be_built_keeps_the_working_run(self) -> None:
        from autonomy.decision_cycle import runner as step_runner

        instantiate = step_runner.instantiate_plugin

        def failing_instantiate(definition):
            if definition.plugin_id == "floor_continuity":
                raise RuntimeError("constructor failed")
            return instantiate(definition)

        with image_source(3) as root:
            runner = ImageReplayRunner(root, cadence_ms=30000)
            runner.dispatch("select_plugins", step="perception", active_plugin_ids=["classical_regions"])
            run_id = runner.start()["run_id"]
            _wait_until(lambda: runner.state()["position"] == 1)
            before = runner.state()
            self.assertEqual(before["phase"], "running")
            perception_step = runner._steps["perception"]
            memory_step = runner._steps["memory"]
            retained = perception_step.plugins["classical_regions"]
            runner._shared_memory["retention-marker"] = "kept"

            with mock.patch.object(step_runner, "instantiate_plugin", failing_instantiate):
                with self.assertRaises(ReplayActionError) as caught:
                    runner.dispatch(
                        "select_plugins", step="perception", run_id=run_id,
                        active_plugin_ids=["floor_continuity", "classical_regions"],
                    )

            self.assertEqual(caught.exception.status_code, 422)
            self.assertEqual(caught.exception.boundary, "plugin_catalog")
            self.assertIn("constructor failed", str(caught.exception))
            state = runner.state()
            self.assertEqual(state["phase"], "running")
            self.assertEqual(state["position"], before["position"])
            self.assertEqual(state["failure_boundary"], "plugin_catalog")
            self.assertEqual(state["active_perception_plugin_ids"], ["classical_regions"])
            self.assertIs(runner._steps["perception"], perception_step)
            self.assertIs(runner._steps["memory"], memory_step)
            self.assertEqual(perception_step.plugin_manager.selected_ids, ("classical_regions",))
            self.assertEqual(perception_step.plugins, {"classical_regions": retained})
            self.assertEqual(runner._shared_memory["retention-marker"], "kept")

            runner.dispatch("pause", run_id=run_id)
            stepped = runner.dispatch("step", run_id=run_id)
            self.assertEqual(stepped["phase"], "paused")
            self.assertEqual(_plugin_ids(stepped["steps"]["perception"]), ["classical_regions"])
            self.assertIs(perception_step.plugins["classical_regions"], retained)
            runner.dispatch("reset", run_id=run_id)


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
            runner = ImageReplayRunner(
                root, activations=perception_activations("classical_regions"), cadence_ms=0
            )
            runner.start()
            shown = runner.wait(10)["steps"]["perception"]
        for key in ("status", "signals", "things", "limits"):
            self.assertEqual(canonical(shown[key]), canonical(inspected[key]), key)


class WorkbenchStartingSelectionTests(unittest.TestCase):
    """Each step starts from the activation the CLI resolved for it."""

    def test_perception_preset_activation_equals_update_perception_selection(self) -> None:
        # `vehicles update perception --preset` stages this same activation.
        with image_source(2) as root:
            for preset in PERCEPTION_PRESETS:
                activation = selection_activation("perception", preset=preset)
                runner = ImageReplayRunner(
                    root, activations={"perception": activation}, cadence_ms=30000
                )
                run_id, paused = _pause_after_first_frame(runner)
                pipeline = paused["machine_detail"]["pipeline"]
                self.assertEqual(pipeline["perception_preset"], preset)
                self.assertEqual(paused["active_perception_plugin_ids"], list(activation.plugins))
                self.assertEqual(
                    runner._steps["perception"].activation.to_payload(), activation.to_payload(), preset
                )
                runner.dispatch("reset", run_id=run_id)

    def test_reselecting_a_presets_plugins_keeps_its_configs(self) -> None:
        activation = selection_activation("perception", preset="obstruction_observer")
        self.assertIn("multi_obstruction_tracks", activation.plugin_configs)
        plugins = list(activation.plugins)
        with image_source(3) as root:
            runner = ImageReplayRunner(
                root, activations={"perception": activation}, cadence_ms=30000
            )
            idle = runner.dispatch("select_plugins", step="perception", active_plugin_ids=plugins)
            self.assertEqual(
                idle["machine_detail"]["pipeline"]["perception_preset"], "obstruction_observer"
            )
            run_id, _ = _pause_after_first_frame(runner)
            built = runner._steps["perception"]
            same = runner.dispatch(
                "select_plugins", run_id=run_id, step="perception", active_plugin_ids=plugins
            )
            self.assertIs(runner._steps["perception"], built)
            self.assertEqual(
                same["machine_detail"]["pipeline"]["perception_preset"], "obstruction_observer"
            )
            self.assertEqual(built.activation.to_payload(), activation.to_payload())

            changed = runner.dispatch(
                "select_plugins", run_id=run_id, step="perception", active_plugin_ids=["frame"]
            )
            self.assertEqual(changed["machine_detail"]["pipeline"]["perception_preset"], "custom")
            self.assertEqual(
                runner._steps["perception"].activation.to_payload(),
                selection_activation("perception", plugins=["frame"]).to_payload(),
            )
            runner.dispatch("reset", run_id=run_id)

    def test_memory_starts_from_its_given_activation(self) -> None:
        with image_source(2) as root:
            runner = ImageReplayRunner(
                root,
                activations={"memory": selection_activation("memory", plugins=[])},
                cadence_ms=0,
                step_factories={"perception": DecisionFixtureMapper},
            )
            idle = runner.state()
            self.assertEqual(idle["active_memory_plugin_ids"], [])
            self.assertEqual(idle["machine_detail"]["pipeline"]["memory_preset"], "custom")
            runner.start()
            state = runner.wait(10)
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["steps"]["memory"], _empty_memory_report())
        self.assertEqual(
            state["machine_detail"]["pipeline"]["memory_plugin_report"]["applied_plugin_ids"], []
        )
        # The other step keeps its default preset.
        self.assertEqual(state["active_perception_plugin_ids"], ["frame", "floor_plane"])
        self.assertEqual(
            state["machine_detail"]["pipeline"]["perception_preset"], "lightweight_observer"
        )

    def test_an_activation_must_be_given_as_its_own_step(self) -> None:
        with self.assertRaisesRegex(ValueError, "activation for 'memory' given as 'perception'"):
            ImageReplayRunner(activations={"perception": selection_activation("memory")})


class WorkbenchMemorySelectionTests(unittest.TestCase):
    def _runner(self, root, **kwargs) -> ImageReplayRunner:
        return ImageReplayRunner(
            root, cadence_ms=kwargs.pop("cadence_ms", 30000),
            step_factories={"perception": DecisionFixtureMapper, **kwargs.pop("step_factories", {})},
            **kwargs,
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
        self.assertNotEqual(catalog["digest"], state["perception_plugin_catalog"]["digest"])

    def test_unknown_memory_plugins_and_missing_or_other_steps_are_rejected(self) -> None:
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
        for step in ("decision", "plan", ""):
            with self.assertRaises(ReplayActionError, msg=step) as other:
                runner.dispatch("select_plugins", step=step, active_plugin_ids=[])
            self.assertEqual(other.exception.status_code, 400)
        with self.assertRaises(ReplayActionError) as misplaced:
            runner.dispatch("validate", step="memory")
        self.assertEqual(misplaced.exception.status_code, 400)
        # Neither step is assumed.
        with self.assertRaisesRegex(ReplayActionError, "requires step") as missing:
            runner.dispatch("select_plugins", active_plugin_ids=["classical_regions"])
        self.assertEqual(missing.exception.status_code, 400)
        state = runner.state()
        self.assertEqual(state["active_perception_plugin_ids"], ["frame", "floor_plane"])
        self.assertEqual(state["active_memory_plugin_ids"], ["bounded_evidence"])

    def test_memory_activation_equals_update_memory_manifest(self) -> None:
        with image_source(3) as root:
            for preset, entry in MEMORY_PRESETS.items():
                plugins = list(entry["plugins"])
                manifest = json.loads(update_vehicle_memory(
                    vehicle_id="chase-sim-chaser", preset=preset,
                    dry_run=True, json_output=True,
                ).message)["manifest"]
                runner = self._runner(root)
                self.assertEqual(
                    runner._catalogs["memory"].activation(plugins).to_payload(), manifest, plugins
                )
                run_id, _ = _pause_after_first_frame(runner)
                runner.dispatch(
                    "select_plugins", run_id=run_id, step="memory", active_plugin_ids=plugins
                )
                self.assertEqual(runner._steps["memory"].activation.to_payload(), manifest, plugins)
                runner.dispatch("reset", run_id=run_id)

    def test_paused_memory_selection_rebuilds_memory_from_the_first_frame(self) -> None:
        with image_source(6) as root:
            runner = self._runner(root)
            run_id, _ = _pause_after_first_frame(runner)
            shown = runner.dispatch("seek", run_id=run_id, position=3)
            frame_id = shown["current_frame"]["frame_id"]
            self.assertEqual(runner._steps["memory"].update_count, 4)
            before = runner._steps["memory"]

            selected = runner.dispatch(
                "select_plugins", run_id=run_id, step="memory", active_plugin_ids=[]
            )
            self.assertIsNot(runner._steps["memory"], before)
            # Memory ran over frames 0..3 under the new selection, not only the displayed one.
            self.assertEqual(runner._steps["memory"].update_count, 4)
            self.assertEqual(runner._steps["memory"].plugin_ids, ())
            self.assertEqual(selected["phase"], "paused")
            self.assertEqual(selected["position"], 4)
            self.assertEqual(len(selected["timeline"]), 4)
            self.assertEqual(selected["current_frame"]["frame_id"], frame_id)
            self.assertEqual(selected["active_memory_plugin_ids"], [])
            self.assertEqual(
                [item["id"] for item in selected["memory_plugin_catalog"]["plugins"] if item["active"]],
                [],
            )
            self.assertEqual(selected["steps"]["memory"], _empty_memory_report())
            self.assertEqual(
                selected["machine_detail"]["pipeline"]["memory_plugin_report"]["applied_plugin_ids"],
                [],
            )
            # The perception selection is untouched.
            self.assertEqual(selected["active_perception_plugin_ids"], shown["active_perception_plugin_ids"])

            emptied = runner._steps["memory"]
            unchanged = runner.dispatch(
                "select_plugins", run_id=run_id, step="memory", active_plugin_ids=[]
            )
            self.assertIs(runner._steps["memory"], emptied)
            self.assertEqual(unchanged["position"], 4)

            restored = runner.dispatch(
                "select_plugins", run_id=run_id, step="memory", active_plugin_ids=["bounded_evidence"]
            )
            self.assertEqual(restored["position"], 4)
            self.assertEqual(runner._steps["memory"].update_count, 4)
            self.assertEqual(runner._steps["memory"].plugin_ids, ("bounded_evidence",))
            self.assertEqual(restored["steps"]["memory"]["plugins"][0]["plugin_id"], "bounded_evidence")
            self.assertEqual(restored["steps"]["memory"]["evidence_publisher"], "bounded_evidence")
            runner.dispatch("reset", run_id=run_id)

    def test_memory_selection_before_start_applies_to_the_run(self) -> None:
        with image_source(2) as root:
            runner = self._runner(root, cadence_ms=0)
            runner.dispatch("select_plugins", step="memory", active_plugin_ids=[])
            runner.start()
            state = runner.wait(10)
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["steps"]["memory"], _empty_memory_report())
        self.assertEqual(
            state["machine_detail"]["pipeline"]["memory_plugin_report"]["applied_plugin_ids"], []
        )

    def test_running_memory_selection_restarts_the_pass_and_the_replay_finishes(self) -> None:
        with image_source(12) as root:
            runner = self._runner(root, cadence_ms=40)
            started = runner.start()
            _wait_until(lambda: runner.state()["position"] >= 3)
            runner.dispatch(
                "select_plugins", run_id=started["run_id"], step="memory", active_plugin_ids=[]
            )
            state = runner.wait(10)
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(len(state["timeline"]), 12)
        self.assertEqual(state["steps"]["memory"], _empty_memory_report())
        self.assertEqual(
            state["machine_detail"]["pipeline"]["memory_plugin_report"]["applied_plugin_ids"], []
        )

    def test_memory_selection_that_cannot_be_built_leaves_the_replay_as_it_was(self) -> None:
        builds = []

        def memory_step_factory():
            builds.append(1)
            if len(builds) > 1:
                raise RuntimeError("cannot build memory")
            return MemoryRunner.from_activation(selection_activation("memory"))

        with image_source(4) as root:
            runner = self._runner(root, step_factories={"memory": memory_step_factory})
            run_id, paused = _pause_after_first_frame(runner)
            before = runner._steps["memory"]
            with self.assertRaises(ReplayActionError) as caught:
                runner.dispatch(
                    "select_plugins", run_id=run_id, step="memory", active_plugin_ids=[]
                )
            state = runner.state()
            self.assertEqual(caught.exception.status_code, 422)
            self.assertEqual(state["phase"], "paused")
            self.assertEqual(state["position"], paused["position"])
            self.assertEqual(state["timeline"], paused["timeline"])
            self.assertEqual(state["active_memory_plugin_ids"], ["bounded_evidence"])
            self.assertIs(runner._steps["memory"], before)
            runner.dispatch("reset", run_id=run_id)
