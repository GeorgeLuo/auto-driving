from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from cli.automa_cli.shared.command_result import CommandResult
from cli.automa_cli.hosts.deployment import picar_deploy, service
from cli.automa_cli.hosts.deployment.models import DeploymentResult
from cli.automa_cli.hosts.deployment.registry import BACKENDS, DeploymentBackend, register_backend
from cli.automa_cli.hosts.deployment.render import present_deployment
from cli.automa_cli.hosts.step_activations import write_host_configuration
from cli.automa_cli.hosts import staging
from cli.automa_cli.providers.catalog import PICAR, PROVIDERS
from cli.automa_cli.providers.probes import Candidate, ProbeResult
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.vehicle_fixtures import probe_returning
from tests.support.runtime_fixtures import write_host_fixture


class HostLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "vehicles"
        self.enterContext(patch.object(service, "RUNTIME_ROOT", self.root))
        self.enterContext(patch.object(picar_deploy, "RUNTIME_ROOT", self.root))
        self.enterContext(patch.object(staging, "RUNTIME_ROOT", self.root))
        self.enterContext(patch("cli.automa_cli.releases.paths.RUNTIME_ROOT", self.root))

    def test_create_picar_dry_run_has_core_release_then_restart_without_writes(self):
        result = invoke_automa("hosts", "create", "--name", "car", "--provider", "picar",
                               "--url", "http://example.local:8887", "--dry-run", "--drive-args=--js", "--json",
                               runtime_root=self.root, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["configuration"]["provider"], "picar")
        self.assertEqual(payload["configuration"]["connection"]["ssh_target"], "piracer@example.local")
        commands = payload["core"]["commands"] + payload["host"]["commands"]
        self.assertEqual(commands[0]["step"], "Prepare remote directories")
        self.assertEqual(commands[-1]["step"], "Restart Donkey runtime service")
        self.assertIn("b64:LS1qcw==", commands[-1]["command"])
        self.assertFalse(self.root.exists())

    def test_create_chase_packages_starts_and_records_a_free_name(self):
        probe = ProbeResult(active=True, candidate=Candidate("chase-sim", "ws://localhost:5050/ws/control", "cli"))
        with probe_returning(probe), \
             patch.object(service, "ensure_chase_host", return_value="http://localhost:9999") as start, \
             patch.object(service, "host_state", return_value={"state": "idle"}):
            result = service.create_host(host_name="test", provider="chase-sim", url="http://localhost:5050")
        self.assertEqual(result.exit_code, 0, result.message)
        start.assert_called_once_with("test")
        record = json.loads((self.root / "test" / "host-configuration.json").read_text())
        self.assertEqual((record["schema"], record["host_name"]), ("automa_host_configuration", "test"))
        self.assertNotIn("vehicle_id", record)
        self.assertEqual(record["connection"]["ws_url"], "ws://localhost:5050/ws/control")
        self.assertTrue((self.root / "test" / "bundle" / "releases" / "latest-controller-bundle.json").exists())
        again = service.create_host(host_name="test", provider="chase-sim", url="http://localhost:5050")
        self.assertEqual(again.exit_code, 2)
        self.assertIn("already exists: the chaser", again.message)

    def test_unreachable_simulator_leaves_no_directory(self):
        probe = ProbeResult(active=False, candidate=Candidate("chase-sim", "ws://example/ws/control", "cli"), error="no frontend")
        with probe_returning(probe):
            result = service.create_host(host_name="test", provider="chase-sim", url="http://example")
        self.assertEqual(result.exit_code, 2)
        self.assertIn("no frontend", result.message)
        self.assertFalse(self.root.exists())

    def test_creation_failure_preserves_adopted_steps_but_removes_identity(self):
        directory = self.root / "test"
        directory.mkdir(parents=True)
        staged = directory / "keep.json"
        staged.write_text("{}")
        with patch.object(service, "install_picar_core", return_value=CommandResult(2, "ssh failed")):
            result = service.create_host(host_name="test", provider="picar", url="http://example:8887")
        self.assertEqual(result.exit_code, 2)
        self.assertTrue(staged.exists())
        self.assertFalse((directory / "host-configuration.json").exists())
        self.assertIn("Pi keeps", result.message)

    def test_unknown_host_commands_do_not_write(self):
        write_host_fixture(self.root, "test")
        for words in (("update",), ("run",)):
            result = invoke_automa("hosts", *words, "--name", "unknown", runtime_root=self.root, check=False)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("No host 'unknown'. Hosts: test.", result.stdout)
            self.assertFalse((self.root / "unknown").exists())

    def test_update_uses_record_and_requires_restart_for_drive_args(self):
        write_host_fixture(self.root, "car", provider="picar")
        result = service.update_host(host_name="car", drive_args="--js", dry_run=True)
        self.assertEqual(result.exit_code, 2)
        self.assertIn("requires --restart", result.message)
        result = service.update_host(host_name="car", dry_run=True)
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.receipt["restart_requested"])
        self.assertFalse(json.loads(present_deployment(result, json_output=True))["restart_requested"])
        self.assertTrue(present_deployment(result, json_output=False).startswith("Autonomy update dry run"))

    def test_delete_chase_stops_host_and_removes_directory(self):
        write_host_fixture(self.root, "test")
        with patch.object(service, "stop_chase_host") as stop:
            result = service.delete_host(host_name="test")
        self.assertEqual(result.exit_code, 0)
        stop.assert_called_once_with("test")
        self.assertFalse((self.root / "test").exists())

    def test_delete_picar_keeps_record_on_ssh_failure_then_removes_on_success(self):
        write_host_fixture(self.root, "car", provider="picar")
        with patch.object(service, "run_step", return_value=1) as run:
            result = service.delete_host(host_name="car")
        self.assertEqual(result.exit_code, 1)
        self.assertTrue((self.root / "car" / "host-configuration.json").exists())
        self.assertIn("control.sh /home/piracer stop", run.call_args.args[1][-1])
        with patch.object(service, "run_step", return_value=0):
            result = service.delete_host(host_name="car")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("~/mycar", result.message)
        self.assertFalse((self.root / "car").exists())

    def test_failed_new_creation_removes_the_directory(self):
        with patch.object(service, "install_picar_core", return_value=CommandResult(2, "sync failed")):
            result = service.create_host(host_name="car", provider="picar", url="http://example:8887")
        self.assertEqual(result.exit_code, 2)
        self.assertFalse((self.root / "car").exists())

    def test_delete_aborts_when_an_active_run_cannot_stop(self):
        write_host_fixture(self.root, "car", provider="picar")
        with patch.object(service, "host_state", return_value={"state": "running"}), \
             patch("cli.automa_cli.hosts.deployment.service.stop_host_automation",
                   return_value=CommandResult(2, "could not release control")), \
             patch.object(service, "run_step") as remote:
            result = service.delete_host(host_name="car")
        self.assertEqual(result.exit_code, 2)
        self.assertTrue((self.root / "car" / "host-configuration.json").exists())
        remote.assert_not_called()

    def test_renderer_formats_a_supplied_receipt(self):
        receipt = {
            "host_name": "car",
            "dry_run": True,
            "restart": False,
            "source": {"tree_sha256": "abcdef1234567890", "file_count": 3},
        }
        rendered = present_deployment(DeploymentResult(0, receipt=receipt, render_kind="local_update"), json_output=False)
        self.assertEqual(
            rendered,
            "Dry run: would package car's controller release (tree abcdef123456, 3 files)\n"
            "Restart the host onto it: no",
        )
        self.assertEqual(json.loads(present_deployment(DeploymentResult(0, receipt=receipt, render_kind="local_update"), json_output=True))["source"]["file_count"], 3)
        self.assertEqual(present_deployment(DeploymentResult(2, "missing"), json_output=True), "missing")

    def test_a_third_provider_is_selected_by_registration(self):
        probe = Mock(side_effect=AssertionError("bench probe"))
        bench = replace(
            PICAR, name="bench", heading="Bench", machine="Bench", label="bench",
            vehicle_kind="bench", local_host=False, probe=probe,
        )

        def update_bench(configuration, *, dry_run, restart, drive_args, verbose, output):
            return DeploymentResult(0, receipt={
                "schema": "bench_update", "host_name": configuration["host_name"], "dry_run": dry_run,
            })

        register_backend("bench", DeploymentBackend(create=None, update=update_bench, delete=None))
        self.addCleanup(BACKENDS.pop, "bench", None)
        write_host_configuration(self.root, {
            "host_name": "car", "provider": "bench", "vehicle_kind": "bench",
            "connection": {"endpoint": "bench://local"},
        })
        with patch.dict(PROVIDERS, {"bench": bench}), \
             patch.object(service, "install_picar_core", side_effect=AssertionError("picar install")), \
             patch.object(service, "run_step", side_effect=AssertionError("remote step")), \
             patch.object(service, "stop_chase_host", side_effect=AssertionError("chase stop")):
            updated = service.update_host(host_name="car", dry_run=True)
            self.assertEqual(updated.exit_code, 0, updated.message)
            self.assertEqual(updated.receipt["schema"], "bench_update")
            self.assertEqual(json.loads(present_deployment(updated, json_output=False)), updated.receipt)
            self.assertTrue(updated.receipt["dry_run"])
            for flags in ([], ["--json"]):
                command = invoke_automa("hosts", "update", "--name", "car", "--dry-run", *flags,
                                        runtime_root=self.root, check=False)
                self.assertEqual(command.returncode, 0, command.stderr)
                self.assertEqual(json.loads(command.stdout), updated.receipt)
            self.assertEqual(present_deployment(updated, json_output=True).count("bench_update"), 1)
            removed = service.delete_host(host_name="car")
            self.assertEqual(removed.message, "Provider 'bench' does not support hosts delete.")
            self.assertEqual(removed.exit_code, 2)
            self.assertTrue((self.root / "car" / "host-configuration.json").exists())
            created = service.create_host(host_name="other", provider="bench", url="http://bench.example")
            self.assertEqual(created.message, "Provider 'bench' does not support hosts create.")
            self.assertEqual(created.exit_code, 2)
            self.assertFalse((self.root / "other").exists())
        probe.assert_not_called()

    def test_host_name_cannot_escape_runtime_root(self):
        for name in ("..", ".", "../car", "car/name"):
            with self.subTest(name=name):
                result = service.create_host(host_name=name, provider="picar", url="http://example:8887")
                self.assertEqual(result.exit_code, 2)
        self.assertFalse(self.root.exists())
