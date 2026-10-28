from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.vehicles.probes import Candidate, ProbeResult, chase_operator_url, normalize_chase_url
from implementations.vehicle.chase_sim.presence import MetricsUiPresence
from tests.shared.cli.automation_pipeline.pipeline_fixtures import chase_runtime
from tests.shared.cli.invoke import invoke_automa
from tests.shared.implementations.runtime.wait import wait_until
from tests.support.fake_hosts import closed_url
from tests.support.fake_metrics_ui import fake_metrics_ui
from tests.support.runtime_fixtures import write_host_fixture


class DiscoveredVehicleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.default = self.enterContext(fake_metrics_ui())
        self.other = self.enterContext(fake_metrics_ui())
        self.other_http = chase_operator_url(self.other.url)

    def automa(self, *args: str, default: str | None = None) -> subprocess.CompletedProcess[str]:
        return invoke_automa("vehicles", *args, runtime_root=self.runtime_root,
                             extra_env={"CHASE_UI_WS_URL": default or self.default.url}, check=False)

    def listed(self) -> list[dict[str, str]]:
        path = self.runtime_root / "discovered.json"
        return json.loads(path.read_text(encoding="utf-8"))["vehicles"] if path.exists() else []

    def headings(self) -> list[str]:
        return [block.splitlines()[0] for block in self.automa("status").stdout.strip().split("\n\n")]

    def test_a_simulator_that_can_take_a_host_is_listed_after_the_default(self) -> None:
        result = self.automa("discover", "--provider", "chase-sim", "--url", self.other_http)

        self.assertEqual((result.returncode, result.stdout),
                         (0, f"Discovered Simulator (chase-sim) at {self.other_http}\n"))
        self.assertEqual(self.listed(), [{"provider": "chase-sim", "url": self.other.url}])
        self.assertEqual(self.headings(), [
            f"Simulator (chase-sim)  running at {chase_operator_url(self.default.url)}",
            f"Simulator (chase-sim)  running at {self.other_http}",
        ])

    def test_a_simulator_that_cannot_take_a_host_is_listed_only_with_force(self) -> None:
        url = closed_url()
        ws_url = normalize_chase_url(url)

        refused = self.automa("discover", "--provider", "chase-sim", "--url", url)
        self.assertEqual(refused.returncode, 2)
        self.assertTrue(refused.stdout.startswith(f"No Chase simulator can take a host at {ws_url}: "), refused.stdout)
        self.assertEqual(self.listed(), [])

        forced = self.automa("discover", "--provider", "chase-sim", "--url", url, "--force")
        self.assertEqual(forced.returncode, 0, forced.stdout)
        self.assertTrue(forced.stdout.startswith(
            f"Discovered Simulator (chase-sim) at {chase_operator_url(ws_url)}, which cannot take a host now: "
        ), forced.stdout)
        self.assertEqual(self.listed(), [{"provider": "chase-sim", "url": ws_url}])
        self.assertIn(f"Simulator (chase-sim)  unreachable at {chase_operator_url(ws_url)}", self.headings())

    def test_a_picar_is_listed_once_its_machine_answers(self) -> None:
        url = "http://piracer.local:8887"
        for runtime_state, code in (("endpoint_unreachable", 2), ("server_not_listening", 0)):
            probe = ProbeResult(active=False, candidate=Candidate("picar", url, "discovered"),
                                diagnostics={"runtime_state": runtime_state}, error="probe detail")
            with self.subTest(runtime_state=runtime_state), patch("cli.automa_cli.vehicles.probes._probe_picar", return_value=probe):
                result = self.automa("discover", "--provider", "picar", "--url", url + "/")
                headings = self.headings()

            self.assertEqual(result.returncode, code, result.stdout)
        self.assertEqual(self.listed(), [{"provider": "picar", "url": url}])
        self.assertIn(f"PiCar (picar)  reachable, no host listening at {url}: probe detail", headings)

    def test_listing_a_vehicle_again_or_the_default_changes_nothing(self) -> None:
        self.automa("discover", "--provider", "chase-sim", "--url", self.other_http)

        for url in (self.other.url, chase_operator_url(self.default.url)):
            with self.subTest(url=url):
                result = self.automa("discover", "--provider", "chase-sim", "--url", url)

                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertTrue(result.stdout.startswith("Already listed: Simulator (chase-sim) at "), result.stdout)
        self.assertEqual(self.listed(), [{"provider": "chase-sim", "url": self.other.url}])

    def test_remove_takes_either_url_form_and_keeps_the_default(self) -> None:
        self.automa("discover", "--provider", "chase-sim", "--url", self.other.url)
        default_http = chase_operator_url(self.default.url)

        default = self.automa("remove", "--url", default_http)
        unknown = self.automa("remove", "--url", "http://localhost:1")
        removed = self.automa("remove", "--url", self.other_http)
        again = self.automa("remove", "--url", self.other_http)

        self.assertEqual((default.returncode, default.stdout),
                         (2, f"{default_http} is the default simulator and is always listed.\n"))
        self.assertEqual((unknown.returncode, unknown.stdout), (2,
            f"No discovered vehicle at http://localhost:1. Discovered: Simulator (chase-sim) at {self.other_http}.\n"))
        self.assertEqual((removed.returncode, removed.stdout), (0, f"Removed Simulator (chase-sim) at {self.other_http}\n"))
        self.assertEqual((again.returncode, again.stdout),
                         (2, f"No discovered vehicle at {self.other_http}. None discovered.\n"))
        self.assertEqual(self.listed(), [])

    def test_a_host_is_attached_to_its_own_simulator(self) -> None:
        self.automa("discover", "--provider", "chase-sim", "--url", self.other.url)
        write_host_fixture(self.runtime_root, "chase-sim-chaser", url=self.other.url)
        host = self.enterContext(chase_runtime(self.runtime_root))
        client = {"kind": "host", "host_name": "chase-sim-chaser", "url": host.base_url, "pid": os.getpid()}
        self.enterContext(MetricsUiPresence(self.other.url, client, retry_s=0.05))

        # The default simulator lists no hosts; the attached one lists this host name.
        from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWsClient
        other = MetricsUiWsClient(self.other.url)
        default = MetricsUiWsClient(self.default.url)
        [entry] = wait_until(
            lambda: [agent for agent in other.list_agents() if agent.get("client") == client],
            message="the discovered simulator never listed its host as attached",
        )
        self.assertEqual(entry["client"]["host_name"], "chase-sim-chaser")
        self.assertFalse(any(agent.get("client") == client for agent in default.list_agents()))

    def test_status_exits_one_only_when_no_vehicle_is_reachable(self) -> None:
        unreachable = closed_url()
        self.automa("discover", "--provider", "chase-sim", "--url", self.other.url)
        self.automa("discover", "--provider", "chase-sim", "--url", closed_url(), "--force")

        reachable = self.automa("status", default=unreachable)
        self.automa("remove", "--url", self.other.url)
        none = self.automa("status", default=unreachable)

        self.assertEqual(reachable.returncode, 0, reachable.stdout)
        self.assertEqual(none.returncode, 1, none.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
