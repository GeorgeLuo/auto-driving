"""Collections reuse uploaded source revisions and preserve package dependencies."""
import unittest

from autonomy.plugins import LocalPluginCatalog, instantiate_plugin
from autonomy.runtime.plugin_catalog import PluginCatalogAPI
from tests.shared.integration.collection_fixtures import collection_request as request


class CollectionTests(unittest.TestCase):
    def test_upload_membership_arm_and_revision_isolation(self):
        catalog = LocalPluginCatalog()
        requests = []
        api = PluginCatalogAPI(catalog, arm=lambda selection: requests.append(selection) or {'ok': True})
        self.assertTrue(api(request())['ok'])
        self.assertEqual(api()['collections'], {'experiment': ['memory/remember', 'proposal/propose']})
        self.assertEqual(requests, [])
        self.assertTrue(api(dict(operation='arm_collection', name='experiment'))['ok'])
        self.assertEqual(requests, [{'memory': ['remember'], 'proposal': ['propose']}])
        prior = catalog.resolve('memory', 'remember')
        first = instantiate_plugin(prior)
        self.assertEqual(first.value, 9)
        self.assertTrue(api(request(17))['ok'])
        self.assertEqual(instantiate_plugin(catalog.resolve('memory', 'remember')).value, 19)
        self.assertEqual(instantiate_plugin(prior).value, 9)
        self.assertFalse(api(dict(operation='arm_collection', name='missing'))['ok'])

    def test_disarming_a_collection_leaves_one_plugin_steps_their_plugin(self):
        catalog = LocalPluginCatalog()
        requests = []
        api = PluginCatalogAPI(catalog, disarm=lambda selection: requests.append(selection) or {'ok': True})
        payload = request()
        payload['plugins'].append(dict(name='plan/choose', entrypoint='pkg.plugin:Propose'))
        self.assertTrue(api(payload)['ok'])
        only_plan = request()
        only_plan.update(name='only-plan', plugins=[dict(name='plan/choose', entrypoint='pkg.plugin:Propose')])
        self.assertTrue(api(only_plan)['ok'])

        self.assertEqual(api(dict(operation='disarm_collection', name='experiment')),
                         {'ok': True, 'collection': 'experiment'})
        self.assertEqual(requests, [{'memory': ['remember'], 'proposal': ['propose']}])
        refused = api(dict(operation='disarm_collection', name='only-plan'))
        self.assertFalse(refused['ok'])
        self.assertIn('only arms one-plugin steps', refused['error'])
        self.assertFalse(api(dict(operation='disarm_collection', name='missing'))['ok'])
        self.assertEqual(len(requests), 1)

    def test_invalid_package_or_membership_does_not_publish_a_collection(self):
        for change in ('syntax', 'path', 'duplicate', 'step', 'entrypoint', 'config'):
            with self.subTest(change=change):
                payload = request()
                if change == 'syntax':
                    payload['sources']['pkg/shared.py'] = 'class (\n'
                if change == 'path':
                    payload['sources']['../outside.py'] = 'pass'
                if change == 'duplicate':
                    payload['plugins'].append(payload['plugins'][0])
                if change == 'step':
                    payload['plugins'][0]['name'] = 'unknown/remember'
                if change == 'entrypoint':
                    payload['plugins'][0]['entrypoint'] = 'missing:Remember'
                if change == 'config':
                    payload['plugins'][0]['config'] = []
                catalog = LocalPluginCatalog()
                receipt = PluginCatalogAPI(catalog)(payload)
                self.assertFalse(receipt['ok'])
                self.assertEqual(catalog.collections(), {})
                self.assertEqual(catalog.read(), (0, ()))

    def test_upload_does_not_import_package_and_failed_reupload_preserves_membership(self):
        catalog = LocalPluginCatalog()
        api = PluginCatalogAPI(catalog)
        payload = request()
        payload['sources']['pkg/__init__.py'] = "raise RuntimeError('not during upload')"
        self.assertTrue(api(payload)['ok'])
        with self.assertRaisesRegex(RuntimeError, 'not during upload'):
            instantiate_plugin(catalog.resolve('memory', 'remember'))
        bad = request()
        bad['sources']['pkg/plugin.py'] = 'class ('
        before = api()
        self.assertFalse(api(bad)['ok'])
        self.assertEqual(api(), before)
