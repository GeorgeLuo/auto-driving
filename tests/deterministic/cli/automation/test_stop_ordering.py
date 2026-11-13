"""A stop is not delayed by unrelated work, and it is the run that remains.

Barriers hold the other work. The assertions are the order of those barriers
and the run record after both sides finish.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch
from urllib.request import Request, urlopen

from cli.automa_cli import viewer_commands
from cli.automa_cli.frontend.server import RuntimeViewServer
from cli.automa_cli.hosts.runs import service
from cli.automa_cli.hosts.runs.render import render_lifecycle_result
from cli.automa_cli.providers.models import Candidate, ProbeResult
from cli.automa_cli.vehicles import discovery
from cli.automa_cli.vehicles.discovery import discover_vehicle, remove_vehicle
from cli.automa_cli.vehicles.store import discovered_vehicles, write_discovered_vehicles
from cli.automa_cli.viewer_commands import VIEWER_READS, VIEWER_WRITES
from tests.shared.cli.workbench_fixtures import ImageReplayRunner
from tests.support.runtime_fixtures import runtime_root_env


class StopOrderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(service, "run_directory", return_value=self.root))
        self.enterContext(patch.object(service, "staged_host", return_value={"provider": "chase-sim"}))
        self.enterContext(patch.object(service, "bundle_paths", return_value={}))
        self.enterContext(patch.object(service, "bundle_activation_problems", return_value=[]))
        self.enterContext(patch.object(service, "running_base_url", return_value=None))
        self.enterContext(patch.object(service, "pid_alive", return_value=True))
        self.enterContext(patch.object(service, "pid_matches_automation", return_value=True))
        self.enterContext(patch.object(service, "stop_worker", return_value="stopped"))
        self.held = threading.Event()
        self.release = threading.Event()

    def wait(self, **_kwargs: object) -> dict[str, object]:
        self.held.set()
        self.release.wait()
        return {"status": "ready", "processed_count": 0, "state": {}}

    def test_stop_during_start_is_the_run_that_remains(self) -> None:
        client = mock.Mock()
        self.enterContext(patch.object(service, "running_base_url", return_value="http://127.0.0.1:9"))
        self.enterContext(patch.object(service, "RuntimeClient", return_value=client))
        self.enterContext(patch.object(service, "launch_worker", return_value=SimpleNamespace(pid=424242)))
        self.enterContext(patch.object(service, "wait_for_automation_startup", side_effect=self.wait))
        started = self._run(service.start_host_automation_background, host_name="car", take_control=False)

        self.held.wait()
        stopped = service.stop_host_automation(host_name="car")
        self.assertFalse(self.release.is_set())
        client.stop.assert_called_once_with()
        self.release.set()
        result = started()

        self.assertEqual((stopped.exit_code, stopped.kind), (0, "stopped"))
        self.assertEqual((result.exit_code, result.kind), (2, "stopped"))
        self.assertIn("stopped before it was ready", render_lifecycle_result(result))
        self.assertEqual(self._record("state.json")["status"], "stopped")
        self.assertEqual(self._record("process.json")["stopped_by"], "stop_command")

    def test_a_failed_stop_does_not_cancel_the_start(self) -> None:
        self.enterContext(patch.object(service, "staged_host", return_value={"provider": "picar"}))
        self.enterContext(patch.object(service, "running_base_url", return_value="http://127.0.0.1:9"))
        client = mock.Mock()
        client.stop.side_effect = RuntimeError("offline")
        self.enterContext(patch.object(service, "RuntimeClient", return_value=client))
        self.enterContext(patch.object(service, "launch_worker", return_value=SimpleNamespace(pid=424242)))
        self.enterContext(patch.object(service, "wait_for_automation_startup", side_effect=self.wait))
        started = self._run(service.start_host_automation_background, host_name="car", take_control=False)

        self.held.wait()
        stopped = service.stop_host_automation(host_name="car")
        self.assertFalse(self.release.is_set())
        self.release.set()
        result = started()

        self.assertEqual((stopped.exit_code, stopped.kind, stopped.error), (2, "remote_failed", "offline"))
        self.assertEqual((result.exit_code, result.kind), (0, "started"))
        self.assertNotIn("stopped_by", self._record("process.json"))

    def test_a_stop_that_times_out_lets_the_start_finish(self) -> None:
        self.enterContext(patch.object(service, "stop_worker", return_value="timeout"))
        self.enterContext(patch.object(service, "launch_worker", return_value=SimpleNamespace(pid=424242)))
        self.enterContext(patch.object(service, "wait_for_automation_startup", side_effect=self.wait))
        started = self._run(service.start_host_automation_background, host_name="car", take_control=False)

        self.held.wait()
        stopped = service.stop_host_automation(host_name="car")
        self.release.set()
        result = started()

        self.assertEqual((stopped.exit_code, stopped.kind), (2, "timeout"))
        self.assertEqual(result.kind, "started")
        self.assertNotIn("stopped_by", self._record("process.json"))
        self.assertEqual(self._record("order.json")["stops"], 0)

    def test_stop_during_restart_is_the_run_that_remains(self) -> None:
        self.enterContext(patch.object(service, "launch_worker", return_value=SimpleNamespace(pid=424242)))
        self.enterContext(patch.object(service, "wait_for_automation_startup", side_effect=self.wait))
        restarted = self._run(service.restart_host_automation, host_name="car", take_control=False)

        self.held.wait()
        stopped = service.stop_host_automation(host_name="car")
        self.assertFalse(self.release.is_set())
        self.release.set()
        result = restarted()

        self.assertEqual(stopped.kind, "stopped")
        self.assertEqual((result.exit_code, result.kind), (2, "start_failed"))
        self.assertEqual(result.start.kind, "stopped")
        self.assertEqual(self._record("state.json")["status"], "stopped")
        self.assertEqual(self._record("process.json")["stopped_by"], "stop_command")

    def _run(self, call, **kwargs):
        box: dict[str, object] = {}

        def run() -> None:
            try:
                box["result"] = call(**kwargs)
            except Exception as exc:  # The test thread must not leave the barrier unset.
                box["error"] = exc
                self.held.set()

        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(self._finish, thread)
        def finish() -> object:
            thread.join()
            if "error" in box:
                raise box["error"]
            return box["result"]

        return finish

    def _finish(self, thread: threading.Thread) -> None:
        self.release.set()
        thread.join()

    def _record(self, name: str) -> dict[str, object]:
        return json.loads((self.root / name).read_text())


class ViewerStopTests(unittest.TestCase):
    def test_stop_proceeds_while_a_write_is_held(self) -> None:
        cases = (("car", "other"), ("car", "car"))
        for armed, stopped in cases:
            with self.subTest(armed=armed, stopped=stopped):
                self._stop_while_arm_is_held(armed, stopped)

    def _stop_while_arm_is_held(self, armed: str, stopped: str) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(runtime_root_env(root))
        held, release = threading.Event(), threading.Event()

        def block(*_args: object, **_kwargs: object) -> None:
            held.set()
            release.wait()
            raise RuntimeError("released")

        server = RuntimeViewServer(
            host_name="viewer", automation_dir=None, port=0, connected=False,
            reads=VIEWER_READS, writes=VIEWER_WRITES, replay_runner_factory=ImageReplayRunner,
        ).start()
        self.addCleanup(server.stop)
        self.addCleanup(release.set)
        with (patch.object(service, "running_base_url", return_value=None),
              patch.object(viewer_commands, "arm_plugins", side_effect=block)):
            def arm() -> None:
                try:
                    self._post(server.url, "/api/plugins/arm", {
                        "host_name": armed, "name": ["perception/classical_regions"],
                    })
                except Exception:
                    return

            arming = threading.Thread(target=arm)
            arming.start()
            self.addCleanup(arming.join)
            held.wait()
            code, body = self._post(server.url, "/api/hosts/stop", {"name": stopped})
        self.assertFalse(release.is_set())
        self.assertEqual(code, 200)
        self.assertEqual(body["kind"], "no_pid")
        release.set()

    def _post(self, url: str, route: str, body: dict[str, object]) -> tuple[int, dict[str, object]]:
        request = Request(
            f"{url}{route.lstrip('/')}", data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=5.0) as response:
            return response.status, json.loads(response.read())


class RegistryTests(unittest.TestCase):
    def test_a_discover_keeps_vehicles_recorded_while_it_probes(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(runtime_root_env(root))
        slow, fast, removed = (f"ws://car-{name}/ws/control" for name in ("slow", "fast", "old"))
        write_discovered_vehicles([{"provider": "chase-sim", "url": removed}])
        entered, release = threading.Event(), threading.Event()

        def probe(candidate: Candidate, timeout_s: float) -> ProbeResult:
            del timeout_s
            if candidate.url == slow:
                entered.set()
                release.wait()
            return ProbeResult(active=True, candidate=candidate)

        provider = SimpleNamespace(
            name="chase-sim", normalize_url=lambda url: url, probe=probe, can_take_host=lambda result: True,
        )

        def record_while_the_probe_is_held() -> None:
            entered.wait()
            self.assertEqual(remove_vehicle(url=removed).kind, "removed")
            self.assertEqual(discover_vehicle(provider="chase-sim", url=fast).kind, "discovered")
            release.set()

        other = threading.Thread(target=record_while_the_probe_is_held)
        other.start()
        self.addCleanup(other.join)
        self.addCleanup(release.set)
        self.addCleanup(entered.set)
        with patch.dict(discovery.PROVIDERS, {"chase-sim": provider}):
            discovered = discover_vehicle(provider="chase-sim", url=slow)
        other.join()
        self.assertEqual(discovered.kind, "discovered")
        self.assertEqual(
            {vehicle["url"] for vehicle in discovered_vehicles()},
            {slow, fast},
        )


if __name__ == "__main__":
    unittest.main()
