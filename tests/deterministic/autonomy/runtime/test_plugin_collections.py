"""Collections reuse uploaded source revisions and preserve package dependencies."""
import unittest

from autonomy.plugins import LocalPluginCatalog, instantiate_plugin
from autonomy.runtime.plugin_catalog import PluginCatalogAPI
from tests.shared.integration.collection_fixtures import collection_request as request


class CollectionTests(unittest.TestCase):
    def test_upload_membership_arm_and_revision_isolation(self):
        catalog = LocalPluginCatalog()
        requests = []
        api = PluginCatalogAPI(catalog, arm=lambda selection: requests.append(selection) or {"ok": True})
        uploaded = api(request())
        self.assertTrue(uploaded["ok"])
        self.assertEqual(uploaded["catalog_version"], 1)
        self.assertEqual(api()["collections"], {"experiment": {"memory": ["remember"], "proposal": ["propose"]}})
        self.assertEqual(requests, [])
        self.assertTrue(api(dict(operation="arm", collection="experiment"))["ok"])
        self.assertEqual(requests, [{"memory": ["remember"], "proposal": ["propose"]}])
        prior = catalog.resolve("memory", "remember")
        first = instantiate_plugin(prior)
        self.assertEqual(first.value, 9)
        self.assertTrue(api(request(17))["ok"])
        self.assertEqual(instantiate_plugin(catalog.resolve("memory", "remember")).value, 19)
        self.assertEqual(instantiate_plugin(prior).value, 9)
        self.assertIn("unknown plugin collection 'missing'", api(dict(operation="arm", collection="missing"))["error"])

    def test_a_collection_lists_its_own_entrypoints_and_files_from_one_revision(self):
        listed = PluginCatalogAPI(LocalPluginCatalog())(request())["plugins"]
        self.assertEqual([(item["id"], item["entrypoint"], item["metadata"]["filename"]) for item in listed],
                         [("remember", "pkg.plugin:Remember", "pkg/plugin.py"),
                          ("propose", "pkg.plugin:Propose", "pkg/plugin.py")])
        self.assertEqual(len({item["metadata"]["revision"] for item in listed}), 1)

    def test_disarming_a_collection_leaves_one_plugin_steps_their_plugin(self):
        catalog = LocalPluginCatalog()
        requests = []
        api = PluginCatalogAPI(catalog, disarm=lambda selection, all_plugins: requests.append(selection) or {"ok": True})
        payload = request()
        payload["plugins"].append(dict(step="plan", plugin_id="choose", entrypoint="pkg.plugin:Propose"))
        self.assertTrue(api(payload)["ok"])
        only_plan = request()
        only_plan.update(name="only-plan", plugins=[dict(step="plan", plugin_id="choose", entrypoint="pkg.plugin:Propose")])
        self.assertTrue(api(only_plan)["ok"])

        self.assertEqual(api(dict(operation="disarm", collection="experiment")), {"ok": True})
        self.assertEqual(requests, [{"memory": ["remember"], "proposal": ["propose"]}])
        refused = api(dict(operation="disarm", collection="only-plan"))
        self.assertFalse(refused["ok"])
        self.assertIn("only arms one-plugin steps", refused["error"])
        self.assertFalse(api(dict(operation="disarm", collection="missing"))["ok"])
        self.assertEqual(len(requests), 1)

    def test_invalid_package_or_membership_does_not_publish_a_collection(self):
        for change, error in (
            ("syntax", "pkg/shared.py:1"), ("path", "invalid collection source path: ../outside.py"),
            ("duplicate", "collection plugins must be unique"), ("step", "unknown cycle step 'unknown'"),
            ("entrypoint", "collection entrypoint has no source: missing:Remember"),
            ("config", "plugin config must be an object"), ("field", "missing field: plugin_id"),
        ):
            with self.subTest(change=change):
                payload = request()
                if change == "syntax":
                    payload["sources"]["pkg/shared.py"] = "class (\n"
                if change == "path":
                    payload["sources"]["../outside.py"] = "pass"
                if change == "duplicate":
                    payload["plugins"].append(payload["plugins"][0])
                if change == "step":
                    payload["plugins"][0]["step"] = "unknown"
                if change == "entrypoint":
                    payload["plugins"][0]["entrypoint"] = "missing:Remember"
                if change == "config":
                    payload["plugins"][0]["config"] = []
                if change == "field":
                    del payload["plugins"][0]["plugin_id"]
                catalog = LocalPluginCatalog()
                receipt = PluginCatalogAPI(catalog)(payload)
                self.assertFalse(receipt["ok"])
                self.assertIn(error, receipt["error"])
                self.assertEqual(catalog.read_with_collections(), (0, (), {}))

    def test_upload_does_not_import_package_and_failed_reupload_preserves_membership(self):
        catalog = LocalPluginCatalog()
        api = PluginCatalogAPI(catalog)
        payload = request()
        payload["sources"]["pkg/__init__.py"] = "raise RuntimeError('not during upload')"
        self.assertTrue(api(payload)["ok"])
        with self.assertRaisesRegex(RuntimeError, "not during upload"):
            instantiate_plugin(catalog.resolve("memory", "remember"))
        bad = request()
        bad["sources"]["pkg/plugin.py"] = "class ("
        before = api()
        self.assertFalse(api(bad)["ok"])
        self.assertEqual(api(), before)
