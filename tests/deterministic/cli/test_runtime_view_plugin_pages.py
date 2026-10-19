"""The Automa page reaches both plugin pages, and each can load the feed it shares."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from urllib.request import urlopen

from cli.automa_cli.runtime_view import RuntimeViewServer


class RuntimeViewPluginPagesTests(unittest.TestCase):
    def test_selection_page_links_plugin_pages_whose_scripts_are_served_as_javascript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = RuntimeViewServer(vehicle_id="test-vehicle", automation_dir=Path(tmp), port=0).start()
            self.addCleanup(server.stop)
            base = server.url.rstrip("/")
            with urlopen(base + "/", timeout=1.0) as response:
                links = re.findall(r'href="([^"]+)"', response.read().decode("utf-8"))
            self.assertIn("/plugins", links)
            self.assertIn("/plugin-stack", links)
            for page in ("/plugins", "/plugin-stack"):
                with self.subTest(page=page):
                    with urlopen(base + page, timeout=1.0) as response:
                        self.assertEqual(response.headers.get_content_type(), "text/html")
                        scripts = re.findall(r'<script src="([^"]+)"', response.read().decode("utf-8"))
                    self.assertTrue(scripts)
                    # nosniff makes a browser refuse a script served under any other type.
                    for script in scripts:
                        with urlopen(base + script, timeout=1.0) as response:
                            self.assertEqual(response.headers.get_content_type(), "text/javascript")
                            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
