"""All cycle steps share catalog selection reporting and live viewer semantics."""

from __future__ import annotations

import unittest
from dataclasses import replace

from playwright.sync_api import sync_playwright, expect

from autonomy.decision_cycle.activation import STEPS, step_activation_path, write_step_activation
from cli.automa_cli.plugin_catalog import ONE_PLUGIN_STEPS
from implementations.decision_cycle.catalog import packaged_activation
from tests.shared.cli.workbench_fixtures import image_source
from tests.shared.integration.plugin_flow_fixtures import arm_step, frame, plugin_host, upload, write_plugin


class LiveCatalogViewTests(unittest.TestCase):
    def test_browser_follows_upload_arm_revision_and_deselection_without_reload(self):
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch()
            try:
                activations = {step: packaged_activation(step) for step in STEPS}
                for vehicle in ("chase", "picar"):
                    with self.subTest(vehicle=vehicle), image_source(1) as root, plugin_host(root, vehicle, activations) as (host, base):
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
                            for index, step in enumerate(STEPS, start=1):
                                plugin_id = f"prototype-{step}"
                                default = activations[step].plugins[0] if step in ONE_PLUGIN_STEPS else None
                                packaged = page.locator(f'tr[data-step="{step}"][data-plugin-id="{default}"]')
                                file = root / f"{step}.py"
                                write_plugin(file, step, plugin_id, 1)
                                _, uploaded = upload(base, file, step, plugin_id)
                                row = page.locator(f'tr[data-step="{step}"][data-plugin-id="{plugin_id}"]')
                                expect(row).to_have_count(1)
                                expect(row.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Disabled")
                                expect(row.locator("td").nth(3)).to_have_text("uploaded")
                                arm_step(base, step, plugin_id)
                                expect(row.locator("td").nth(5)).to_have_text(uploaded["plugin"]["metadata"]["revision"])
                                expect(row.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Disabled")
                                host.run(frame(root, index * 2))
                                expect(row.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Enabled")
                                if default:
                                    # Arming a one-plugin step swaps out its packaged plugin.
                                    expect(packaged.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Disabled")
                                write_plugin(file, step, plugin_id, 2)
                                _, next_revision = upload(base, file, step, plugin_id)
                                expect(row.locator("td").nth(4)).to_have_text(next_revision["plugin"]["metadata"]["revision"])
                                expect(row.locator("td").nth(6)).to_have_text(uploaded["plugin"]["metadata"]["revision"])
                                expect(row.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Enabled")
                                if default:
                                    # A one-plugin step cannot be emptied; arming its default deselects the upload.
                                    arm_step(base, step, default)
                                else:
                                    write_step_activation(step_activation_path(root / "runtime", step),
                                                          replace(activations[step], plugins=()))
                                    host.sync_selection()
                                expect(row.locator("td").nth(5)).to_have_text("—")
                                expect(row.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Enabled")
                                host.run(frame(root, index * 2 + 1))
                                expect(row.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Disabled")
                                if default:
                                    expect(packaged.locator("td").nth(2).locator(".status-dot")).to_have_attribute("aria-label", "Enabled")
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
