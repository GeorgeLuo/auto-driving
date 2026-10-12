"""All cycle steps share catalog selection reporting and live viewer semantics."""

from __future__ import annotations

import unittest
from dataclasses import replace

from playwright.sync_api import sync_playwright, expect

from autonomy.decision_cycle.activation import STEPS, step_activation_path, write_step_activation
from autonomy.runtime.plugin_catalog import ARMING_STEPS
from implementations.decision_cycle.catalog import default_activations
from tests.shared.cli.workbench_fixtures import image_source
from tests.systems.journeys.plugins import test_arming as test_flows


class LiveCatalogViewTests(unittest.TestCase):
    host = test_flows.PluginArmingFlows.host

    def test_browser_follows_upload_arm_revision_and_deselection_without_reload(self):
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch()
            try:
                activations = default_activations()
                for vehicle in ("chase", "picar"):
                    with self.subTest(vehicle=vehicle), image_source(1) as root, self.host(root, vehicle, activations) as (host, base):
                        page = browser.new_page()
                        try:
                            page.goto(base + "plugins")
                            expect(page.get_by_role("columnheader", name="Enabled", exact=True)).to_be_visible()
                            expect(page.get_by_role("columnheader", name="Origin", exact=True)).to_be_visible()
                            revisions = page.get_by_role("checkbox", name="Show revisions")
                            available = page.get_by_role("columnheader", name="Available revision", exact=True)
                            expect(available).to_be_hidden()
                            revisions.check()
                            expect(available).to_be_visible()
                            for step in STEPS:
                                for plugin_id in activations[step].plugins:
                                    row = page.locator(f'tr[data-step="{step}"][data-plugin-id="{plugin_id}"]')
                                    expect(row.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Enabled")
                                    expect(row.locator("td").nth(3)).to_have_text("packaged")
                            for index, step in enumerate(ARMING_STEPS, start=1):
                                plugin_id = f"prototype-{step}"
                                file = root / f"{step}.py"
                                test_flows.write_plugin(file, step, plugin_id, 1)
                                _, uploaded = test_flows.upload(base, file, step, plugin_id)
                                row = page.locator(f'tr[data-step="{step}"][data-plugin-id="{plugin_id}"]')
                                expect(row).to_have_count(1)
                                expect(row.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Disabled")
                                expect(row.locator("td").nth(3)).to_have_text("uploaded")
                                test_flows.arm_step(base, step, plugin_id)
                                expect(row.locator("td").nth(5)).to_have_text(uploaded["plugin"]["metadata"]["revision"])
                                expect(row.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Disabled")
                                host.run(test_flows.frame(root, index * 2))
                                expect(row.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Enabled")
                                test_flows.write_plugin(file, step, plugin_id, 2)
                                _, next_revision = test_flows.upload(base, file, step, plugin_id)
                                expect(row.locator("td").nth(4)).to_have_text(next_revision["plugin"]["metadata"]["revision"])
                                expect(row.locator("td").nth(6)).to_have_text(uploaded["plugin"]["metadata"]["revision"])
                                expect(row.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Enabled")
                                write_step_activation(step_activation_path(root / "runtime", step),
                                                      replace(activations[step], plugins=()))
                                host.sync_selection()
                                expect(row.locator("td").nth(5)).to_have_text("—")
                                expect(row.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Enabled")
                                host.run(test_flows.frame(root, index * 2 + 1))
                                expect(row.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Disabled")
                            revisions.uncheck()
                            expect(available).to_be_hidden()
                            expect(page.get_by_role("columnheader", name="Source", exact=True)).to_be_visible()
                            expect(page.locator("#status")).to_be_hidden()
                        finally:
                            page.close()
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
