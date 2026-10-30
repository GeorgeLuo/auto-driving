"""``hosts status``: each host's state and attachment, and with ``--long`` its run."""

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
from unittest import mock

from autonomy.decision_cycle.activation import step_activation
from cli.automa_cli.releases.paths import controller_bundle_paths
from cli.automa_cli.hosts.local import HOST_RECORD
from cli.automa_cli.providers.probes import chase_operator_url
from implementations.decision_cycle.catalog import preset_activation
from implementations.runtime.chase_sim.service import HOST_RECORD_SCHEMA, write_host_record
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWsClient
from implementations.vehicle.chase_sim.presence import MetricsUiPresence
from tests.shared.cli.automation_pipeline.pipeline_fixtures import chase_runtime
from tests.shared.cli.invoke import invoke_automa
from tests.shared.implementations.runtime.wait import wait_until
from tests.support.fake_hosts import answering, closed_url, hung_url
from tests.support.fake_metrics_ui import fake_metrics_ui
from tests.support.runtime_fixtures import write_host_fixture


def _host_blocks(stdout: str) -> list[list[str]]:
    """Each host's block of ``hosts status``."""

    return [block.splitlines() for block in stdout.strip("\n").split("\n\n")]


def _record(runtime_root: Path, host_name: str, url: str, simulator: str | None = None) -> None:
    """A running host record for ``host_name`` at ``url``, held by this process."""

    write_host_fixture(runtime_root, host_name, url=simulator)
    bundle = controller_bundle_paths(runtime_root / host_name)
    write_host_record(Path(bundle["runtime_dir"]) / "automation" / HOST_RECORD, {
        "schema": HOST_RECORD_SCHEMA, "host_name": host_name, "pid": os.getpid(), "base_url": url,
    })


def _stage_picar(runtime_root: Path, host_name: str, url: str) -> None:
    """A PiCar's staged perception step, which records its connection at ``url``."""

    write_host_fixture(runtime_root, host_name, provider="picar", url=url)
    bundle = controller_bundle_paths(runtime_root / host_name)
    packaged = preset_activation("perception", "lightweight_observer")
    activation = step_activation(
        "perception", packaged.plugins, packaged.plugin_specs, packaged.plugin_configs,
        metadata={**packaged.metadata, "host_name": host_name, "provider": "picar",
                  "runtime": {"connection": {"base_url": url}}},
    )
    path = Path(bundle["perception_runtime_dir"]) / "active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(activation.to_payload()), encoding="utf-8")


@contextmanager
def _attached(server, host_name: str, url: str) -> Iterator[None]:
    """``host_name``'s host at ``url``, listed by ``server`` as an attached agent."""

    client = {"kind": "host", "host_name": host_name, "url": url, "pid": os.getpid()}
    with MetricsUiPresence(server.url, client, retry_s=0.05):
        wait_until(
            lambda: any(agent.get("client") == client for agent in MetricsUiWsClient(server.url).list_agents()),
            message=f"{host_name}'s host never attached",
        )
        yield


class HostStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.server = self.enterContext(fake_metrics_ui())
        self.chaser = f"  chaser at {chase_operator_url(self.server.url)}"

    def status(self, *args: str) -> subprocess.CompletedProcess[str]:
        return invoke_automa("hosts", "status", *args, runtime_root=self.runtime_root,
                             extra_env={"CHASE_UI_WS_URL": self.server.url}, check=False)

    def host(self):
        """The chaser's in-process host, created against this simulator."""

        write_host_fixture(self.runtime_root, "chase-sim-chaser", url=self.server.url)
        return self.enterContext(chase_runtime(self.runtime_root))

    def test_an_attached_host_shows_its_state_vehicle_and_attachment(self) -> None:
        host = self.host()
        self.enterContext(_attached(self.server, "chase-sim-chaser", host.base_url))

        # The run lines use the host's one /host/status read.
        with mock.patch("cli.automa_cli.hosts.runs.status.host_state", side_effect=AssertionError("read twice")):
            human = self.status()
            long = self.status("--long")
            machine = json.loads(self.status("--json").stdout)

        self.assertEqual((human.returncode, long.returncode), (0, 0), human.stderr + long.stderr)
        [block] = _host_blocks(human.stdout)
        self.assertEqual(block[:3], ["Host chase-sim-chaser  not started", self.chaser, "  0 decisions, 0 errors"])
        self.assertRegex(block[3], r"^  attached since \d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertEqual(block[4:], [f"  {host.base_url}, pid {os.getpid()}"])
        # --long adds the run lines under the same block.
        self.assertTrue(long.stdout.startswith("\n".join(block[:3]) + "\n"), long.stdout)
        self.assertIn(f"\n  {host.base_url}, pid {os.getpid()}\n  deployment: ", long.stdout)
        self.assertEqual((machine["schema"], machine["host_name"]), ("automa_hosts_status", None))
        [entry] = machine["hosts"]
        self.assertEqual(
            {key: entry[key] for key in ("host_name", "provider", "vehicle_url", "attached", "local", "state", "mode",
                                         "decision_count")},
            {"host_name": "chase-sim-chaser", "provider": "chase-sim", "vehicle_url": chase_operator_url(self.server.url),
             "attached": True, "local": "host record", "state": "pre_start", "mode": "manual", "decision_count": 0},
        )
        self.assertEqual(entry["run"]["host_name"], "chase-sim-chaser")

    def test_a_host_with_only_a_local_record_is_not_attached(self) -> None:
        host = self.host()

        result = self.status()

        self.assertEqual(_host_blocks(result.stdout), [[
            "Host chase-sim-chaser  not started",
            self.chaser,
            "  0 decisions, 0 errors",
            "  not attached to its simulator (local host record)",
            f"  {host.base_url}, pid {os.getpid()}",
        ]])

    def test_a_created_host_without_a_process_is_not_running_and_fails_its_id(self) -> None:
        write_host_fixture(self.runtime_root, "free-name", url=self.server.url)

        targeted = self.status("--name", "free-name")
        every = self.status()
        [entry] = json.loads(self.status("--json").stdout)["hosts"]

        self.assertEqual(targeted.returncode, 1, targeted.stderr)
        self.assertEqual(every.returncode, 0, every.stderr)
        self.assertEqual(_host_blocks(targeted.stdout), [[
            "Host free-name  not running", self.chaser, "  not attached to its simulator (local host record)",
        ]])
        self.assertEqual((entry["url"], entry["attached"]), (None, False))

    def test_an_unreadable_host_configuration_does_not_hide_the_others(self) -> None:
        self.host()
        path = self.runtime_root / "old-host" / "host-configuration.json"
        path.parent.mkdir()
        path.write_text(json.dumps({
            "host_name": "old-host", "provider": "chase-sim",
            "connection": {"ws_url": self.server.url},
        }), encoding="utf-8")

        every = self.status()
        targeted = self.status("--name", "old-host")
        entry = json.loads(self.status("--name", "old-host", "--json").stdout)["hosts"][0]

        self.assertEqual(every.returncode, 0, every.stderr)
        self.assertEqual([block[0] for block in _host_blocks(every.stdout)], [
            "Host chase-sim-chaser  not started", "Host old-host  error",
        ])
        self.assertEqual(targeted.returncode, 1, targeted.stderr)
        self.assertEqual(targeted.stdout.splitlines(), [
            "Host old-host  error", "  Cannot read host 'old-host': invalid host record",
        ])
        self.assertEqual((entry["state"], entry["run"]), ("error", None))

    def test_a_host_that_never_answers_does_not_hold_up_the_others(self) -> None:
        hung = self.enterContext(hung_url())
        _record(self.runtime_root, "chase-sim-follower", hung, self.server.url)
        host = self.host()
        self.enterContext(_attached(self.server, "chase-sim-chaser", host.base_url))

        started = time.monotonic()
        result = self.status("--timeout-s", "1")
        elapsed = time.monotonic() - started

        self.assertEqual(result.returncode, 0, result.stderr)
        chaser, follower = _host_blocks(result.stdout)
        self.assertEqual(chaser[0], "Host chase-sim-chaser  not started")
        self.assertEqual(follower, [
            "Host chase-sim-follower  not answering",
            self.chaser,
            "  no answer within 1.0s",
            "  not attached to its simulator (local host record)",
            f"  {hung}, pid {os.getpid()}",
        ])
        self.assertLess(elapsed, 2.0)

    def test_hosts_on_one_simulator_list_its_agents_once(self) -> None:
        for host_name in ("chase-sim-chaser", "chase-sim-follower"):
            _record(self.runtime_root, host_name, closed_url(), self.server.url)

        with mock.patch.object(MetricsUiWsClient, "list_agents", autospec=True,
                               side_effect=MetricsUiWsClient.list_agents) as list_agents:
            result = self.status()

        self.assertEqual(list_agents.call_count, 1)
        self.assertEqual([block[0] for block in _host_blocks(result.stdout)], [
            "Host chase-sim-chaser  not answering", "Host chase-sim-follower  not answering",
        ])

    def test_an_id_whose_host_does_not_answer_exits_one(self) -> None:
        url = closed_url()
        _record(self.runtime_root, "chase-sim-chaser", url, self.server.url)

        result = self.status("--name", "chase-sim-chaser")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(_host_blocks(result.stdout), [[
            "Host chase-sim-chaser  not answering",
            self.chaser,
            "  Connection refused",
            "  not attached to its simulator (local host record)",
            f"  {url}, pid {os.getpid()}",
        ]])

    def test_a_host_session_error_shows_on_its_host_and_fails_its_id(self) -> None:
        body = json.dumps({
            "ok": True, "host_name": "chase-sim-chaser", "mode": "autonomy",
            "session": {"status": "error", "processed_decisions": 3},
            "host": {"error_count": 2, "last_error": "Chase passiveCaptureError: session moved"},
        }).encode()
        url = self.enterContext(answering(body))
        _record(self.runtime_root, "chase-sim-chaser", url, self.server.url)
        self.enterContext(_attached(self.server, "chase-sim-chaser", url))

        targeted = self.status("--name", "chase-sim-chaser")
        every = self.status()

        self.assertEqual(targeted.returncode, 1, targeted.stderr)
        self.assertEqual(every.returncode, 0, every.stderr)
        [host] = _host_blocks(targeted.stdout)
        self.assertEqual(host[:4], [
            "Host chase-sim-chaser  error, autonomy",
            self.chaser,
            "  3 decisions, 2 errors",
            "  Chase passiveCaptureError: session moved",
        ])
        self.assertTrue(host[4].startswith("  attached since "), host)

    def test_a_host_shows_its_run_state_without_the_idle_mode(self) -> None:
        for session, mode, heading, state in (
            ("stopped", "manual", "finished run", "finished_run"),
            ("completed", "manual", "finished run", "finished_run"),
            ("running", "observe_only", "running, observe only", "running"),
        ):
            body = json.dumps({
                "ok": True, "host_name": "chase-sim-chaser", "mode": mode,
                "session": {"status": session, "processed_decisions": 1}, "host": {"error_count": 0},
            }).encode()
            with self.subTest(session=session), answering(body) as url:
                _record(self.runtime_root, "chase-sim-chaser", url, self.server.url)

                result = self.status("--name", "chase-sim-chaser")
                machine = json.loads(self.status("--name", "chase-sim-chaser", "--json").stdout)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(_host_blocks(result.stdout)[0][:3], [
                    f"Host chase-sim-chaser  {heading}", self.chaser, "  1 decision, 0 errors",
                ])
                self.assertEqual(machine["hosts"][0]["state"], state)

    def test_a_simulator_that_cannot_list_agents_leaves_attachment_unknown(self) -> None:
        self.server.refuse("list_agents", "unsupported command")
        host = self.host()

        result = self.status()
        machine = json.loads(self.status("--json").stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        [block] = _host_blocks(result.stdout)
        self.assertEqual(block[-2:], ["  local host record", f"  {host.base_url}, pid {os.getpid()}"])
        self.assertIsNone(machine["hosts"][0]["attached"])

    def test_a_staged_picar_is_listed_from_its_connection(self) -> None:
        url = closed_url()
        _stage_picar(self.runtime_root, "piracer", url)

        result = self.status()
        [entry] = json.loads(self.status("--json").stdout)["hosts"]

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_host_blocks(result.stdout), [[
            "Host piracer  not answering", f"  PiCar at {url}", "  Connection refused", "  staged connection",
            f"  {url}",
        ]])
        self.assertEqual((entry["provider"], entry["vehicle_url"], entry["attached"]), ("picar", url, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
