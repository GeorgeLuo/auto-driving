from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from autonomy.decision_cycle.activation import step_activation
from cli.automa_cli.bundles import controller_bundle_paths
from cli.automa_cli.host_publications import host_state
from cli.automa_cli.runtime_hosts import HOST_RECORD
from cli.automa_cli.vehicles import chase_operator_url
from implementations.decision_cycle.catalog import preset_activation
from implementations.runtime.chase_sim.service import HOST_RECORD_SCHEMA, write_host_record
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWsClient
from implementations.vehicle.chase_sim.presence import MetricsUiPresence
from tests.shared.cli.automation_pipeline.pipeline_fixtures import chase_runtime
from tests.shared.cli.invoke import invoke_automa
from tests.shared.implementations.runtime.wait import wait_until
from tests.support.runtime_fixtures import write_host_fixture
from tests.support.fake_hosts import answering, closed_url, hung_url
from tests.support.fake_metrics_ui import fake_metrics_ui


def _blocks(stdout: str) -> list[list[str]]:
    """Each vehicle block and each host block of ``vehicles status``, as lines."""

    return [[line for line in block.splitlines() if not line.startswith(("  chaser at", "  PiCar at"))]
            for block in stdout.strip("\n").split("\n\n")]


def _record(runtime_root: Path, host_id: str, url: str, simulator: str | None = None) -> None:
    """A running host record for ``host_id`` at ``url``, held by this process."""

    write_host_fixture(runtime_root, host_id, url=simulator)
    bundle = controller_bundle_paths(runtime_root / host_id)
    write_host_record(Path(bundle["runtime_dir"]) / "automation" / HOST_RECORD, {
        "schema": HOST_RECORD_SCHEMA, "host_id": host_id, "pid": os.getpid(), "base_url": url,
    })


def _stage_picar(runtime_root: Path, host_id: str, url: str) -> None:
    """A PiCar's staged perception step, which records its connection at ``url``."""

    write_host_fixture(runtime_root, host_id, provider="picar", url=url)
    bundle = controller_bundle_paths(runtime_root / host_id)
    packaged = preset_activation("perception", "lightweight_observer")
    activation = step_activation(
        "perception", packaged.plugins, packaged.plugin_specs, packaged.plugin_configs,
        metadata={**packaged.metadata, "host_id": host_id, "provider": "picar",
                  "runtime": {"connection": {"base_url": url}}},
    )
    path = Path(bundle["perception_runtime_dir"]) / "active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(activation.to_payload()), encoding="utf-8")


@contextmanager
def _attached(server, host_id: str, url: str) -> Iterator[None]:
    """``host_id``'s host at ``url``, listed by ``server`` as an attached agent."""

    client = {"kind": "host", "host_id": host_id, "url": url, "pid": os.getpid()}
    with MetricsUiPresence(server.url, client, retry_s=0.05):
        wait_until(
            lambda: any(agent.get("client") == client for agent in MetricsUiWsClient(server.url).list_agents()),
            message=f"{host_id}'s host never attached",
        )
        yield


class SimulatorStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.server = self.enterContext(fake_metrics_ui())
        self.env = {"CHASE_UI_WS_URL": self.server.url}

    def status(self, *args: str, chase_url: str | None = None) -> subprocess.CompletedProcess[str]:
        return invoke_automa("vehicles", "status", *args, runtime_root=self.runtime_root,
                             extra_env={"CHASE_UI_WS_URL": chase_url or self.server.url}, check=False)

    def test_vehicle_status_does_not_report_or_probe_hosts(self) -> None:
        # A hung host must not delay or appear in the vehicle environment view.
        hung = self.enterContext(hung_url())
        _record(self.runtime_root, "controller-a", hung, self.server.url)
        started = time.monotonic()
        result = self.status("--json", "--timeout-s", "1")
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "automa_vehicle_environments_status_v1")
        self.assertNotIn("hosts", payload)
        self.assertEqual(payload["vehicles"][0]["url"], chase_operator_url(self.server.url))
        self.assertEqual(payload["vehicles"][0]["state"], "running")
        self.assertNotIn("Host controller-a", self.status().stdout)

    def test_host_status_reports_host_identity_and_vehicle_endpoint(self) -> None:
        write_host_fixture(self.runtime_root, "controller-a", url=self.server.url)
        result = invoke_automa("hosts", "status", "--id", "controller-a", "--json",
                               runtime_root=self.runtime_root, extra_env=self.env)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "automa_host_status_v1")
        self.assertNotIn("vehicles", payload)
        [host] = payload["hosts"]
        self.assertEqual(host["host_id"], "controller-a")
        self.assertEqual(host["provider"], "chase-sim")
        self.assertEqual(host["vehicle_url"], self.server.url)
        self.assertIsNone(host["url"])
        human = invoke_automa("hosts", "status", "--id", "controller-a",
                              runtime_root=self.runtime_root, extra_env=self.env)
        self.assertIn(f"vehicle: chase-sim at {self.server.url}", human.stdout)
        self.assertIn("host URL: not running", human.stdout)

    def test_unknown_host_is_reported_separately(self) -> None:
        result = invoke_automa("hosts", "status", "--id", "missing", "--json",
                               runtime_root=self.runtime_root, extra_env=self.env, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["outcome"]["status"], "not_found")
        self.assertEqual(self.status().returncode, 0)

    def test_internal_host_discovery_matches_the_attached_host_id(self) -> None:
        from unittest.mock import patch
        from cli.automa_cli import runtime_hosts
        from cli.automa_cli.vehicles import get_chase_simulator_status

        write_host_fixture(self.runtime_root, "chase-sim-chaser", url=self.server.url)
        host = self.enterContext(chase_runtime(self.runtime_root))
        self.enterContext(_attached(self.server, "chase-sim-chaser", host.base_url))
        with patch.object(runtime_hosts, "RUNTIME_ROOT", self.runtime_root), \
             patch.dict(os.environ, self.env):
            status = get_chase_simulator_status()
        [entry] = status["hosts"]
        self.assertEqual((entry["host_id"], entry["attached"], entry["state"]),
                         ("chase-sim-chaser", True, "pre_start"))
        result = invoke_automa("hosts", "status", "--id", "chase-sim-chaser", "--json",
                               runtime_root=self.runtime_root, extra_env=self.env)
        [reported] = json.loads(result.stdout)["hosts"]
        self.assertTrue(reported["attached"])
        self.assertIsNotNone(reported["attached_since"])

    def test_unreachable_or_frontend_disconnected_simulator(self) -> None:
        unavailable = self.status(chase_url=closed_url())
        self.assertEqual(unavailable.returncode, 1)
        self.assertIn("unreachable", unavailable.stdout)
        self.server.refuse("get_state", "Frontend not connected", code="frontend_not_connected")
        disconnected = self.status()
        self.assertEqual(disconnected.returncode, 0)
        self.assertIn("frontend not connected", disconnected.stdout)


class HostStateTests(unittest.TestCase):
    def test_a_host_that_answers_reports_its_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory, chase_runtime(Path(directory)) as host:
            state = host_state(host.base_url, host_id="chase-sim-chaser", timeout_s=2.0)
            other = host_state(host.base_url, host_id="chase-sim-follower", timeout_s=2.0)

        self.assertEqual(
            {key: state[key] for key in ("state", "mode", "decision_count", "error_count", "error")},
            {"state": "pre_start", "mode": "manual", "decision_count": 0, "error_count": 0, "error": None},
        )
        self.assertEqual(state["reply"]["host_id"], "chase-sim-chaser")
        self.assertEqual((other["state"], other["error"]), ("error", f"{host.base_url} answers as chase-sim-chaser"))

    def test_a_host_that_does_not_answer_says_why_in_plain_words(self) -> None:
        with hung_url() as hung, answering(None) as dropping:
            for url, error in (
                (closed_url(), "Connection refused"),
                (hung, "no answer within 0.5s"),
                (dropping, "Remote end closed connection without response"),
            ):
                with self.subTest(error=error):
                    state = host_state(url, timeout_s=0.5)

                    self.assertEqual((state["state"], state["error"], state["reply"]), ("not answering", error, None))

    def test_a_reply_that_is_not_a_host_status_is_an_error(self) -> None:
        no_host = "{url} answers but reports no runtime host"
        for body, status, error in (
            (b"[]", 200, no_host),
            (b"not json", 200, no_host),
            (b'{"ok": true}', 200, no_host),
            (b'{"ok": false, "error": "status failed"}', 500, "status failed"),
            (b'{"ok": false}', 503, "HTTP 503"),
        ):
            with self.subTest(body=body), answering(body, status) as url:
                state = host_state(url, timeout_s=2.0)

                self.assertEqual((state["state"], state["error"]), ("error", error.format(url=url)))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class EnvironmentStatusTests(unittest.TestCase):
    def test_created_host_without_a_process_is_listed(self):
        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui() as simulator:
            root = Path(tmp)
            write_host_fixture(root, "free-name", url=simulator.url)
            env = {"CHASE_UI_WS_URL": simulator.url}
            result = invoke_automa("hosts", "status", "--json", runtime_root=root, extra_env=env)
            [host] = json.loads(result.stdout)["hosts"]
            self.assertIsNone(host["url"])
            self.assertEqual(host["provider"], "chase-sim")
            self.assertEqual(host["vehicle_url"], simulator.url)

    def test_picar_states_and_picar_only_exit(self):
        from unittest.mock import patch
        from cli.automa_cli.vehicles import Candidate, ProbeResult
        url = "http://example:8887"
        for active, diagnostics, expected, code in (
            (True, {}, "answering", 0),
            (False, {"runtime_state": "server_not_listening"}, "reachable, no host listening", 0),
            (False, {"runtime_state": "endpoint_unreachable"}, "unreachable", 1),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                probe = ProbeResult(active=active, candidate=Candidate("picar", url, "discovered"),
                                    diagnostics=diagnostics, error=None if active else "probe detail")
                env = {"CHASE_UI_WS_URL": closed_url()}
                with patch("cli.automa_cli.vehicles._probe_picar", return_value=probe):
                    invoke_automa("vehicles", "discover", "--provider", "picar", "--url", url, "--force",
                                  runtime_root=Path(tmp), extra_env=env)
                    result = invoke_automa("vehicles", "status", "--json", runtime_root=Path(tmp),
                                           extra_env=env, check=False)
                    text = invoke_automa("vehicles", "status", runtime_root=Path(tmp), extra_env=env, check=False)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(json.loads(result.stdout)["vehicles"][1],
                                 {"provider": "picar", "url": url, "state": expected, "error": probe.error})
                self.assertIn(f"PiCar (picar)  {expected} at {url}", text.stdout)
