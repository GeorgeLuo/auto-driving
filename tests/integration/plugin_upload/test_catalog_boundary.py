"""Catalog writes cannot be induced by another browser origin or a form POST."""

from __future__ import annotations

import base64
import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from autonomy.decision_cycle.steps import decision_steps
from cli.automa_cli.runtime_view import RuntimeViewServer
from implementations.runtime.picar import create_host
from tests.cli.workbench_fixtures import ImageReplayRunner, image_source, serve_workbench
from tests.integration.plugin_upload.test_flows import get_json, upload


class CatalogWriteBoundaryFlows(unittest.TestCase):
    def test_browser_and_form_requests_cannot_upload_or_arm_through_viewer_or_workbench(self):
        with image_source(1) as root:
            host = create_host(steps=decision_steps())
            self.addCleanup(host.close)
            host.follow_activations({}, root / "runtime")
            view = RuntimeViewServer(vehicle_id="picar", automation_dir=root / "automation",
                                     port=0, plugin_catalog=host.plugin_catalog).start()
            self.addCleanup(view.stop)
            workbench = serve_workbench(self, ImageReplayRunner(root))
            for base in (view.url, workbench):
                with self.subTest(endpoint=base):
                    file = root / "prototype.py"
                    file.write_text("raise RuntimeError('rejected uploads must never execute')")
                    upload(base, file, "existing", step="proposal")
                    before = get_json(base, "/api/plugins")
                    payloads = (
                        {"step": "proposal", "plugin_id": "intruder", "entrypoint": "prototype:Prototype",
                         "source_base64": base64.b64encode(file.read_bytes()).decode()},
                        {"operation": "arm", "selections": {"proposal": ["existing"]}},
                    )
                    for payload in payloads:
                        for headers, status in (
                            ({"Origin": "https://attacker.example", "Content-Type": "text/plain"}, 403),
                            ({"Origin": "https://attacker.example", "Content-Type": "application/json"}, 403),
                            ({"Origin": "null", "Content-Type": "application/json"}, 403),
                            ({"Sec-Fetch-Site": "cross-site", "Content-Type": "application/json"}, 403),
                            ({"Content-Type": "text/plain"}, 415),
                            ({"Origin": base.rstrip("/"), "Content-Type": "application/x-www-form-urlencoded"}, 415),
                            ({"Host": "attacker.example", "Content-Type": "application/json"}, 403),
                        ):
                            with self.subTest(payload=payload.get("operation", "upload"), headers=headers):
                                request = Request(base + "api/plugins", data=json.dumps(payload).encode(), headers=headers)
                                with self.assertRaises(HTTPError) as rejected:
                                    urlopen(request, timeout=3)
                                with rejected.exception as response:
                                    self.assertEqual(response.code, status)
                                    failure = json.load(response)
                                self.assertFalse(failure["ok"])
                                self.assertEqual(failure["status"], "failed")
                                self.assertEqual(get_json(base, "/api/plugins"), before)
                    # CLI JSON was accepted above; same-origin browser JSON is also accepted.
                    request = Request(base + "api/plugins", data=json.dumps({**payloads[0], "plugin_id": "browser"}).encode(),
                                      headers={"Origin": base.rstrip("/"), "Content-Type": "application/json; charset=utf-8"})
                    with urlopen(request, timeout=3) as response:
                        self.assertEqual(json.load(response)["status"], "uploaded")
                    if base == view.url:
                        request = Request(base + "api/plugins", data=json.dumps(payloads[1]).encode(),
                                          headers={"Origin": base.rstrip("/"), "Content-Type": "application/json"})
                        with urlopen(request, timeout=3) as response:
                            self.assertEqual(json.load(response)["status"], "requested")


if __name__ == "__main__":
    unittest.main()
