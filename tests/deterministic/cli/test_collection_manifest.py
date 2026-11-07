"""Manifest paths and failures before contacting a host."""
import json
from pathlib import Path
import re
import tempfile
import unittest

from cli.automa_cli.plugins.catalog.manifest import read_manifest
from cli.automa_cli.plugins.catalog.render import render_upload
from cli.automa_cli.plugins.catalog.service import upload_collection


def write_manifest(root: Path, **manifest) -> Path:
    path = root / "manifest.json"
    path.write_text(json.dumps({"name": "experiment", "plugins": [
        {"name": "memory/experiment", "entrypoint": "plugin:Plugin", "config": {"offset": 1}},
    ], **manifest}))
    return path


class CollectionManifestTests(unittest.TestCase):
    def test_sources_are_the_modules_python_can_import_by_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plugin.py").write_text("class Plugin: pass\n")
            for script in ("scripts/run-sim.py", ".venv/lib/site.py", "notes/read me.py"):
                (root / script).parent.mkdir(parents=True, exist_ok=True)
                (root / script).write_text("not python (\n")
            payload = read_manifest(write_manifest(root))
            self.assertEqual(payload, {
                "operation": "upload_collection", "name": "experiment",
                "plugins": [{"step": "memory", "plugin_id": "experiment", "entrypoint": "plugin:Plugin",
                             "config": {"offset": 1}}],
                "sources": {"plugin.py": "class Plugin: pass\n"},
            })

    def test_a_manifest_problem_names_its_field(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for plugin, error in (
                ({"name": "memory/experiment"}, "manifest plugins[0]: missing field: entrypoint"),
                ({"name": "remember", "entrypoint": "plugin:Plugin"},
                 "manifest plugins[0]: expected <step>/<plugin>"),
            ):
                with self.subTest(error=error), self.assertRaisesRegex(ValueError, re.escape(error)):
                    read_manifest(write_manifest(root, plugins=[plugin]))
            with self.assertRaisesRegex(ValueError, "manifest: missing field: name"):
                path = root / "manifest.json"
                path.write_text(json.dumps({"plugins": [{"name": "memory/x", "entrypoint": "plugin:Plugin"}]}))
                read_manifest(path)

    def test_manifest_failures_are_upload_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            for content in ("[1]", "{", '{"name":"experiment","plugins":[]}', "{}"):
                with self.subTest(content=content):
                    path.write_text(content)
                    result = upload_collection(url="http://localhost:1", host_name=None, manifest=path)
                    self.assertEqual(result.exit_code, 2)
                    receipt = json.loads(render_upload(result, json_output=True))
                    self.assertFalse(receipt["ok"])
                    self.assertEqual(receipt["outcome"]["recovery"], f"Fix {path}, then upload it again.")

    def test_each_failure_offers_its_own_next_step(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plugin.py").write_text("class Plugin: pass\n")
            manifest = write_manifest(root)
            unreachable = render_upload(upload_collection(url="http://127.0.0.1:1", host_name=None,
                                                          manifest=manifest), json_output=False)
            self.assertIn("Next: Check that the catalog host at http://127.0.0.1:1 is running", unreachable)
            refused = upload_collection(url="http://127.0.0.1:1", host_name=None, manifest=manifest,
                                        entrypoint="plugin:Plugin")
            self.assertEqual(refused.exit_code, 2)
            receipt = json.loads(render_upload(refused, json_output=True))
            self.assertIn("use --name, --entrypoint and --config with --file", receipt["error"])
            self.assertIsNone(receipt["outcome"]["recovery"])
            missing = render_upload(upload_collection(url="http://127.0.0.1:1", host_name=None,
                                                      manifest=root / "missing.json"), json_output=False)
            self.assertIn(f"Next: Check the local plugin file: {root / 'missing.json'}", missing)
