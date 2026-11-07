"""External collections through the CLI and self-contained recorded sources."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from autonomy.plugins import LocalPluginCatalog, uploaded_source
from autonomy.runtime.plugin_catalog import PluginCatalogAPI
from autonomy.runtime.recording import uploaded_sources
from autonomy.decision_cycle.activation import activation_for_definitions
from tests.shared.integration.collection_fixtures import collection_request as request
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.workbench_fixtures import image_source
from tests.shared.integration.plugin_flow_fixtures import plugin_host, frame, write_plugin, catalog


class CollectionJourneys(unittest.TestCase):
    def test_recorded_source_carries_siblings_into_a_fresh_process(self):
        catalog = LocalPluginCatalog()
        self.assertTrue(PluginCatalogAPI(catalog)(request())['ok'])
        definitions = list(catalog.read()[1])
        steps = {item.step: activation_for_definitions(item.step, [item]).to_payload() for item in definitions}
        sources = uploaded_sources(steps)
        self.assertEqual(len(sources), 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in sources:
                (root / name).write_bytes(uploaded_source(name).read_bytes())
            spec = definitions[0].executable_entrypoint
            script = ('from autonomy.plugins import *; '
                      f'add_uploaded_source_root({str(root)!r}); '
                      f'd = PluginDefinition("memory", "remember", {spec!r}, {{"offset":2}}); '
                      'print(instantiate_plugin(d).value)')
            result = subprocess.run([sys.executable, '-c', script], check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), '9')

    def test_manifest_upload_list_and_arm_on_a_host(self):
        with image_source(1) as root, plugin_host(root) as (host, base):
            source = root / 'src'
            source.mkdir()
            for step in ('perception', 'memory', 'proposal'):
                write_plugin(source / (step + '.py'), step, 'prototype-' + step, 3)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps(dict(name='experiment', source_directory='src', plugins=[
                dict(name=f'{step}/prototype-{step}', file=f'src/{step}.py', entrypoint=f'{step}:Prototype')
                for step in ('perception', 'memory', 'proposal')
            ])))
            before = catalog(base)['arming']
            uploaded = invoke_automa('plugins', 'upload', '--url', base, '--manifest', str(manifest)).stdout
            self.assertIn('Collection: experiment', uploaded)
            self.assertIn('--collection experiment', uploaded)
            self.assertEqual(catalog(base)['arming'], before)
            listing = json.loads(invoke_automa('plugins', 'list', '--url', base, '--long', '--json').stdout)
            self.assertEqual(listing['catalog']['collections']['experiment'],
                             ['perception/prototype-perception', 'memory/prototype-memory', 'proposal/prototype-proposal'])
            receipt = json.loads(invoke_automa('plugins', 'arm', '--url', base, '--collection', 'experiment', '--json').stdout)
            self.assertTrue(receipt['ok'])
            result = host.run(frame(root, 1))
            self.assertEqual(result.proposal.candidates[0].metadata['memory']['perception'], 3)
            prototypes = lambda: [item['id'] for items in catalog(base)['arming']['requested'].values()
                                  for item in items if item['id'].startswith('prototype-')]
            self.assertEqual(prototypes(), ['prototype-memory', 'prototype-perception', 'prototype-proposal'])
            disarmed = invoke_automa('plugins', 'disarm', '--url', base, '--collection', 'experiment', '--json')
            self.assertTrue(json.loads(disarmed.stdout)['ok'])
            self.assertEqual(prototypes(), [])
            armed = invoke_automa('plugins', 'upload', '--url', base, '--manifest', str(manifest), '--arm', '--json')
            self.assertEqual(json.loads(armed.stdout)['collection'], 'experiment')

            before = catalog(base)['arming']
            memory = source / 'memory.py'
            memory.write_text(memory.read_text().replace(
                "    def update(self, context):",
                "    def __init__(self): raise ValueError('collection construction failed')\n    def update(self, context):"))
            failed = invoke_automa('plugins', 'upload', '--url', base, '--manifest', str(manifest),
                                   '--arm', '--json', check=False)
            self.assertEqual(failed.returncode, 2)
            receipt = json.loads(failed.stdout)
            self.assertTrue(receipt['upload']['ok'])
            self.assertFalse(receipt['ok'])
            self.assertIn('collection construction failed', receipt['error'])
            self.assertEqual(catalog(base)['arming']['applied'], before['applied'])
            self.assertEqual(catalog(base)['arming']['requested'], before['requested'])
