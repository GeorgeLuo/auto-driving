"""Saving makes what a host has armed the plugins it starts with, through hosts update."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import STEPS
from cli.automa_cli.hosts.bundles import sync_controller_bundle
from cli.automa_cli.hosts.step_activations import (
    bundle_activation_path,
    ensure_step_activations,
    host_bundle,
    staging_host,
    startup_activations,
)
from implementations.decision_cycle.catalog import DEFAULT_STEP_PLUGINS, DEFAULT_STEP_PRESETS, selection_activation
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.workbench_fixtures import image_source
from tests.shared.integration.plugin_flow_fixtures import arm_step, name_host, plugin_host, upload, write_plugin
from tests.support.fake_hosts import closed_url
from tests.support.runtime_fixtures import write_host_fixture

DEFAULTS = {step: selection_activation(step) for step in STEPS}


def update(runtime_root: Path) -> dict[str, Path]:
    """Stage every step with a packaged release, as hosts update does."""

    configuration, error = staging_host("one", runtime_root=runtime_root)
    assert error is None, error
    bundle = host_bundle("one", runtime_root)
    return ensure_step_activations(
        host_name="one", bundle=bundle, release=sync_controller_bundle(bundle, output=None),
        activations=startup_activations(bundle, "one"), vehicle=configuration,
    )


def staged(paths: dict[str, Path]) -> dict[str, dict]:
    return {step: json.loads(path.read_text(encoding="utf-8")) for step, path in paths.items()}


class PluginSaveFlows(unittest.TestCase):
    def test_a_saved_selection_is_staged_and_kept_by_update_until_the_default_is_saved(self):
        for vehicle, provider in (("chase", "chase-sim"), ("picar", "picar")):
            with self.subTest(vehicle=vehicle), image_source(1) as root, plugin_host(root, vehicle, DEFAULTS) as (_, base):
                runtime_root = root / "hosts"
                name_host(runtime_root, base, provider)
                paths = update(runtime_root)
                before = staged(paths)

                def saved(*options):
                    return invoke_automa("plugins", "save", "--host-name", "one", *options, runtime_root=runtime_root)

                arm_step(base, "perception", "motion_tracks")
                arm_step(base, "action", "hold")
                payload = json.loads(saved("--json").stdout)
                self.assertTrue(payload["ok"])
                self.assertEqual(set(payload["steps"]), set(STEPS))
                self.assertEqual(payload["steps"]["perception"],
                                 {"activation": payload["steps"]["perception"]["activation"], "preset": "visual_observer",
                                  "plugins": ["frame", "floor_plane", "motion_tracks"], "saved": True})
                self.assertEqual(payload["steps"]["action"]["plugins"], ["hold"])
                self.assertTrue(payload["steps"]["action"]["saved"])
                self.assertEqual({step for step, entry in payload["steps"].items() if entry["saved"]},
                                 {"perception", "action"})

                # The staged specs, configs and release stay; only the selection is saved.
                after = staged(paths)
                for step in STEPS:
                    self.assertEqual(after[step]["plugins"], payload["steps"][step]["plugins"])
                    self.assertEqual(after[step]["plugin_specs"], before[step]["plugin_specs"])
                    self.assertEqual(after[step]["plugin_configs"], before[step]["plugin_configs"])
                    self.assertEqual(after[step]["metadata"]["controller_bundle"],
                                     before[step]["metadata"]["controller_bundle"])
                    self.assertEqual(after[step]["metadata"].get("saved", False), payload["steps"][step]["saved"])

                human = saved().stdout.splitlines()
                self.assertEqual(human[0], "Host: one")
                self.assertIn("Perception: visual_observer (frame, floor_plane, motion_tracks) (saved)", human)
                self.assertIn("Memory: recency_ledger (bounded_evidence) (default)", human)
                self.assertIn("Action: hold (saved)", human)
                self.assertEqual(human[-1], "Startup: " + (
                    "one starts with these plugins; hosts update keeps the saved steps." if provider == "chase-sim"
                    else "one starts with these plugins once they ship: ./cli/automa hosts update --name one"))
                if provider == "picar":
                    preview = json.loads(invoke_automa("hosts", "update", "--name", "one", "--dry-run", "--json",
                                                       runtime_root=runtime_root).stdout)
                    self.assertEqual(preview["activation"]["steps"]["perception"],
                                     ["frame", "floor_plane", "motion_tracks"])
                    self.assertEqual(preview["activation"]["presets"]["perception"], "visual_observer")

                # An update keeps each saved step and stages every other step's default.
                kept = staged(update(runtime_root))
                self.assertEqual(kept["perception"]["plugins"], ["frame", "floor_plane", "motion_tracks"])
                self.assertEqual(kept["action"]["plugins"], ["hold"])
                self.assertTrue(kept["action"]["metadata"]["saved"])
                for step in ("observation", "memory", "proposal", "plan"):
                    self.assertEqual(kept[step]["plugins"], list(DEFAULT_STEP_PLUGINS[step]))

                # Saving a step's default makes it follow the catalog's default again.
                invoke_automa("plugins", "disarm", "--url", base, "--name", "perception/motion_tracks")
                reset = json.loads(saved("--step", "perception", "--json").stdout)
                self.assertEqual(list(reset["steps"]), ["perception"])
                self.assertEqual(reset["steps"]["perception"]["preset"], DEFAULT_STEP_PRESETS["perception"])
                self.assertFalse(reset["steps"]["perception"]["saved"])
                followed = staged(update(runtime_root))
                self.assertEqual(followed["perception"]["plugins"], list(DEFAULT_STEP_PLUGINS["perception"]))
                self.assertNotIn("saved", followed["perception"]["metadata"])
                self.assertEqual(followed["action"]["plugins"], ["hold"])

    def test_nothing_is_saved_unless_every_step_can_be(self):
        with image_source(1) as root, plugin_host(root, "chase", DEFAULTS) as (_, base):
            runtime_root = root / "hosts"
            name_host(runtime_root, base, "chase-sim")
            paths = update(runtime_root)

            def refused(*options):
                result = invoke_automa("plugins", "save", "--host-name", "one", *options,
                                       runtime_root=runtime_root, check=False)
                self.assertEqual(result.returncode, 2)
                return result.stdout

            def unchanged(check):
                before = {step: path.read_bytes() for step, path in paths.items()}
                check()
                self.assertEqual({step: path.read_bytes() for step, path in paths.items()}, before)

            arm_step(base, "action", "hold")
            file = root / "proposal.py"
            write_plugin(file, "proposal", "prototype", 1)
            upload(base, file, "proposal", "prototype", arm=True)
            unchanged(lambda: self.assertIn(
                "proposal/prototype is uploaded, and uploads last until the host restarts", refused()))
            # The steps without the upload can still be saved.
            self.assertEqual(json.loads(invoke_automa("plugins", "save", "--host-name", "one", "--step", "action",
                                                      "--json", runtime_root=runtime_root).stdout)
                             ["steps"]["action"]["plugins"], ["hold"])

            file.write_text("raise RuntimeError('cannot load this revision')\n")
            upload(base, file, "proposal", "prototype", arm=True, check=False)
            unchanged(lambda: self.assertIn("The host's last arming failed", refused("--step", "action")))
            arm_step(base, "proposal", "avoid_recent_obstruction")
            invoke_automa("plugins", "disarm", "--url", base, "--name", "proposal/prototype")

            # A host running other specs or configs than its staged release is restarted first.
            perception = json.loads(paths["perception"].read_text(encoding="utf-8"))
            perception["plugin_configs"]["frame"] = {**perception["plugin_configs"].get("frame", {}), "changed": True}
            paths["perception"].write_text(json.dumps(perception), encoding="utf-8")
            reason = refused("--json")
            self.assertIn("runs a perception/frame its staged release does not package", reason)
            self.assertIn("./cli/automa hosts update --name one --restart", reason)
            self.assertFalse(json.loads(reason)["ok"])
            self.assertEqual(json.loads(paths["action"].read_text(encoding="utf-8"))["plugins"], ["hold"])

            # An unreadable staged step stops only a save that includes it.
            paths["memory"].write_text("{", encoding="utf-8")
            reason = refused()
            self.assertIn("Invalid memory activation", reason)
            self.assertIn("./cli/automa hosts update --name one", reason)
            self.assertTrue(json.loads(invoke_automa("plugins", "save", "--host-name", "one", "--step", "plan",
                                                     "--json", runtime_root=runtime_root).stdout)["ok"])

    def test_a_host_that_cannot_answer_saves_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_host_fixture(root, "unreachable", provider="picar", url=closed_url())
            before = sorted(path.relative_to(root) for path in root.rglob("*"))
            for name, reason in (("missing", "No host"), ("unreachable", "Connection refused")):
                with self.subTest(host=name):
                    result = invoke_automa("plugins", "save", "--host-name", name, runtime_root=root, check=False)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout.splitlines()[:2], [f"Host: {name}", "Save: failed"])
                    self.assertIn(reason, result.stdout)
            self.assertEqual(sorted(path.relative_to(root) for path in root.rglob("*")), before)


if __name__ == "__main__":
    unittest.main()
