"""CLI deselection shares arming's host lifetime and cycle-boundary adoption."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from autonomy.decision_cycle.steps import CLEARABLE_STEPS, STEP_RUNNERS
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.hosts.publications.plugin_catalog import PluginCatalogClient
from implementations.decision_cycle.catalog import packaged_activation
from tests.shared.cli.workbench_fixtures import ImageReplayRunner, image_source, serve_workbench
from tests.shared.integration.plugin_flow_fixtures import (
    arm_step,
    catalog,
    frame,
    name_host,
    plugin_host,
    selected,
    upload,
    write_plugin,
)
from tests.shared.cli.invoke import invoke_automa


class PluginDisarmingFlows(unittest.TestCase):
    def disarm(self, base, *args, check=True):
        return invoke_automa("plugins", "disarm", "--url", base, *args, check=check)

    def test_named_removal_keeps_catalog_order_and_reconstructs_the_pipeline(self):
        for vehicle in ("chase", "picar"):
            with self.subTest(vehicle=vehicle), image_source(1) as root, plugin_host(root, vehicle) as (host, base):
                for name in ("first", "second", "third"):
                    file = root / f"{name}.py"
                    write_plugin(file, "proposal", name, 1)
                    upload(base, file, "proposal", name, arm=True)
                host.run(frame(root, 1))
                before = catalog(base)
                instances = dict((definition.plugin_id, instance) for definition, instance in host.step("proposal").applied)
                with patch.object(host.step("proposal"), "_reset_plugin", wraps=host.step("proposal")._reset_plugin) as reset:
                    receipt = json.loads(self.disarm(base, "--name", "proposal/second",
                                                    "--name", "proposal/second", "--json").stdout)
                    self.assertEqual(receipt["schema"], "automa_plugin_disarm")
                    self.assertEqual(receipt["outcome"]["status"], "requested")
                    self.assertEqual(selected(receipt, "applied", "proposal"), selected(before, "applied", "proposal"))
                    reset.assert_not_called()
                    host.run(frame(root, 2))
                    reset.assert_not_called()
                after = catalog(base)
                self.assertEqual(after["plugins"], before["plugins"])
                self.assertEqual(selected(after, "applied", "proposal"),
                                 [item for item in selected(before, "applied", "proposal") if item["id"] != "second"])
                kept = dict((definition.plugin_id, instance) for definition, instance in host.step("proposal").applied)
                for name in ("first", "third"):
                    self.assertIsNot(kept[name], instances[name])
                self.assertNotEqual(after["applied_decision"]["generation_id"], before["applied_decision"]["generation_id"])
                arm_step(base, "proposal", "second")
                host.run(frame(root, 3))
                rearmed = dict((definition.plugin_id, instance) for definition, instance in host.step("proposal").applied)
                self.assertIsNot(rearmed["second"], instances["second"])

    def test_proposal_deselection_reconstructs_memory_and_discards_its_history(self):
        with image_source(1) as root, plugin_host(root) as (host, base):
            for step, name in (("perception", "prototype-perception"), ("memory", "prototype-memory"),
                               ("proposal", "first"), ("proposal", "second")):
                file = root / f"{name}.py"
                write_plugin(file, step, name, 1)
                upload(base, file, step, name, arm=True)
            host.run(frame(root, 1))
            self.assertEqual(host.shared_memory["prototype.memory"]["count"], 1)
            instance = host.step("memory").applied[0][1]
            self.disarm(base, "--name", "proposal/first")
            result = host.run(frame(root, 2))
            self.assertIsNot(host.step("memory").applied[0][1], instance)
            self.assertEqual(host.shared_memory["prototype.memory"]["count"], 1)
            self.assertEqual([candidate.plugin_id for candidate in result.proposal.candidates], ["second"])

    def test_clear_step_and_all_steps_without_changing_session_or_catalog(self):
        for vehicle in ("chase", "picar"):
            with self.subTest(vehicle=vehicle), image_source(1) as root, plugin_host(root, vehicle) as (host, base):
                for step in CLEARABLE_STEPS:
                    file = root / f"{step}.py"
                    write_plugin(file, step, f"prototype-{step}", 1)
                    upload(base, file, step, f"prototype-{step}", arm=True)
                host.run(frame(root, 1))
                self.assertIn("prototype.memory", host.shared_memory)
                before = catalog(base)
                session = host.session_status()
                human = self.disarm(base, "--step", "memory").stdout
                self.assertIn("Disarming: requested", human)
                self.assertIn("Requested memory: (none)", human)
                self.assertIn("Next:", human)
                self.assertEqual(host.session_status(), session)
                host.run(frame(root, 2))
                self.assertNotIn("prototype.memory", host.shared_memory)
                self.assertEqual(selected(catalog(base), "applied", "memory"), [])
                self.assertEqual(selected(catalog(base), "applied", "proposal"), selected(before, "applied", "proposal"))
                session = host.session_status()
                self.disarm(base, "--all")
                self.assertEqual(host.session_status(), session)
                host.run(frame(root, 3))
                after = catalog(base)
                for step in CLEARABLE_STEPS:
                    self.assertEqual(selected(after, "requested", step), [])
                    self.assertEqual(selected(after, "applied", step), [])
                self.assertEqual(after["plugins"], before["plugins"])
                for step in ("plan", "action"):
                    self.assertEqual(after["applied_decision"]["steps"][step], before["applied_decision"]["steps"][step])
                self.assertIn("Disarming: applied", self.disarm(base, "--all").stdout)

    def test_plugins_disarm_names_and_clears_on_a_named_host(self):
        for vehicle, provider in (("chase", "chase-sim"), ("picar", "picar")):
            with self.subTest(vehicle=vehicle), image_source(1) as root, plugin_host(root, vehicle) as (host, base):
                for step in CLEARABLE_STEPS:
                    file = root / f"{step}.py"
                    write_plugin(file, step, f"prototype-{step}", 1)
                    upload(base, file, step, f"prototype-{step}", arm=True)
                host.run(frame(root, 1))
                before = catalog(base)
                runtime_root = root / "hosts"
                name_host(runtime_root, base, provider)

                def disarm(*args):
                    return json.loads(invoke_automa("plugins", "disarm", "--host-name", "one", *args, "--json",
                                                    runtime_root=runtime_root).stdout)

                # One request disarms the named group across steps.
                receipt = disarm("--name", "memory/prototype-memory", "--name", "proposal/prototype-proposal")
                self.assertEqual((receipt["schema"], receipt["host_name"], receipt["outcome"]["status"]),
                                 ("automa_plugin_disarm", "one", "requested"))
                host.run(frame(root, 2))
                named = catalog(base)
                self.assertEqual([item["id"] for item in selected(named, "applied", "perception")],
                                 ["prototype-perception"])
                for step in ("memory", "proposal"):
                    self.assertEqual(selected(named, "applied", step), [])
                self.assertEqual(disarm("--all")["outcome"]["status"], "requested")
                host.run(frame(root, 3))
                after = catalog(base)
                for step in CLEARABLE_STEPS:
                    self.assertEqual(selected(after, "applied", step), [])
                for step in ("plan", "action"):
                    self.assertEqual(after["applied_decision"]["steps"][step], before["applied_decision"]["steps"][step])
                self.assertIn("Disarming: applied", invoke_automa("plugins", "disarm", "--url", base, "--all").stdout)

    def test_batch_empty_and_inactive_ids_are_noops_and_rapid_requests_compose(self):
        with image_source(1) as root, plugin_host(root) as (host, base):
            for name in ("first", "second", "third"):
                file = root / f"{name}.py"
                write_plugin(file, "proposal", name, 1)
                upload(base, file, "proposal", name)
            arm_step(base, "proposal", "first", "second")
            self.disarm(base, "--name", "proposal/first")
            arm_step(base, "proposal", "third")
            self.disarm(base, "--name", "proposal/third")
            self.assertEqual([item["id"] for item in selected(catalog(base), "requested", "proposal")], ["second"])
            host.run(frame(root, 1))
            before = catalog(base)
            receipt = json.loads(self.disarm(base, "--name", "proposal/first", "--json").stdout)
            self.assertEqual(receipt["status"], "applied")
            # The CLI cannot send an empty ID list; the catalog API treats one as no removal.
            self.assertTrue(PluginCatalogClient(base)({"operation": "disarm", "selections": {"proposal": [], "memory": []}})["ok"])
            host.run(frame(root, 2))
            after = catalog(base)
            self.assertEqual(after["applied_decision"], before["applied_decision"])
            self.assertEqual(after["arming"]["applied"], before["arming"]["applied"])

    def test_invalid_requests_leave_the_whole_selection_unchanged(self):
        with image_source(1) as root, plugin_host(root, activations={
                "perception": packaged_activation("perception", ["frame"]),
        }) as (host, base):
            before = catalog(base)
            # One unknown name fails the whole group.
            result = self.disarm(base, "--name", "perception/frame", "--name", "proposal/missing", "--json", check=False)
            self.assertEqual(result.returncode, 2)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["outcome"]["status"], "failed")
            self.assertIn("recovery", receipt["outcome"])
            self.assertEqual(catalog(base), before)
            for selections in ({"steering": []}, {"perception": "frame"}, {"memory": [7]}, {}, []):
                with self.subTest(selections=selections):
                    self.assertFalse(PluginCatalogClient(base)({"operation": "disarm", "selections": selections})["ok"])
                    self.assertEqual(catalog(base), before)
            for args in ((), ("--all", "--name", "perception/frame"), ("--step", "memory", "--all"),
                         ("--step", "memory", "--name", "memory/x"), ("--name", "frame"), ("--step", "plan")):
                with self.subTest(args=args):
                    self.assertEqual(self.disarm(base, *args, check=False).returncode, 2)
                    self.assertEqual(catalog(base), before)
            for value in ("yes", 1, None):
                result = PluginCatalogClient(base)({"operation": "disarm", "selections": {"memory": []}, "all": value})
                self.assertFalse(result["ok"])
                self.assertEqual(catalog(base), before)

    def test_a_one_plugin_step_keeps_its_plugin(self):
        with image_source(1) as root, plugin_host(root) as (host, base):
            before = selected(catalog(base), "applied", "plan")
            result = self.disarm(base, "--name", "plan/highest_confidence", "--json", check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("the plan step runs exactly one plugin; 0 selected", json.loads(result.stdout)["outcome"]["message"])
            host.run(frame(root, 1))
            self.assertEqual(host.plugin_catalog()["arming"]["applied"]["plan"], before)

    def test_clear_absent_runners_does_not_create_them(self):
        with image_source(1) as root, plugin_host(root) as (host, base):
            absent = [step for step in CLEARABLE_STEPS if host.step(step) is None]
            self.assertTrue(absent)
            receipt = json.loads(self.disarm(base, "--all", "--json").stdout)
            self.assertEqual(receipt["status"], "applied")
            for step in absent:
                self.assertIsNone(host.step(step))

    def test_request_does_not_start_or_change_running_or_completed_sessions(self):
        with image_source(1) as root, plugin_host(root, activations={
                "perception": packaged_activation("perception", ["frame"]),
        }) as (host, base):
            for index, phase in enumerate(("pre_start", "running", "completed"), start=1):
                arm_step(base, "perception", "frame")
                host.run(frame(root, index * 2))
                if phase == "running":
                    host.start(RunConfiguration(mode="observe_only", num_decisions=1))
                before = host.session_status()
                self.assertEqual(before["status"], phase)
                self.disarm(base, "--all")
                self.assertEqual(host.session_status(), before)
                host.run(frame(root, index * 2 + 1))

    def test_disarm_does_not_reset_removed_plugins(self):
        with image_source(1) as root, plugin_host(root, activations={
                "perception": packaged_activation("perception", ["frame"]),
        }) as (host, base):
            host.start(RunConfiguration(mode="observe_only", num_decisions=10))
            host.run(frame(root, 1))
            runner = host.step("perception")
            with patch.object(runner, "_reset_plugin", side_effect=AssertionError("reset called")) as reset:
                self.disarm(base, "--step", "perception")
                host.run(frame(root, 2))
                reset.assert_not_called()
            self.assertIsNot(host.step("perception"), runner)
            self.assertEqual(host.step("perception").plugin_ids, ())
            self.assertEqual(host.session_status()["status"], "running")

    def test_failed_group_build_keeps_all_steps_and_the_session_running(self):
        with image_source(1) as root, plugin_host(root, activations={
                "perception": packaged_activation("perception", ["frame"]),
                "memory": packaged_activation("memory", ["bounded_evidence"]),
        }) as (host, base):
            host.start(RunConfiguration(mode="observe_only", num_decisions=10))
            host.run(frame(root, 1))
            steps, memory, identity = host.steps, host.shared_memory, host.applied_decision()
            with patch.object(STEP_RUNNERS["memory"], "validate_selection", side_effect=RuntimeError("build refused")):
                result = self.disarm(base, "--all", "--json", check=False)
            self.assertEqual(result.returncode, 2)
            receipt = json.loads(result.stdout)
            self.assertIn("build refused", receipt["error"])
            self.assertEqual(receipt["arming"]["requested"], receipt["arming"]["applied"])
            self.assertIs(host.steps, steps)
            self.assertIs(host.shared_memory, memory)
            host.run(frame(root, 2))
            self.assertIs(host.steps, steps)
            self.assertEqual(host.applied_decision(), identity)
            self.assertEqual(host.session_status()["status"], "running")
            self.assertEqual(host.session_status()["processed_decisions"], 2)
            self.assertEqual(catalog(base)["arming"]["status"], "failed")
            self.disarm(base, "--all")
            host.run(frame(root, 3))
            self.assertEqual(catalog(base)["arming"]["status"], "applied")

    def test_workbench_rejects_disarm_without_changing_the_catalog(self):
        root = self.enterContext(image_source(1))
        base = serve_workbench(self, ImageReplayRunner(root))
        before = catalog(base)
        result = self.disarm(base, "--all", "--json", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("runtime host", json.loads(result.stdout)["error"])
        self.assertEqual(catalog(base), before)


if __name__ == "__main__":
    unittest.main()
