"""Inspect and workbench apply the same recorded selections and overrides."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from autonomy.decision_cycle.activation import STEPS, activation_for_definitions, activation_generation_id
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.plugins import LocalPluginCatalog, uploaded_source
from autonomy.runtime.plugin_catalog import PluginCatalogAPI, upload_collection
from autonomy.runtime.recording import UPLOADED_SOURCES_DIR, RecordingWriter
from autonomy.shared_memory import shared_memory_document
from cli.automa_cli.plugins.inspect.commands import inspect_step
from cli.automa_cli.replay.frames import run_frame
from cli.automa_cli.plugins.inspect.replay import read_replay_source

from cli.automa_cli.replay.selections import default_selection
from cli.automa_cli.shared.collection_manifest import read_manifest
from cli.automa_cli.workbench.replay import run_workbench_replay
from cli.automa_cli.workbench.runner import ImageReplayRunner
from implementations.decision_cycle.catalog import packaged_activation
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.memory.frame_fixtures import write_frames
from tests.shared.cli.workbench_fixtures import replay_states


class ReplaySelectionTests(unittest.TestCase):
    def test_absent_empty_and_configured_memory_match_in_both_replay_callers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = write_frames(Path(directory) / "source", count=3)
            configured = default_selection("memory")
            empty = replace(configured, plugins=())
            for selection in (None, empty, configured):
                with self.subTest(selection=selection):
                    (source / "manifest.json").write_text(json.dumps({
                        "frames": [{
                            "image_path": f"frame_{i}.png",
                            "steps": {"memory": selection.to_payload() if selection else None},
                        } for i in range(3)],
                    }))
                    inspected = inspect_step("memory", source=source, json_output=True)
                    self.assertEqual(inspected.exit_code, 0, inspected.message)
                    expected = json.loads(inspected.message)["frames"][-1]["record"]
                    runner = ImageReplayRunner(source, cadence_ms=0, loop=False)
                    try:
                        runner.start()
                        completed = runner.wait(5)
                        self.assertEqual(completed["phase"], "completed", completed.get("failure"))
                        self.assertEqual(json.loads(json.dumps(completed["cycle"]["memory"])), expected)
                    finally:
                        runner.close()
                    if selection is None:
                        self.assertIsNone(expected)
                    elif not selection.plugins:
                        self.assertEqual(expected["plugins"], [])
                    else:
                        self.assertGreater(expected["plugins"][0]["state"]["record_count"], 0)

    def test_recorded_restages_and_an_explicit_override_match_in_both_callers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = write_frames(Path(directory) / "source", count=3)
            default = default_selection("memory")
            empty = replace(default, plugins=())
            (source / "manifest.json").write_text(json.dumps({
                "frames": [
                    {"image_path": "frame_0.png", "steps": {"memory": default.to_payload()}},
                    {"image_path": "frame_1.png", "steps": {"memory": None}},
                    {"image_path": "frame_2.png", "steps": {"memory": empty.to_payload()}},
                ],
            }))
            for overridden in (False, True):
                options = {"plugins": ["bounded_evidence"]} if overridden else {}
                inspected = inspect_step("memory", source=source, json_output=True, **options)
                self.assertEqual(inspected.exit_code, 0, inspected.message)
                expected = json.loads(inspected.message)
                runner = ImageReplayRunner(source, cadence_ms=0, loop=False,
                                           activations={"memory": default} if overridden else None)
                try:
                    states = replay_states(runner)
                    completed = states[-1]
                    self.assertEqual(completed["phase"], "completed", completed.get("failure"))
                    self.assertEqual(json.loads(json.dumps(completed["cycle"]["memory"])), expected["frames"][-1]["record"])
                    self.assertEqual([row["summary"]["memory_plugins"] for row in states],
                                     [([] if frame["record"] is None else [
                                         {"plugin_id": item["plugin_id"], **{key: item["state"].get(key) for key in
                                          ("health", "record_count", "epoch_id")}}
                                         for item in frame["record"]["plugins"]]) for frame in expected["frames"]])
                finally:
                    runner.close()

    def test_manifest_root_selections_do_not_select_plugins(self):
        with tempfile.TemporaryDirectory() as directory:
            source = write_frames(Path(directory) / "source", count=1)
            (source / "manifest.json").write_text(json.dumps({
                "steps": {"memory": None},
                "frames": [{"image_path": "frame_0.png"}],
            }))
            inspected = inspect_step("memory", source=source, json_output=True)
            self.assertEqual(inspected.exit_code, 0, inspected.message)
            expected = json.loads(inspected.message)["frames"][0]["record"]
            self.assertEqual([item["plugin_id"] for item in expected["plugins"]], ["bounded_evidence"])
            runner = ImageReplayRunner(source, cadence_ms=0, loop=False)
            try:
                runner.start()
                completed = runner.wait(5)
                self.assertEqual(completed["phase"], "completed", completed.get("failure"))
                self.assertEqual(json.loads(json.dumps(completed["cycle"]["memory"])), expected)
            finally:
                runner.close()

    def test_replay_uses_normal_perception_runner_status_reset_and_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            source = write_frames(Path(directory) / "source", count=1)
            images = read_replay_source(source, max_frames=512)
            frame = replace(images.frames[0], frame_id="frame-7", frame_index=7)
            mapper = PerceptionRunner.from_activation(default_selection("perception"))
            memory = {}
            requests = []
            perceive = mapper.perceive
            mapper.perceive = lambda request: requests.append(request) or perceive(request)
            result = run_frame(frame, steps={"perception": mapper}, shared_memory=memory)
            self.assertEqual(mapper.last_frame_index, 7)
            self.assertEqual(requests[0].metadata["source_id"], frame.source_id)
            self.assertEqual(requests[0].metadata["sequence_index"], frame.position)
            self.assertEqual(requests[0].metadata["frame_index"], frame.frame_index)
            self.assertIs(mapper.last_output, result.result.perception)
            timing = result.result.timing["steps"][0]
            self.assertEqual(timing["step"], "perception")
            self.assertEqual([item["plugin_id"] for item in timing["plugins"]], list(mapper.plugin_ids))
            with patch.object(mapper, "reset", wraps=mapper.reset) as reset:
                absent = run_frame(
                    replace(frame, frame_id="gap", frame_index=8, image_path=None,
                            absence_reason="camera missing"),
                    steps={"perception": mapper}, shared_memory=memory,
                )
                reset.assert_called_once_with(memory)
            self.assertIsNone(absent.result.perception)
            self.assertEqual(mapper.last_frame_index, 8)


_PROBE = """
from autonomy.decision_cycle.perception.evidence.values import PerceptionEvidenceBatch
from autonomy.decision_cycle.perception.plugin import PerceptionPluginContract

class {factory}:
    plugin_id = "probe"
    contract = PerceptionPluginContract(inputs=())

    def __init__(self, offset):
        self.offset = offset

    def perceive(self, inputs):
        return PerceptionEvidenceBatch(measurements={{"offset": self.offset}})
"""


class CollectionReplayTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.images = write_frames(self.root / "images", count=2)
        self.source = self.root / "recording"
        self.manifest = self.collection("variant", offset=7, factory="Variant")
        self.provenance = {"path": str(self.manifest), "collection": "variant", "replaced_plugins": ["perception/probe"]}

    def collection(self, name, *, offset, factory="Probe"):
        root = self.root / name
        root.mkdir()
        (root / "probe.py").write_text(_PROBE.format(factory=factory))
        path = root / "manifest.json"
        path.write_text(json.dumps({"name": name, "plugins": [
            {"name": "perception/probe", "entrypoint": f"probe:{factory}", "config": {"offset": offset}},
        ]}))
        return path

    def recording(self, *, change=lambda steps: steps):
        catalog = LocalPluginCatalog()
        _, (probe,) = upload_collection(catalog, read_manifest(self.collection("baseline", offset=2)))
        perception = packaged_activation("perception", ["frame"])
        probe_activation = activation_for_definitions("perception", [probe])
        initial = {step: default_selection(step) for step in STEPS}
        initial.update(
            perception=replace(
                perception, plugins=("probe", "frame"),
                plugin_specs={**perception.plugin_specs, **probe_activation.plugin_specs},
                plugin_configs=probe_activation.plugin_configs,
                metadata=probe_activation.metadata,
            ),
            memory=packaged_activation("memory", ["bounded_evidence"],
                                       config_overrides={"bounded_evidence": {"max_records": 3}}),
            action=packaged_activation("action", ["mode"]),
        )
        writer = RecordingWriter(self.source, host_name="fixture", run_id="recording")
        for index, steps in enumerate((initial, change(dict(initial)))):
            generation = activation_generation_id(steps, prefix="decision")
            writer.store_generation(generation, {step: activation.to_payload() for step, activation in steps.items()},
                                    lambda name: uploaded_source(name).read_bytes())
            writer.commit({
                "host_name": "fixture", "run_id": "recording", "generation_id": generation,
                "frame_id": f"frame_{index}", "frame_index": index, "timestamp_ms": 1000 + index,
                "mode": "autonomy", "user_steering": .25, "user_throttle": .5,
            }, (self.images / f"frame_{index}.png").read_bytes(), ".png")

    def workbench(self, *, manifest=None):
        events = []
        result = run_workbench_replay(self.source, manifest=manifest, pace="realtime", cadence_ms=0,
                                      emit=events.append, view_server=None)
        self.assertEqual(result.exit_code, 0, result.error or events[-1].state["failure"])
        return events[-1].state

    def inspect(self, source, *options):
        return json.loads(invoke_automa(
            "plugins", "perception", "inspect", str(source), "--json", *options,
            extra_env={"AUTOMA_PERCEPTION_INSPECT_ROOT": str(self.root / "inspections")},
        ).stdout)

    def failures(self, source=None, manifest=None):
        source = str(source or self.source)
        arguments = (source, "--manifest", str(manifest or self.manifest))
        for command in (("workbench", *arguments, "--cadence-ms", "0"),
                        ("plugins", "inspect", "perception", *arguments, "--frame", "0")):
            result = invoke_automa(*command, check=False)
            self.assertEqual(result.returncode, 2, result.stdout)
            if command[0] == "workbench":
                self.assertIn("progress: 0/0", result.stdout)
            yield result.stdout

    def test_collection_changes_one_plugin_and_preserves_other_records_and_order(self):
        self.recording()
        plain = self.workbench()
        changed = self.workbench(manifest=self.manifest)
        self.assertEqual(changed["collection_manifest"], self.provenance)
        self.assertEqual(changed["current_frame"], plain["current_frame"])
        self.assertEqual(changed["cycle"]["perception"]["measurements"]["probe"], {"offset": 7})
        for step in STEPS[1:]:
            self.assertEqual(changed["cycle"][step], plain["cycle"][step], step)

        baseline = self.inspect(self.source)
        variant = self.inspect(self.source, "--manifest", str(self.manifest))
        self.assertEqual(variant["collection_manifest"], self.provenance)
        for before, after in zip(baseline["frames"], variant["frames"], strict=True):
            previous, current = before["record"], after["record"]
            self.assertEqual([run["plugin_id"] for run in current["plugin_runs"]], ["probe", "frame"])
            self.assertEqual(previous["measurements"]["probe"], {"offset": 2})
            self.assertEqual(current["measurements"]["probe"], {"offset": 7})
            for key in ("signals", "things"):
                self.assertEqual(current[key], previous[key])
            self.assertEqual(current["measurements"]["frame"], previous["measurements"]["frame"])
            self.assertEqual(after["steps"]["memory"]["plugin_configs"], before["steps"]["memory"]["plugin_configs"])

    def test_headless_command_prints_manifest_provenance(self):
        self.recording()
        output = invoke_automa("workbench", str(self.source), "--manifest", str(self.manifest),
                               "--pace", "realtime", "--cadence-ms", "0").stdout
        for value in (str(self.manifest), "collection: variant", "replaced plugins: perception/probe", "progress: 2/2"):
            self.assertIn(value, output)

    def test_archive_replays_after_recorded_source_and_manifest_are_removed(self):
        self.recording()
        shutil.rmtree(self.source / UPLOADED_SOURCES_DIR)
        seed = self.root / "seed.json"
        seed_document = shared_memory_document({"seed": 1})
        seed.write_text(json.dumps(seed_document))
        report = self.inspect(self.source, "--manifest", str(self.manifest), "--record", "--frame", "1",
                              "--shared-memory", str(seed))
        archive = Path(report["run_dir"])
        saved = json.loads((archive / "report.json").read_text())
        self.assertEqual(saved["collection_manifest"], self.provenance)
        self.assertEqual(saved["shared_memory_seed"], {"path": str(seed), "document": seed_document})
        self.assertEqual([frame["position"] for frame in report["frames"]], [1])
        (replacement,) = (archive / UPLOADED_SOURCES_DIR).iterdir()
        self.assertIn("class Variant:", replacement.read_text())
        shutil.rmtree(self.source)
        shutil.rmtree(self.manifest.parent)
        replayed = self.inspect(archive)
        self.assertEqual([frame["record"]["measurements"]["probe"] for frame in replayed["frames"]],
                         [{"offset": 7}, {"offset": 7}])

    def test_restages_use_installed_specs_without_opening_the_controller_release(self):
        def change(steps):
            steps["perception"] = replace(steps["perception"], plugins=("frame", "probe"))
            memory = steps["memory"]
            steps["memory"] = replace(
                memory, plugin_specs={"bounded_evidence": "retired:Memory"},
                plugin_configs={"bounded_evidence": {"max_records": 5}},
                metadata={"host_name": "fixture", "controller_bundle": {"release": {"tree_sha256": "a" * 64}}},
            )
            return steps

        self.recording(change=change)
        with patch("cli.automa_cli.replay.selections.release_code_dir", side_effect=OSError("release missing")) as release:
            report = self.inspect(self.source, "--manifest", str(self.manifest))
            workbench = self.workbench(manifest=self.manifest)
        release.assert_not_called()
        self.assertEqual(workbench["active_perception_plugin_ids"], ["frame", "probe"])
        self.assertEqual(workbench["cycle"]["memory"]["plugins"][0]["state"]["bounds"]["max_records"], 5)
        self.assertEqual([frame["steps"]["memory"]["plugin_configs"]["bounded_evidence"]["max_records"]
                          for frame in report["frames"]], [3, 5])

    def test_later_unavailable_plugin_fails_before_frame_zero(self):
        def change(steps):
            steps["memory"] = replace(steps["memory"], plugins=("old_memory",),
                                      plugin_specs={"old_memory": "retired:Memory"})
            return steps

        self.recording(change=change)
        for error in self.failures():
            self.assertIn("memory/old_memory is missing from the installed catalog and --manifest", error)

    def test_later_recorded_config_that_cannot_construct_fails_before_frame_zero(self):
        def change(steps):
            steps["memory"] = replace(steps["memory"], plugin_configs={"bounded_evidence": {"bad_option": 1}})
            return steps

        self.recording(change=change)
        for error in self.failures():
            self.assertIn("memory/bounded_evidence", error)
            self.assertIn("bad_option", error)

    def test_manifest_cannot_add_a_plugin_or_run_without_a_recorded_selection(self):
        for error in self.failures(source=self.images):
            self.assertIn("--manifest requires a source with a recorded selection", error)
        self.recording()
        self.manifest.write_text(self.manifest.read_text().replace("perception/probe", "perception/other"))
        for error in self.failures():
            self.assertIn("perception/other, which the recording did not run", error)

    def test_manifest_registration_errors_match_upload(self):
        self.recording()
        original = self.manifest.read_text()
        for invalid in (original.replace("probe:Variant", "missing:Variant"), original.replace('{"offset": 7}', "[]")):
            self.manifest.write_text(invalid)
            upload = PluginCatalogAPI(LocalPluginCatalog())(read_manifest(self.manifest))
            self.assertFalse(upload["ok"])
            for error in self.failures():
                self.assertIn(upload["error"], error)

    def test_manifest_rejects_packaged_selection_flags(self):
        common = (str(self.source), "--manifest", str(self.manifest))
        for command, flag, value in (
            (("workbench",), "--plan-plugin", "unused"),
            (("workbench",), "--memory-preset", "recency_ledger"),
            (("plugins", "perception", "inspect"), "--plugin", "frame"),
            (("plugins", "memory", "inspect"), "--preset", "recency_ledger"),
        ):
            with self.subTest(flag=flag):
                result = invoke_automa(*command, *common, flag, value, check=False)
                self.assertEqual(result.returncode, 2)
                self.assertIn(f"--manifest cannot be used with {flag}", result.stdout)
