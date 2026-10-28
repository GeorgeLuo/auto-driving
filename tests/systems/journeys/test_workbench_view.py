"""The workbench and runtime home share one viewer and replay session."""

from __future__ import annotations

import unittest

from playwright.sync_api import expect, sync_playwright

from cli.automa_cli.frontend.server import RuntimeViewServer
from cli.automa_cli.workbench_runner import ImageReplayRunner
from tests.shared.cli.workbench_fixtures import image_source


class WorkbenchViewTests(unittest.TestCase):
    def test_browser_loads_a_replay_and_keeps_it_when_navigating_home_and_back(self):
        with image_source(1) as root, sync_playwright() as runtime:
            runner = ImageReplayRunner(root, cadence_ms=250)
            server = RuntimeViewServer(
                host_name="viewer", automation_dir=None, port=0,
                connected=False, replay_runner=runner,
            ).start()
            browser = runtime.chromium.launch()
            try:
                runner.start()
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(server.workbench_url)
                expect(page.locator("#sourceDir")).to_have_value(str(root.resolve()))
                expect(page.locator("#viewerFrame img")).to_be_visible()
                expect(page.locator("#pluginSections .plugin-section").first).to_be_visible()
                run_id = page.request.get(server.workbench_url + "api/state").json()["run_id"]

                page.get_by_role("link", name="Automa", exact=True).click()
                expect(page).to_have_url(server.url)
                page.get_by_role("link", name="Workbench", exact=True).click()
                expect(page).to_have_url(server.workbench_url)
                expect(page.locator("#sourceDir")).to_have_value(str(root.resolve()))
                expect(page.locator("#viewerFrame img")).to_be_visible()
                self.assertEqual(page.request.get(server.workbench_url + "api/state").json()["run_id"], run_id)
                self.assertEqual(errors, [])
            finally:
                browser.close()
                server.stop()


if __name__ == "__main__":
    unittest.main()
