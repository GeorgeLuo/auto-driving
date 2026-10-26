"""CLI -> catalog -> frame adoption, on an in-process vehicle host."""

from __future__ import annotations

import json
import unittest

from autonomy.runtime.session import RunConfiguration
from implementations.decision_cycle.catalog import packaged_activation
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.workbench_fixtures import ImageReplayRunner, image_source, serve_workbench
from tests.shared.integration.plugin_flow_fixtures import (
    arm_step,
    catalog,
    frame,
    plugin_host,
    proposal,
    selected,
    upload,
    write_plugin,
)


class PluginArmingFlows(unittest.TestCase):
    def test_available_requested_and_applied_revisions_stay_independent(self):
        with image_source(1) as root, plugin_host(root) as (host, base):
            file = root / "proposal.py"
            write_plugin(file, "proposal", "prototype", 1)
            _, first = upload(base, file, "proposal", "prototype", arm=True)
            self.assertEqual(first["upload"]["status"], "uploaded")
            self.assertEqual(first["status"], "requested")
            self.assertEqual(proposal(host.run(frame(root, 1)), "prototype").metadata["revision"], 1)
            old_identity = catalog(base)["applied_decision"]
            write_plugin(file, "proposal", "prototype", 2)
            _, second = upload(base, file, "proposal", "prototype")
            available = catalog(base)
            self.assertEqual(selected(available, "applied", "proposal")[0], first["plugin"])
            self.assertEqual(available["applied_decision"], old_identity)
            self.assertEqual(proposal(host.run(frame(root, 2)), "prototype").metadata["revision"], 1)
            arm_step(base, "proposal", "prototype")
            write_plugin(file, "proposal", "prototype", 3)
            _, third = upload(base, file, "proposal", "prototype")
            queued = catalog(base)
            self.assertEqual(selected(queued, "requested", "proposal")[0], second["plugin"])
            self.assertEqual(selected(queued, "applied", "proposal")[0], first["plugin"])
            self.assertEqual(next(item for item in queued["plugins"] if item["id"] == "prototype"), third["plugin"])
            self.assertEqual(proposal(host.run(frame(root, 3)), "prototype").metadata["revision"], 2)
            identity2 = catalog(base)["applied_decision"]
            self.assertNotEqual(identity2["generation_id"], old_identity["generation_id"])
            arm_step(base, "proposal", "prototype")
            self.assertEqual(proposal(host.run(frame(root, 4)), "prototype").metadata["revision"], 3)
            self.assertNotEqual(catalog(base)["applied_decision"]["generation_id"], identity2["generation_id"])

    def test_rapid_requests_append_in_order_and_rearming_keeps_memory_history(self):
        with image_source(1) as root, plugin_host(root) as (host, base):
            for step, plugin_id in (("perception", "prototype-perception"), ("memory", "prototype-memory"),
                                    ("proposal", "first"), ("proposal", "second"), ("proposal", "third")):
                file = root / f"{plugin_id}.py"
                write_plugin(file, step, plugin_id, 1)
                upload(base, file, step, plugin_id)
                arm_step(base, step, plugin_id)
            queued = catalog(base)
            self.assertEqual([item["id"] for item in selected(queued, "requested", "proposal")],
                             ["first", "second", "third"])
            first = host.run(frame(root, 1))
            self.assertEqual(proposal(first, "first").metadata["memory"]["count"], 1)
            identity = catalog(base)["applied_decision"]
            for step, plugin_id in (("memory", "prototype-memory"), ("proposal", "first")):
                receipt = arm_step(base, step, plugin_id)
                self.assertEqual(receipt["status"], "applied")
            second = host.run(frame(root, 2))
            self.assertEqual(proposal(second, "first").metadata["memory"]["count"], 2)
            self.assertEqual(catalog(base)["applied_decision"], identity)

    def test_arming_before_during_and_after_completion_does_not_start_or_change_mode(self):
        with image_source(1) as root, plugin_host(root) as (host, base):
            file = root / "proposal.py"
            for index, phase in enumerate(("pre_start", "running", "completed"), start=1):
                if phase == "running":
                    host.start(RunConfiguration(mode="observe_only", num_decisions=1))
                write_plugin(file, "proposal", "prototype", index)
                before = host.session_status()
                _, receipt = upload(base, file, "proposal", "prototype", arm=True)
                self.assertEqual(receipt["status"], "requested")
                self.assertEqual(host.session_status(), before)
                self.assertEqual(host.session_status()["status"], phase)
                if phase == "completed":
                    host.start(RunConfiguration(mode="observe_only", num_decisions=1))
                host.run(frame(root, index))
                self.assertEqual(catalog(base)["arming"]["status"], "applied")
                if phase != "pre_start":
                    self.assertEqual(host.session_status()["status"], "completed")

    def test_an_import_failure_fails_the_arm_and_keeps_the_previous_selection(self):
        with (image_source(1) as root, plugin_host(root, activations={
                "perception": packaged_activation("perception", ["frame"]),
        }) as (host, base)):
            file = root / "proposal.py"
            write_plugin(file, "proposal", "prototype-proposal", 1)
            file.write_text("import not_uploaded_yet\n" + file.read_text())
            upload(base, file, "proposal", "prototype-proposal")
            before = catalog(base)
            for command in (
                ("arm", "--step", "proposal", "--plugin", "prototype-proposal"),
                ("upload", "--file", str(file), "--step", "proposal", "--plugin-id", "prototype-proposal",
                 "--entrypoint", "prototype:Prototype", "--arm"),
            ):
                with self.subTest(command=command[0]):
                    refused = invoke_automa("hosts", "plugins", *command, "--url", base, check=False)
                    self.assertEqual(refused.returncode, 2, refused.stdout)
                    self.assertIn("Arming: failed", refused.stdout)
                    self.assertIn("ModuleNotFoundError: No module named 'not_uploaded_yet'", refused.stdout)
            failed = invoke_automa("hosts", "plugins", "status", "--url", base, "--json", check=False)
            self.assertEqual(failed.returncode, 2)
            shown = json.loads(failed.stdout)
            self.assertEqual(shown["arming"]["status"], "failed")
            self.assertEqual(shown["arming"]["requested"], before["arming"]["requested"])
            self.assertEqual(shown["arming"]["applied"], before["arming"]["applied"])
            self.assertEqual(shown["applied_decision"], before["applied_decision"])
            host.run(frame(root, 1))
            write_plugin(file, "proposal", "prototype-proposal", 2)
            upload(base, file, "proposal", "prototype-proposal", arm=True)
            self.assertEqual(proposal(host.run(frame(root, 2)), "prototype-proposal").metadata["revision"], 2)
            self.assertEqual(catalog(base)["arming"]["status"], "applied")

    def test_upload_remains_available_when_composed_arming_fails_on_a_workbench(self):
        root = self.enterContext(image_source(1))
        base = serve_workbench(self, ImageReplayRunner(root))
        file = root / "prototype.py"
        write_plugin(file, "perception", "prototype", 1)
        result, receipt = upload(base, file, "perception", "prototype", arm=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt["upload"]["status"], "uploaded")
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("runtime host", receipt["error"])
        self.assertIn(receipt["plugin"], catalog(base)["plugins"])


if __name__ == "__main__":
    unittest.main()
