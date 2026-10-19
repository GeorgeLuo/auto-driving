"""A live catalog serves the uploaded source a cycle ran, by its content name only."""

from __future__ import annotations

import base64
import hashlib
import unittest
import uuid

from cli.automa_cli.plugin_catalog import PluginCatalogClient
from tests.shared.cli.workbench_fixtures import image_source
from tests.shared.integration.plugin_flow_fixtures import plugin_host, upload


class UploadedSourceReadFlows(unittest.TestCase):
    def test_the_catalog_serves_an_uploaded_source_by_its_sha256_name(self):
        with image_source(1) as root, plugin_host(root) as (_, base):
            source = f"class Prototype:\n    pass\n# {uuid.uuid4().hex}\n".encode()
            file = root / "prototype.py"
            file.write_bytes(source)
            upload(base, file, "memory", "prototype")
            name = f"{hashlib.sha256(source).hexdigest()}.py"
            client = PluginCatalogClient(base)
            served = client({"operation": "source", "name": name})
            self.assertTrue(served["ok"], served)
            self.assertEqual(served["name"], name)
            self.assertEqual(base64.b64decode(served["source_base64"]), source)
            for bad in (f"../{name}", name.upper(), "prototype.py", name[:-3], 7):
                with self.subTest(name=bad):
                    refused = client({"operation": "source", "name": bad})
                    self.assertFalse(refused["ok"])
                    self.assertIn("<sha256>.py", refused["error"])
            missing = client({"operation": "source", "name": f"{'0' * 64}.py"})
            self.assertFalse(missing["ok"])
            self.assertIn("not available here", missing["error"])
            self.assertFalse(client({"operation": "source"})["ok"])


if __name__ == "__main__":
    unittest.main()
