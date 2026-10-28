"""Named-host listing follows applied revisions through catalog changes."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import STEPS
from implementations.decision_cycle.catalog import DEFAULT_STEP_PLUGINS, step_plugins
from implementations.runtime.chase_sim.service import HOST_RECORD_SCHEMA
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.workbench_fixtures import image_source
from tests.shared.integration.plugin_flow_fixtures import (
    arm_step, frame, plugin_host, upload, write_plugin,
)
from tests.support.fake_hosts import answering, closed_url
from tests.support.runtime_fixtures import write_host_fixture, write_json


def name_host(runtime_root: Path, base: str, provider: str) -> None:
    write_host_fixture(runtime_root, "one", provider=provider, url=base)
    if provider == "chase-sim":
        write_json(runtime_root / "one/bundle/runtime/automation/host.json", {
            "schema": HOST_RECORD_SCHEMA, "host_name": "one", "pid": os.getpid(), "base_url": base,
        })


class PluginListFlows(unittest.TestCase):
    def test_without_a_host_lists_every_packaged_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def listed(*options):
                return invoke_automa("plugins", "list", *options, runtime_root=root)

            catalog = json.loads(listed("--json").stdout)
            self.assertTrue(catalog["ok"])
            self.assertIsNone(catalog["host_name"])
            self.assertEqual(catalog["defaults"], {step: list(ids) for step, ids in DEFAULT_STEP_PLUGINS.items()})
            packaged = {(step, plugin_id): entry for step in STEPS for plugin_id, entry in step_plugins(step).items()}
            self.assertEqual({(item["step"], item["id"]) for item in catalog["plugins"]}, set(packaged))
            for item in catalog["plugins"]:
                entry = packaged[item["step"], item["id"]]
                self.assertEqual((item["entrypoint"], item["config"]), (entry["spec"], entry["default_config"]))

            human = listed().stdout
            self.assertIn("Packaged plugins:", human)
            for step in STEPS:
                defaults = DEFAULT_STEP_PLUGINS[step]
                ordered = [*defaults, *sorted(set(step_plugins(step)) - set(defaults))]
                self.assertEqual([item["id"] for item in catalog["plugins"] if item["step"] == step], ordered)
                marked = [f"{plugin_id} (default)" if plugin_id in defaults else plugin_id for plugin_id in ordered]
                self.assertIn(f"  {step}: {', '.join(marked)}\n", human)
            action = json.loads(listed("--step", "action", "--json").stdout)
            self.assertEqual({item["step"] for item in action["plugins"]}, {"action"})
            self.assertEqual(list(action["defaults"]), ["action"])
            self.assertEqual(list(root.iterdir()), [])

    def test_named_hosts_list_applied_revisions_without_changing_selection_or_session(self):
        for vehicle, provider in (("chase", "chase-sim"), ("picar", "picar")):
            with self.subTest(vehicle=vehicle), image_source(1) as root, plugin_host(root, vehicle) as (host, base):
                runtime_root = root / "hosts"
                name_host(runtime_root, base, provider)

                def listed(*options):
                    return invoke_automa("plugins", "list", "--host-name", "one", *options,
                                         runtime_root=runtime_root)

                human = listed().stdout
                for step in STEPS:
                    self.assertIn(f"  {step}:", human)
                initial = json.loads(listed("--json").stdout)
                self.assertEqual(initial["host_name"], "one")
                self.assertEqual(initial["url"].rstrip("/"), base.rstrip("/"))
                self.assertIn("perception_summary", [item["id"] for item in initial["plugins"]])
                self.assertIn("selected", [item["id"] for item in initial["plugins"]])
                self.assertEqual(json.loads(listed("--step", "memory", "--json").stdout)["plugins"], [])
                self.assertIn("memory: (none)", listed("--step", "memory").stdout)

                file = root / "proposal.py"
                write_plugin(file, "proposal", "prototype", 1)
                _, first = upload(base, file, "proposal", "prototype", arm=True)
                host.run(frame(root, 1))
                write_plugin(file, "proposal", "prototype", 2)
                _, second = upload(base, file, "proposal", "prototype")
                arm_step(base, "proposal", "prototype")
                write_plugin(file, "proposal", "prototype", 3)
                upload(base, file, "proposal", "prototype")

                before = host.plugin_catalog()
                session = host.session_status()
                pending = json.loads(listed("--step", "proposal", "--json").stdout)
                self.assertEqual(pending["plugins"], [first["plugin"]])
                self.assertEqual(pending["arming"]["status"], "requested")
                self.assertIn(f"prototype (revision {first['plugin']['metadata']['revision']})",
                              listed("--step", "proposal").stdout)
                self.assertEqual(host.plugin_catalog(), before)
                self.assertEqual(host.session_status(), session)

                host.run(frame(root, 2))
                adopted = json.loads(listed("--step", "proposal", "--json").stdout)
                self.assertEqual(adopted["plugins"], [second["plugin"]])
                self.assertEqual(adopted["arming"]["status"], "applied")

                file.write_text("raise RuntimeError('cannot load this revision')\n")
                upload(base, file, "proposal", "prototype", arm=True, check=False)
                failed = json.loads(listed("--step", "proposal", "--json").stdout)
                self.assertEqual(failed["plugins"], [second["plugin"]])
                self.assertEqual(failed["arming"]["status"], "failed")
                self.assertIn("cannot load this revision", failed["arming"]["error"])
                self.assertIn("Selection error:", listed("--step", "proposal").stdout)

    def test_unavailable_hosts_are_errors_and_are_not_started(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, provider in (("stopped", "chase-sim"), ("unreachable", "picar")):
                write_host_fixture(root, name, provider=provider, url=closed_url())
            before = sorted(path.relative_to(root) for path in root.rglob("*"))
            for name, reason in (("missing", "No host"), ("stopped", "not running"),
                                 ("unreachable", "Connection refused")):
                for options in ((), ("--json",)):
                    with self.subTest(host=name, options=options):
                        result = invoke_automa("plugins", "list", "--host-name", name, *options,
                                               runtime_root=root, check=False)
                        self.assertEqual(result.returncode, 2)
                        self.assertIn(reason, result.stdout)
                        if options:
                            payload = json.loads(result.stdout)
                            self.assertFalse(payload["ok"])
                            self.assertEqual(payload["host_name"], name)
                            self.assertEqual(payload["plugins"], [])
                        else:
                            self.assertIn("Loaded plugins: unavailable", result.stdout)
            self.assertEqual(sorted(path.relative_to(root) for path in root.rglob("*")), before)

    def test_catalog_without_applied_selection_does_not_report_empty_success(self):
        for snapshot in ({"ok": True, "plugins": []}, {"ok": True, "arming": {"applied": {}}}):
            with self.subTest(snapshot=snapshot), tempfile.TemporaryDirectory() as directory:
                with answering(json.dumps(snapshot).encode()) as base:
                    root = Path(directory)
                    name_host(root, base, "picar")
                    result = invoke_automa("plugins", "list", "--host-name", "one", "--json",
                                           runtime_root=root, check=False)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(json.loads(result.stdout)["ok"])
                self.assertIn("did not report its applied plugin selection", result.stdout)
