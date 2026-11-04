"""Plugin listing names a catalog, or what a host has armed, through catalog changes."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import STEPS
from implementations.decision_cycle.catalog import DEFAULT_STEP_PLUGINS, step_plugins
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.workbench_fixtures import image_source
from tests.shared.integration.plugin_flow_fixtures import (
    arm_step, catalog, frame, name_host, plugin_host, upload, write_plugin,
)
from tests.support.fake_hosts import answering, closed_url
from tests.support.runtime_fixtures import write_host_fixture


def row(plugin, mark=""):
    metadata = plugin["metadata"]
    source = f"revision {metadata['revision']} ({metadata['filename']})" if "revision" in metadata else "packaged"
    return f"- {plugin['step']}/{plugin['id']}: {source}" + (f" ({mark})" if mark else "")


class PluginListFlows(unittest.TestCase):
    def test_without_a_host_lists_the_packaged_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def listed(*options):
                return invoke_automa("plugins", "list", *options, runtime_root=root)

            payload = json.loads(listed("--json").stdout)
            self.assertTrue(payload["ok"])
            self.assertIsNone(payload["host_name"])
            self.assertNotIn("armed", payload)
            self.assertEqual(payload["catalog"]["source"], "implementations/decision_cycle/*/catalog.json")
            self.assertEqual(payload["defaults"], {step: list(ids) for step, ids in DEFAULT_STEP_PLUGINS.items()})
            plugins = payload["catalog"]["plugins"]
            packaged = {(step, plugin_id): entry for step in STEPS for plugin_id, entry in step_plugins(step).items()}
            self.assertEqual({(item["step"], item["id"]) for item in plugins}, set(packaged))
            for item in plugins:
                entry = packaged[item["step"], item["id"]]
                self.assertEqual((item["entrypoint"], item["config"]), (entry["spec"], entry["default_config"]))

            # One row per plugin in cycle order, each step's defaults first and marked.
            rows = []
            for step in STEPS:
                defaults = DEFAULT_STEP_PLUGINS[step]
                ordered = [*defaults, *sorted(set(step_plugins(step)) - set(defaults))]
                self.assertEqual([item["id"] for item in plugins if item["step"] == step], ordered)
                rows += [f"- {step}/{plugin_id}: packaged" + (" (default)" if plugin_id in defaults else "")
                         for plugin_id in ordered]
            self.assertEqual(listed().stdout.splitlines(), ["Catalog: implementations/decision_cycle/*/catalog.json", *rows])
            # Without a host the listing is already the whole catalog.
            self.assertEqual(listed("--long").stdout, listed().stdout)
            action = json.loads(listed("--step", "action", "--json").stdout)
            self.assertEqual({item["step"] for item in action["catalog"]["plugins"]}, {"action"})
            self.assertEqual(list(action["defaults"]), ["action"])
            self.assertEqual(action["catalog"]["source"], "implementations/decision_cycle/action/catalog.json")
            self.assertEqual(list(root.iterdir()), [])

    def test_named_hosts_list_what_is_armed_and_long_marks_it_in_the_catalog(self):
        for vehicle, provider in (("chase", "chase-sim"), ("picar", "picar")):
            with self.subTest(vehicle=vehicle), image_source(1) as root, plugin_host(root, vehicle) as (host, base):
                runtime_root = root / "hosts"
                name_host(runtime_root, base, provider)

                def listed(*options, check=True):
                    return invoke_automa("plugins", "list", "--host-name", "one", *options,
                                         runtime_root=runtime_root, check=check)

                initial = json.loads(listed("--json").stdout)
                self.assertEqual(initial["host_name"], "one")
                self.assertEqual(initial["url"].rstrip("/"), base.rstrip("/"))
                self.assertNotIn("catalog", initial)
                self.assertIsNone(initial["arming_error"])
                self.assertIn("perception_summary", [item["id"] for item in initial["armed"]])
                self.assertIn("selected", [item["id"] for item in initial["armed"]])
                self.assertEqual(listed().stdout.splitlines(),
                                 ["Host: one", "Armed:", *(row(item) for item in initial["armed"])])
                by_url = invoke_automa("plugins", "list", "--url", base, runtime_root=runtime_root).stdout
                self.assertEqual(by_url.splitlines(), [f"Catalog: {base}", "Armed:", *(row(item) for item in initial["armed"])])
                self.assertEqual(json.loads(listed("--step", "memory", "--json").stdout)["armed"], [])
                self.assertIn("Armed:\n(none)\n", listed("--step", "memory").stdout)

                long = listed("--long").stdout.splitlines()
                response = catalog(base)
                self.assertEqual(long[:2], ["Host: one", f"Catalog: {initial['url']}"])
                self.assertTrue(long[2].startswith(f"Catalog version: {response['catalog_version']}"))
                for item in response["plugins"]:
                    self.assertIn(row(item, "armed" if item in initial["armed"] else ""), long)
                self.assertEqual(json.loads(listed("--long", "--json").stdout)["catalog"]["plugins"],
                                 [item for step in STEPS for item in response["plugins"] if item["step"] == step])

                file = root / "proposal.py"
                write_plugin(file, "proposal", "prototype", 1)
                upload(base, file, "proposal", "prototype", arm=True)
                host.run(frame(root, 1))
                write_plugin(file, "proposal", "prototype", 2)
                _, second = upload(base, file, "proposal", "prototype")
                arm_step(base, "proposal", "prototype")
                write_plugin(file, "proposal", "prototype", 3)
                _, third = upload(base, file, "proposal", "prototype")

                # Armed means requested: the second revision, before the host's next cycle adopts it.
                before = host.plugin_catalog()
                session = host.session_status()
                armed = json.loads(listed("--step", "proposal", "--json").stdout)["armed"]
                self.assertEqual(armed[-1], second["plugin"])
                self.assertIn(row(second["plugin"]), listed("--step", "proposal").stdout.splitlines())
                # The catalog holds the third; the armed second stays listed, followed by its newer revision.
                proposals = json.loads(listed("--step", "proposal", "--long", "--json").stdout)["catalog"]["plugins"]
                self.assertIn(third["plugin"], proposals)
                self.assertNotIn(second["plugin"], proposals)
                rows = listed("--step", "proposal", "--long").stdout.splitlines()
                self.assertEqual(rows.index(row(second["plugin"], "armed")) + 1, rows.index(row(third["plugin"])))
                self.assertEqual(host.plugin_catalog(), before)
                self.assertEqual(host.session_status(), session)

                file.write_text("raise RuntimeError('cannot load this revision')\n")
                upload(base, file, "proposal", "prototype", arm=True, check=False)
                failed = listed("--step", "proposal", "--json", check=False)
                self.assertEqual(failed.returncode, 2)
                self.assertEqual(json.loads(failed.stdout)["armed"], armed)
                self.assertIn("cannot load this revision", json.loads(failed.stdout)["arming_error"])
                human = listed("--step", "proposal", check=False).stdout.splitlines()
                self.assertIn(row(second["plugin"]), human)
                self.assertTrue(human[-1].startswith("Arming error: "))
                self.assertEqual(listed("--step", "proposal", "--long", check=False).returncode, 2)

    def test_unavailable_hosts_are_errors_and_are_not_started(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, provider in (("stopped", "chase-sim"), ("unreachable", "picar")):
                write_host_fixture(root, name, provider=provider, url=closed_url())
            before = sorted(path.relative_to(root) for path in root.rglob("*"))
            for name, reason in (("missing", "No host"), ("stopped", "not running"),
                                 ("unreachable", "Connection refused")):
                for options in ((), ("--json",), ("--long",)):
                    with self.subTest(host=name, options=options):
                        result = invoke_automa("plugins", "list", "--host-name", name, *options,
                                               runtime_root=root, check=False)
                        self.assertEqual(result.returncode, 2)
                        self.assertIn(reason, result.stdout)
                        if "--json" in options:
                            payload = json.loads(result.stdout)
                            self.assertFalse(payload["ok"])
                            self.assertEqual(payload["host_name"], name)
                            self.assertIsNone(payload["armed"])
                        else:
                            self.assertEqual(result.stdout.splitlines()[:2], [f"Host: {name}", "Catalog: unavailable"])
            self.assertEqual(sorted(path.relative_to(root) for path in root.rglob("*")), before)
            unreachable = invoke_automa("plugins", "list", "--url", closed_url(), runtime_root=root, check=False)
            self.assertEqual(unreachable.returncode, 2)
            self.assertIn("then retry ./cli/automa plugins list --url", unreachable.stdout)

    def test_a_catalog_that_does_not_arm_lists_only_with_long(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with answering(json.dumps({"ok": True, "catalog_version": 3, "plugins": []}).encode()) as base:
                short = invoke_automa("plugins", "list", "--url", base, runtime_root=root, check=False)
                long = invoke_automa("plugins", "list", "--url", base, "--long", runtime_root=root)
            self.assertEqual(short.returncode, 2)
            self.assertIn("This catalog does not arm plugins; add --long to list it.", short.stdout)
            self.assertEqual(long.stdout.splitlines(), [f"Catalog: {base}", "Catalog version: 3", "(none)"])

    def test_catalog_without_armed_plugins_does_not_report_empty_success(self):
        for arming in ({"applied": {}}, {"requested": {"perception": []}}):
            with self.subTest(arming=arming), tempfile.TemporaryDirectory() as directory:
                with answering(json.dumps({"ok": True, "plugins": [], "arming": arming}).encode()) as base:
                    root = Path(directory)
                    name_host(root, base, "picar")
                    result = invoke_automa("plugins", "list", "--host-name", "one", "--json",
                                           runtime_root=root, check=False)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(json.loads(result.stdout)["ok"])
                self.assertIn("did not report its armed plugins", result.stdout)
