from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from cli.automa_cli.providers.urls import chase_operator_url, normalize_chase_url
from cli.automa_cli.providers.models import Candidate, ProbeResult
from cli.automa_cli.simulators.store import SIMULATOR_SCHEMA
from implementations.vehicle.chase_sim.presence import MetricsUiPresence
from tests.shared.cli.automation_pipeline.pipeline_fixtures import chase_runtime
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.vehicle_fixtures import probe_returning
from tests.shared.implementations.runtime.wait import wait_until
from tests.support.fake_hosts import closed_url
from tests.support.fake_metrics_ui import fake_metrics_ui
from tests.support.runtime_fixtures import write_host_fixture, runtime_root_env


class DiscoveredVehicleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.default = self.enterContext(fake_metrics_ui())
        self.other = self.enterContext(fake_metrics_ui())
        self.other_http = chase_operator_url(self.other.url)

    def automa(self, *args: str) -> subprocess.CompletedProcess[str]:
        return invoke_automa("vehicles", *args, runtime_root=self.runtime_root, check=False)

    def listed(self) -> list[dict[str, str]]:
        path = self.runtime_root / "discovered.json"
        return json.loads(path.read_text(encoding="utf-8"))["vehicles"] if path.exists() else []

    def headings(self) -> list[str]:
        return [block.splitlines()[0] for block in self.automa("status").stdout.strip().split("\n\n")]

    def test_a_simulator_that_can_take_a_host_is_listed(self) -> None:
        result = self.automa("discover", "--provider", "chase-sim", "--url", self.other_http)

        self.assertEqual((result.returncode, result.stdout),
                         (0, f"Discovered Simulator (chase-sim) at {self.other_http}\n"))
        self.assertEqual(self.listed(), [{"provider": "chase-sim", "url": self.other.url}])
        self.assertEqual(self.headings(), [
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
            with self.subTest(runtime_state=runtime_state), probe_returning(probe):
                result = self.automa("discover", "--provider", "picar", "--url", url + "/")
                headings = self.headings()

            self.assertEqual(result.returncode, code, result.stdout)
        self.assertEqual(self.listed(), [{"provider": "picar", "url": url}])
        self.assertIn(f"PiCar (picar)  reachable, no host listening at {url}: probe detail", headings)

    def test_listing_a_vehicle_again_changes_nothing(self) -> None:
        self.automa("discover", "--provider", "chase-sim", "--url", self.other_http)

        result = self.automa("discover", "--provider", "chase-sim", "--url", self.other.url)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout, f"Already listed: Simulator (chase-sim) at {self.other_http}\n")
        self.assertEqual(self.listed(), [{"provider": "chase-sim", "url": self.other.url}])

    def test_remove_takes_either_url_form(self) -> None:
        self.automa("discover", "--provider", "chase-sim", "--url", self.other.url)

        unknown = self.automa("remove", "--url", "http://localhost:1")
        removed = self.automa("remove", "--url", self.other_http)
        again = self.automa("remove", "--url", self.other_http)

        self.assertEqual((unknown.returncode, unknown.stdout), (2,
            f"No listed vehicle at http://localhost:1. Listed: Simulator (chase-sim) at {self.other_http}.\n"))
        self.assertEqual((removed.returncode, removed.stdout), (0, f"Removed Simulator (chase-sim) at {self.other_http}\n"))
        self.assertEqual((again.returncode, again.stdout),
                         (2, f"No listed vehicle at {self.other_http}. None listed.\n"))
        self.assertEqual(self.listed(), [])

    def test_a_host_is_attached_to_its_own_simulator(self) -> None:
        self.automa("discover", "--provider", "chase-sim", "--url", self.other.url)
        write_host_fixture(self.runtime_root, "chase-sim-chaser", url=self.other.url)
        host = self.enterContext(chase_runtime(self.runtime_root))
        client = {"kind": "host", "host_name": "chase-sim-chaser", "url": host.base_url, "pid": os.getpid()}
        self.enterContext(MetricsUiPresence(self.other.url, client, retry_s=0.05))

        # The other simulator lists no hosts; the discovered one lists this host name.
        from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWsClient
        other = MetricsUiWsClient(self.other.url)
        default = MetricsUiWsClient(self.default.url)
        [entry] = wait_until(
            lambda: [agent for agent in other.list_agents() if agent.get("client") == client],
            message="the discovered simulator never listed its host as attached",
        )
        self.assertEqual(entry["client"]["host_name"], "chase-sim-chaser")
        self.assertFalse(any(agent.get("client") == client for agent in default.list_agents()))

    def test_status_exits_one_only_when_listed_vehicles_are_all_unreachable(self) -> None:
        unreachable = closed_url()
        self.automa("discover", "--provider", "chase-sim", "--url", self.other.url)
        self.automa("discover", "--provider", "chase-sim", "--url", unreachable, "--force")

        reachable = self.automa("status")
        self.automa("remove", "--url", self.other.url)
        none = self.automa("status")
        self.automa("remove", "--url", unreachable)
        empty = self.automa("status")
        machine = self.automa("status", "--json")

        self.assertEqual(reachable.returncode, 0, reachable.stdout)
        self.assertEqual(none.returncode, 1, none.stdout)
        self.assertEqual((empty.returncode, empty.stdout), (0, "None listed.\n"))
        self.assertEqual(json.loads(machine.stdout), {"schema": "automa_vehicles_status", "vehicles": []})

    def test_status_url_shows_one_vehicle_and_the_hosts_created_at_it(self) -> None:
        picar = "http://piracer.local:8887"
        self.automa("discover", "--provider", "chase-sim", "--url", self.other.url)
        with probe_returning(ProbeResult(active=False, candidate=Candidate("picar", picar, "discovered"),
                                         diagnostics={"runtime_state": "server_not_listening"})):
            self.automa("discover", "--provider", "picar", "--url", picar)
        write_host_fixture(self.runtime_root, "chaser", url=self.other.url)
        write_host_fixture(self.runtime_root, "follower", url=self.other.url)
        write_host_fixture(self.runtime_root, "driver", provider="picar", url=picar + "/")

        # Either URL form selects the vehicle; only it is probed and shown.
        for url in (self.other.url, self.other_http):
            with self.subTest(url=url):
                result = self.automa("status", "--url", url)
                self.assertEqual((result.returncode, result.stdout), (0, "\n".join([
                    f"Simulator (chase-sim)  running at {self.other_http}",
                    "  frontend connected, game chase",
                    "  used by hosts chaser, follower",
                    "",
                ])))
        with probe_returning(ProbeResult(active=False, candidate=Candidate("picar", picar, "discovered"),
                                         diagnostics={"runtime_state": "server_not_listening"})):
            [vehicle] = json.loads(self.automa("status", "--url", picar, "--json").stdout)["vehicles"]
        self.assertEqual((vehicle["provider"], vehicle["url"], vehicle["hosts"]), ("picar", picar, ["driver"]))

    def test_status_url_of_an_unlisted_vehicle_exits_one(self) -> None:
        result = self.automa("status", "--url", "http://localhost:1")
        machine = self.automa("status", "--url", "http://localhost:1", "--json")

        self.assertEqual((result.returncode, result.stdout), (1, "No listed vehicle at http://localhost:1.\n"))
        self.assertEqual((machine.returncode, json.loads(machine.stdout)["vehicles"]), (1, [
            {"provider": None, "url": "http://localhost:1", "state": "not found", "error": None, "hosts": []},
        ]))


class DiscoveredRecordTests(unittest.TestCase):
    def test_an_unreadable_record_lists_no_vehicles(self):
        from cli.automa_cli.vehicles import store

        good = {"provider": "picar", "url": "http://car:8887"}
        for content in ("not json", "[]", json.dumps({"vehicles": "car"}), json.dumps({"schema": "x"}),
                        json.dumps({"vehicles": [good, "car", {"provider": "picar"}]})):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / store.DISCOVERED).write_text(content, encoding="utf-8")
                with runtime_root_env(root):
                    expected = [good] if "car:8887" in content else []
                    self.assertEqual(store.discovered_vehicles(), expected)
        with tempfile.TemporaryDirectory() as directory, runtime_root_env(directory):
            self.assertEqual(store.discovered_vehicles(), [])


class CreatedSimulatorListingTests(unittest.TestCase):
    """A simulator simulators create started can stop being listed, and be listed again, on its own record."""

    def setUp(self) -> None:
        self.runtime_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.server = self.enterContext(fake_metrics_ui())
        self.http = chase_operator_url(self.server.url)
        self.record_path = self.runtime_root / "simulators" / "5052" / "simulator.json"
        self.write_simulator()

    def write_simulator(self, *, pid: int | None = None, started_at_ms: int = 1, url: str | None = None) -> None:
        record = {
            "schema": SIMULATOR_SCHEMA,
            "pid": os.getpid() if pid is None else pid,
            "port": 5052,
            "url": url or self.server.url,
            "metrics_ui_dir": "/tmp/metrics-ui",
            "config": "default",
            "started_at_ms": started_at_ms,
        }
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        self.record_path.write_text(json.dumps(record), encoding="utf-8")

    def automa(self, *args: str) -> subprocess.CompletedProcess[str]:
        return invoke_automa(*args, runtime_root=self.runtime_root, check=False)

    def listed(self) -> bool:
        return json.loads(self.record_path.read_text(encoding="utf-8")).get("listed", True)

    def discovered(self) -> list[dict]:
        path = self.runtime_root / "discovered.json"
        return json.loads(path.read_text(encoding="utf-8"))["vehicles"] if path.exists() else []

    def test_remove_stops_listing_a_created_simulator_and_leaves_its_record(self) -> None:
        status = self.automa("vehicles", "status")
        machine = json.loads(self.automa("vehicles", "status", "--json").stdout)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(status.stdout, (
            f"Simulator (chase-sim)  running at {self.http}\n"
            "  frontend connected, game chase\n"
            f"  created by simulators create, pid {os.getpid()}\n"
        ))
        self.assertEqual(machine["vehicles"][0]["pid"], os.getpid())
        self.assertEqual(self.discovered(), [])

        record = json.loads(self.record_path.read_text(encoding="utf-8"))
        removed = self.automa("vehicles", "remove", "--url", self.http)

        self.assertEqual((removed.returncode, removed.stdout), (0, f"Removed Simulator (chase-sim) at {self.http}\n"))
        self.assertEqual(json.loads(self.record_path.read_text(encoding="utf-8")), {**record, "listed": False})
        self.assertEqual(self.automa("vehicles", "status").stdout, "None listed.\n")
        simulators = self.automa("simulators", "status")
        self.assertEqual(simulators.returncode, 0, simulators.stderr)
        self.assertIn(self.http, simulators.stdout)
        self.assertIn(f"pid {os.getpid()}", simulators.stdout)

    def test_discovering_a_listed_created_simulator_writes_nothing(self) -> None:
        result = self.automa("vehicles", "discover", "--provider", "chase-sim", "--url", self.http)

        self.assertEqual((result.returncode, result.stdout),
                         (0, f"Already listed: Simulator (chase-sim) at {self.http}\n"))
        self.assertEqual(self.discovered(), [])
        self.assertTrue(self.listed())

    def test_discover_lists_an_untracked_simulator_again_without_a_second_record(self) -> None:
        self.automa("vehicles", "remove", "--url", self.server.url)

        result = self.automa("vehicles", "discover", "--provider", "chase-sim", "--url", self.http)

        self.assertEqual((result.returncode, result.stdout),
                         (0, f"Discovered Simulator (chase-sim) at {self.http}\n"))
        self.assertTrue(self.listed())
        self.assertEqual(self.discovered(), [])
        self.assertIn(self.http, self.automa("vehicles", "status").stdout)

    def test_an_untracked_simulator_that_cannot_take_a_host_stays_untracked_without_force(self) -> None:
        url = closed_url()
        self.write_simulator(url=normalize_chase_url(url))
        self.automa("vehicles", "remove", "--url", url)

        refused = self.automa("vehicles", "discover", "--provider", "chase-sim", "--url", url)
        self.assertEqual(refused.returncode, 2, refused.stdout)
        self.assertFalse(self.listed())

        forced = self.automa("vehicles", "discover", "--provider", "chase-sim", "--url", url, "--force")
        self.assertEqual(forced.returncode, 0, forced.stdout)
        self.assertIn("which cannot take a host now", forced.stdout)
        self.assertTrue(self.listed())
        self.assertEqual(self.discovered(), [])

    def test_a_new_simulator_on_the_same_port_is_listed(self) -> None:
        self.automa("vehicles", "remove", "--url", self.http)
        self.write_simulator(started_at_ms=2)

        status = self.automa("vehicles", "status")

        self.assertEqual(status.returncode, 0, status.stdout)
        self.assertIn(f"pid {os.getpid()}", status.stdout)

    def test_remove_of_an_unknown_url_names_created_and_discovered_vehicles(self) -> None:
        other = self.enterContext(fake_metrics_ui())
        other_http = chase_operator_url(other.url)
        self.automa("vehicles", "discover", "--provider", "chase-sim", "--url", other_http)

        missing = self.automa("vehicles", "remove", "--url", "http://localhost:1")

        self.assertEqual((missing.returncode, missing.stdout), (2,
            "No listed vehicle at http://localhost:1. "
            f"Listed: Simulator (chase-sim) at {self.http}, Simulator (chase-sim) at {other_http}.\n"))

    def test_remove_drops_a_discovered_duplicate_of_the_created_simulator(self) -> None:
        (self.runtime_root / "discovered.json").write_text(json.dumps({
            "schema": "automa_discovered_vehicles",
            "vehicles": [{"provider": "chase-sim", "url": self.server.url}],
        }), encoding="utf-8")

        removed = self.automa("vehicles", "remove", "--url", self.http)

        self.assertEqual((removed.returncode, removed.stdout), (0, f"Removed Simulator (chase-sim) at {self.http}\n"))
        self.assertEqual(self.automa("vehicles", "status").stdout, "None listed.\n")
        self.assertEqual(self.discovered(), [])

    def test_a_discovered_entry_at_an_unlisted_simulator_is_already_listed(self) -> None:
        self.automa("vehicles", "remove", "--url", self.http)
        (self.runtime_root / "discovered.json").write_text(json.dumps({
            "schema": "automa_discovered_vehicles",
            "vehicles": [{"provider": "chase-sim", "url": self.server.url}],
        }), encoding="utf-8")

        result = self.automa("vehicles", "discover", "--provider", "chase-sim", "--url", self.http)

        self.assertEqual((result.returncode, result.stdout),
                         (0, f"Already listed: Simulator (chase-sim) at {self.http}\n"))
        self.assertFalse(self.listed())


if __name__ == "__main__":
    unittest.main(verbosity=2)
