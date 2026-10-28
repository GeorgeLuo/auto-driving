from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from cli.automa_cli.hosts.host_publications import host_state
from cli.automa_cli.vehicles.probes import chase_operator_url
from tests.deterministic.cli.vehicles.test_host_status import _record
from tests.shared.cli.automation_pipeline.pipeline_fixtures import chase_runtime
from tests.shared.cli.invoke import invoke_automa
from tests.support.fake_hosts import answering, closed_url, hung_url
from tests.support.fake_metrics_ui import fake_metrics_ui


class SimulatorStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.server = self.enterContext(fake_metrics_ui())

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
        self.assertEqual(payload, {"schema": "automa_vehicles_status", "vehicles": [{
            "provider": "chase-sim", "url": chase_operator_url(self.server.url), "state": "running",
            "error": None, "frontend": "connected", "game": "chase", "pid": None,
        }]})
        self.assertEqual(self.status().stdout, (
            f"Simulator (chase-sim)  running at {chase_operator_url(self.server.url)}\n"
            "  frontend connected, game chase\n"
        ))

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
            state = host_state(host.base_url, host_name="chase-sim-chaser", timeout_s=2.0)
            other = host_state(host.base_url, host_name="chase-sim-follower", timeout_s=2.0)

        self.assertEqual(
            {key: state[key] for key in ("state", "mode", "decision_count", "error_count", "error")},
            {"state": "pre_start", "mode": "manual", "decision_count": 0, "error_count": 0, "error": None},
        )
        self.assertEqual(state["reply"]["host_name"], "chase-sim-chaser")
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


class EnvironmentStatusTests(unittest.TestCase):
    def test_picar_states_and_picar_only_exit(self):
        from unittest.mock import patch
        from cli.automa_cli.vehicles.probes import Candidate, ProbeResult
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
                with patch("cli.automa_cli.vehicles.probes._probe_picar", return_value=probe):
                    invoke_automa("vehicles", "discover", "--provider", "picar", "--url", url, "--force",
                                  runtime_root=Path(tmp), extra_env=env)
                    result = invoke_automa("vehicles", "status", "--json", runtime_root=Path(tmp),
                                           extra_env=env, check=False)
                    text = invoke_automa("vehicles", "status", runtime_root=Path(tmp), extra_env=env, check=False)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(json.loads(result.stdout)["vehicles"][1],
                                 {"provider": "picar", "url": url, "state": expected, "error": probe.error})
                self.assertIn(f"PiCar (picar)  {expected} at {url}", text.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
