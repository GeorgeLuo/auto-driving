"""The catalog request handler that host, viewer, and workbench transports share."""

from __future__ import annotations

import base64
import unittest

from autonomy.plugins import LocalPluginCatalog
from autonomy.runtime.plugin_catalog import PluginCatalogAPI

SOURCE = b"class Prototype:\n    plugin_id = 'prototype'\n"


def _upload(**overrides) -> dict:
    payload = {
        "step": "proposal", "plugin_id": "prototype", "entrypoint": "prototype:Prototype",
        "source_base64": base64.b64encode(SOURCE).decode(),
    }
    payload.update(overrides)
    return payload


class PluginCatalogAPITests(unittest.TestCase):
    def test_malformed_requests_fail_without_changing_the_catalog(self) -> None:
        catalog = LocalPluginCatalog()
        api = PluginCatalogAPI(catalog)
        without_entrypoint = _upload()
        del without_entrypoint["entrypoint"]
        cases = (
            (without_entrypoint, "missing field: entrypoint"),
            (_upload(source_base64="not base64!"), "Only base64 data is allowed"),
            (_upload(source_base64=base64.b64encode(b"class (\n").decode(), filename="broken.py"), "broken.py:1: "),
            ({"operation": "arm", "selections": {"proposal": ["prototype"]}}, "arming requires a vehicle runtime host"),
        )
        for payload, error in cases:
            with self.subTest(error=error):
                result = api(payload)
                self.assertEqual((result["ok"], result["status"]), (False, "failed"))
                self.assertIn(error, result["error"])
        self.assertEqual(catalog.snapshot(), (0, ()))

    def test_a_host_catalog_forwards_arm_requests_and_requires_selections(self) -> None:
        requests = []
        api = PluginCatalogAPI(LocalPluginCatalog(), arm=lambda selections: requests.append(selections) or {"ok": True})
        receipt = api(_upload())
        self.assertEqual((receipt["status"], receipt["catalog_version"]), ("uploaded", 1))
        self.assertEqual(api({"operation": "arm", "selections": {"proposal": ["prototype"]}}), {"ok": True})
        missing = api({"operation": "arm"})
        self.assertEqual((missing["status"], missing["error"]), ("failed", "missing field: selections"))
        self.assertEqual(requests, [{"proposal": ["prototype"]}])


if __name__ == "__main__":
    unittest.main()
