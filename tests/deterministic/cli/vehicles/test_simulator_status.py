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
from cli.automa_cli.vehicles import chase_operator_url, normalize_chase_url
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
    """The simulator block and each host block of ``vehicles status``, as lines."""

    return [[line for line in block.splitlines() if not line.startswith(("  chaser at", "  PiCar at"))]
            for block in stdout.strip("\n").split("\n\n") if not block.startswith("PiCar (picar)  ")]


def _record(runtime_root: Path, vehicle_id: str, url: str) -> None:
    """A running host record for ``vehicle_id`` at ``url``, held by this process."""

    write_host_fixture(runtime_root, vehicle_id)
    bundle = controller_bundle_paths(runtime_root / vehicle_id)
    write_host_record(Path(bundle["runtime_dir"]) / "automation" / HOST_RECORD, {
        "schema": HOST_RECORD_SCHEMA, "vehicle_id": vehicle_id, "pid": os.getpid(), "base_url": url,
    })


def _stage_picar(runtime_root: Path, vehicle_id: str, url: str) -> None:
    """A PiCar's staged perception step, which records its connection at ``url``."""

    write_host_fixture(runtime_root, vehicle_id, provider="picar", url=url)
    bundle = controller_bundle_paths(runtime_root / vehicle_id)
    packaged = preset_activation("perception", "lightweight_observer")
    activation = step_activation(
        "perception", packaged.plugins, packaged.plugin_specs, packaged.plugin_configs,
        metadata={**packaged.metadata, "vehicle_id": vehicle_id, "provider": "picar",
                  "runtime": {"connection": {"base_url": url}}},
    )
    path = Path(bundle["perception_runtime_dir"]) / "active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(activation.to_payload()), encoding="utf-8")


@contextmanager
def _attached(server, vehicle_id: str, url: str) -> Iterator[None]:
    """``vehicle_id``'s host at ``url``, listed by ``server`` as an attached agent."""

    client = {"kind": "vehicle-host", "vehicle_id": vehicle_id, "url": url, "pid": os.getpid()}
    with MetricsUiPresence(server.url, client, retry_s=0.05):
        wait_until(
            lambda: any(agent.get("client") == client for agent in MetricsUiWsClient(server.url).list_agents()),
            message=f"{vehicle_id}'s host never attached",
        )
        yield


class SimulatorStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.server = self.enterContext(fake_metrics_ui())
        self.simulator = [f"Simulator (chase-sim)  running at {chase_operator_url(self.server.url)}",
                          "  frontend connected, game chase"]

    def status(self, *args: str, chase_url: str | None = None) -> subprocess.CompletedProcess[str]:
        target = ("--chase-url", chase_url) if chase_url else ("--chase-ws-url", self.server.url)
        return invoke_automa("vehicles", "status", *target, *args,
                             runtime_root=self.runtime_root, check=False)

    def test_status_shows_the_simulator_and_an_attached_host(self) -> None:
        host = self.enterContext(chase_runtime(self.runtime_root))
        self.enterContext(_attached(self.server, "chase-sim-chaser", host.base_url))

        human = self.status()
        machine = json.loads(self.status("--json").stdout)

        self.assertEqual(human.returncode, 0, human.stderr)
        simulator, attached = _blocks(human.stdout)
        self.assertEqual(simulator, self.simulator)
        self.assertEqual(attached[:2], ["Host chase-sim-chaser  not started", "  0 decisions, 0 errors"])
        self.assertRegex(attached[2], r"^  attached since \d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertEqual(attached[3:], [f"  {host.base_url}, pid {os.getpid()}"])
        self.assertEqual(machine["schema"], "automa_simulator_status_v1")
        self.assertEqual((machine["state"], machine["frontend"], machine["game"]), ("running", "connected", "chase"))
        [entry] = machine["hosts"]
        self.assertEqual(
            {key: entry[key] for key in ("vehicle_id", "attached", "local", "state", "mode", "decision_count")},
            {"vehicle_id": "chase-sim-chaser", "attached": True, "local": "host record",
             "state": "pre_start", "mode": "manual", "decision_count": 0},
        )

    def test_a_host_with_only_a_local_record_is_not_attached(self) -> None:
        host = self.enterContext(chase_runtime(self.runtime_root))

        result = self.status()

        self.assertEqual(_blocks(result.stdout)[1], [
            "Host chase-sim-chaser  not started",
            "  0 decisions, 0 errors",
            "  not attached to this simulator (local host record)",
            f"  {host.base_url}, pid {os.getpid()}",
        ])

    def test_a_host_that_never_answers_does_not_hold_up_the_others(self) -> None:
        hung = self.enterContext(hung_url())
        _record(self.runtime_root, "chase-sim-follower", hung)
        host = self.enterContext(chase_runtime(self.runtime_root))
        self.enterContext(_attached(self.server, "chase-sim-chaser", host.base_url))

        started = time.monotonic()
        result = self.status("--timeout-s", "1")
        elapsed = time.monotonic() - started

        self.assertEqual(result.returncode, 0, result.stderr)
        _simulator, chaser, follower = _blocks(result.stdout)
        self.assertEqual(chaser[0], "Host chase-sim-chaser  not started")
        self.assertEqual(follower, [
            "Host chase-sim-follower  not answering",
            "  no answer within 1.0s",
            "  not attached to this simulator (local host record)",
            f"  {hung}, pid {os.getpid()}",
        ])
        self.assertLess(elapsed, 2.0)

    def test_an_unknown_id_is_not_found_and_exits_one(self) -> None:
        result = self.status("--id", "chase-sim-typo")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(_blocks(result.stdout), [self.simulator, ["Host chase-sim-typo  not found"]])

    def test_an_id_whose_host_does_not_answer_exits_one(self) -> None:
        url = closed_url()
        _record(self.runtime_root, "chase-sim-chaser", url)

        result = self.status("--id", "chase-sim-chaser")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(_blocks(result.stdout)[1], [
            "Host chase-sim-chaser  not answering",
            "  Connection refused",
            "  not attached to this simulator (local host record)",
            f"  {url}, pid {os.getpid()}",
        ])

    def test_a_host_session_error_shows_on_its_host_and_fails_its_id(self) -> None:
        body = json.dumps({
            "ok": True, "vehicle_id": "chase-sim-chaser", "mode": "autonomy",
            "session": {"status": "error", "processed_decisions": 3},
            "host": {"error_count": 2, "last_error": "Chase passiveCaptureError: session moved"},
        }).encode()
        url = self.enterContext(answering(body))
        _record(self.runtime_root, "chase-sim-chaser", url)
        self.enterContext(_attached(self.server, "chase-sim-chaser", url))

        targeted = self.status("--id", "chase-sim-chaser")
        every = self.status()

        self.assertEqual(targeted.returncode, 1, targeted.stderr)
        self.assertEqual(every.returncode, 0, every.stderr)
        host = _blocks(targeted.stdout)[1]
        self.assertEqual(host[:3], [
            "Host chase-sim-chaser  error, autonomy",
            "  3 decisions, 2 errors",
            "  Chase passiveCaptureError: session moved",
        ])
        self.assertTrue(host[3].startswith("  attached since "), host)

    def test_a_host_shows_its_run_state_without_the_idle_mode(self) -> None:
        for session, mode, heading, state in (
            ("stopped", "manual", "finished run", "finished_run"),
            ("completed", "manual", "finished run", "finished_run"),
            ("running", "observe_only", "running, observe only", "running"),
        ):
            body = json.dumps({
                "ok": True, "vehicle_id": "chase-sim-chaser", "mode": mode,
                "session": {"status": session, "processed_decisions": 1}, "host": {"error_count": 0},
            }).encode()
            with self.subTest(session=session), answering(body) as url:
                _record(self.runtime_root, "chase-sim-chaser", url)

                result = self.status("--id", "chase-sim-chaser")
                machine = json.loads(self.status("--id", "chase-sim-chaser", "--json").stdout)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(_blocks(result.stdout)[1][:2], [
                    f"Host chase-sim-chaser  {heading}", "  1 decision, 0 errors",
                ])
                self.assertEqual(machine["hosts"][0]["state"], state)

    def test_a_staged_picar_is_listed_from_its_connection(self) -> None:
        url = closed_url()
        _stage_picar(self.runtime_root, "piracer", url)

        result = self.status()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_blocks(result.stdout)[1], [
            "Host piracer  not answering", "  Connection refused", "  staged connection", f"  {url}",
        ])

    def test_an_unreachable_simulator_says_why_and_still_lists_local_hosts(self) -> None:
        simulator, url = closed_url(), closed_url()
        _record(self.runtime_root, "chase-sim-chaser", url)

        result = self.status(chase_url=simulator)

        self.assertEqual(result.returncode, 1, result.stderr)
        [heading, reason], host = _blocks(result.stdout)
        self.assertEqual(heading, f"Simulator (chase-sim)  unreachable at {chase_operator_url(normalize_chase_url(simulator))}")
        self.assertIn("refused", reason)
        self.assertEqual(host, [
            "Host chase-sim-chaser  not answering",
            "  Connection refused",
            "  local host record",
            f"  {url}, pid {os.getpid()}",
        ])
        self.assertNotIn("None", result.stdout)

    def test_a_simulator_without_a_browser_runs_with_no_frontend(self) -> None:
        for query in ("get_state", "get_play_debug"):
            with self.subTest(query=query), fake_metrics_ui() as server:
                server.refuse(query, "Frontend not connected", code="frontend_not_connected")

                result = invoke_automa("vehicles", "status", "--chase-ws-url", server.url,
                                       runtime_root=self.runtime_root, check=False)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(_blocks(result.stdout), [
                    [f"Simulator (chase-sim)  running at {chase_operator_url(server.url)}", "  frontend not connected"],
                    ["No hosts found"],
                ])

    def test_a_simulator_that_cannot_list_hosts_says_why(self) -> None:
        self.server.refuse("list_agents", "unsupported command")
        host = self.enterContext(chase_runtime(self.runtime_root))

        result = self.status()
        machine = json.loads(self.status("--json").stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        simulator, listed = _blocks(result.stdout)
        self.assertEqual(simulator, [*self.simulator, "  cannot list attached hosts: unsupported command"])
        self.assertEqual(listed[-2:], ["  local host record", f"  {host.base_url}, pid {os.getpid()}"])
        self.assertIsNone(machine["hosts"][0]["attached"])

    def test_no_hosts_found(self) -> None:
        result = self.status()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_blocks(result.stdout), [self.simulator, ["No hosts found"]])


class HostStateTests(unittest.TestCase):
    def test_a_host_that_answers_reports_its_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory, chase_runtime(Path(directory)) as host:
            state = host_state(host.base_url, vehicle_id="chase-sim-chaser", timeout_s=2.0)
            other = host_state(host.base_url, vehicle_id="chase-sim-follower", timeout_s=2.0)

        self.assertEqual(
            {key: state[key] for key in ("state", "mode", "decision_count", "error_count", "error")},
            {"state": "pre_start", "mode": "manual", "decision_count": 0, "error_count": 0, "error": None},
        )
        self.assertEqual(state["reply"]["vehicle_id"], "chase-sim-chaser")
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
            result = invoke_automa("vehicles", "status", "--chase-url", simulator.url,
                                   "--json", runtime_root=root)
            [host] = json.loads(result.stdout)["hosts"]
            self.assertEqual(host["state"], "not running")
            self.assertEqual(host["provider"], "chase-sim")
            self.assertEqual(host["vehicle_url"], simulator.url)
            failed = invoke_automa("vehicles", "status", "--id", "free-name", "--chase-url", simulator.url,
                                   runtime_root=root, check=False)
            self.assertEqual(failed.returncode, 1)
            self.assertIn("chaser at", failed.stdout)

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
                probe = ProbeResult(active=active, candidate=Candidate("picar", url, "cli"),
                                    diagnostics=diagnostics, error=None if active else "probe detail")
                with patch("cli.automa_cli.vehicles._probe_picar", return_value=probe):
                    result = invoke_automa("vehicles", "status", "--chase-url", closed_url(),
                                           "--picar-url", url, "--json", runtime_root=Path(tmp), check=False)
                    text = invoke_automa("vehicles", "status", "--chase-url", closed_url(),
                                         "--picar-url", url, runtime_root=Path(tmp), check=False)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(json.loads(result.stdout)["picar"],
                                 {"url": url, "state": expected, "error": probe.error})
                self.assertIn(f"PiCar (picar)  {expected} at {url}", text.stdout)
