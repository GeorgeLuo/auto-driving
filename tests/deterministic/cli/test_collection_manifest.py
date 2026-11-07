"""Manifest paths and failures before contacting a host."""
import json
from pathlib import Path
import tempfile
import unittest

from cli.automa_cli.plugins.catalog.manifest import read_manifest
from cli.automa_cli.plugins.catalog.service import upload_collection
from cli.automa_cli.plugins.catalog.render import render_upload


class CollectionManifestTests(unittest.TestCase):
    def test_relative_sources_and_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'plugin.py').write_text('class Plugin: pass\n')
            path = root / 'manifest.json'
            path.write_text(json.dumps(dict(name='experiment', plugins=[
                dict(name='memory/experiment', file='plugin.py', entrypoint='plugin:Plugin')
            ])))
            payload = read_manifest(path)
            self.assertEqual(payload['name'], 'experiment')
            self.assertEqual(payload['sources'], {'plugin.py': 'class Plugin: pass\n'})
            self.assertEqual(payload['operation'], 'upload_collection')
            manifest = json.loads(path.read_text())
            manifest['plugins'][0]['entrypoint'] = 'other:Plugin'
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'does not match entrypoint'):
                read_manifest(path)

    def test_manifest_failures_are_upload_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            for content in ('[1]', '{', '{"name":"experiment","plugins":[]}', '{}'):
                with self.subTest(content=content):
                    path.write_text(content)
                    result = upload_collection(url='http://localhost:1', host_name=None, manifest=path)
                    self.assertEqual(result.exit_code, 2)
                    self.assertFalse(json.loads(render_upload(result, json_output=True))['ok'])
