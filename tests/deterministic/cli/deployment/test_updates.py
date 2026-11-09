from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from cli.automa_cli.hosts.runs.models import StopResult
from cli.automa_cli.hosts.deployment import service
from cli.automa_cli.hosts.deployment.models import CoreResult, CreateResult, UpdateResult
from cli.automa_cli.hosts.deployment.registry import BACKENDS, DeploymentBackend, register_backend
from cli.automa_cli.hosts.deployment.render import render_create, render_delete, render_update
from cli.automa_cli.hosts.step_activations import write_host_configuration
from cli.automa_cli.providers.registry import PICAR, PROVIDERS
from cli.automa_cli.providers.models import Candidate, ProbeResult
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.vehicle_fixtures import probe_returning
from tests.support.runtime_fixtures import write_host_fixture, runtime_root_env


class HostLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "vehicles"
        self.enterContext(runtime_root_env(self.root))

    def test_create_picar_dry_run_has_core_release_then_restart_without_writes(self):
        result = invoke_automa("hosts", "create", "--name", "car", "--provider", "picar",
                               "--url", "http://example.local:8887", "--dry-run", "--drive-args=--js", "--json",
                               runtime_root=self.root, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "automa_host_create")
        self.assertTrue(payload["dry_run"])
        self.assertIsNone(payload["host_state"])
        self.assertEqual(payload["core"]["schema"], "host_core_update")
        self.assertEqual(payload["release"]["schema"], "host_autonomy_update")
        self.assertEqual(payload["configuration"]["provider"], "picar")
        self.assertEqual(payload["configuration"]["connection"]["ssh_target"], "piracer@example.local")
        commands = payload["core"]["commands"] + payload["release"]["commands"]
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
        with patch.object(service, "install_picar_core", return_value=CoreResult(2, "error", "ssh failed")):
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
        self.assertFalse(json.loads(render_update(result, json_output=True))["restart_requested"])
        self.assertTrue(render_update(result, json_output=False).startswith("Autonomy update dry run"))

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
        self.assertEqual(render_delete(result), "Could not stop the PiCar host; its local record was kept.")
        self.assertTrue((self.root / "car" / "host-configuration.json").exists())
        argv = run.call_args.args[1]
        self.assertEqual(argv[:3], ["ssh", "-o", "ConnectTimeout=10"])
        self.assertIn("control.sh /home/piracer stop", argv[-1])
        with patch.object(service, "run_step", return_value=0):
            result = service.delete_host(host_name="car")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Harness files stay in /home/piracer/mycar on the PiCar.", render_delete(result))
        self.assertFalse(result.remote_stop_failed)
        self.assertFalse((self.root / "car").exists())

    def test_delete_picar_force_removes_the_record_when_the_pi_cannot_be_stopped(self):
        write_host_fixture(self.root, "car", provider="picar")
        with patch.object(service, "run_step", return_value=255) as run:
            failed = invoke_automa(
                "hosts", "delete", "--name", "car", "--force", runtime_root=self.root, check=False,
            )
        self.assertEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        self.assertEqual(run.call_args.args[1][:3], ["ssh", "-o", "ConnectTimeout=10"])
        self.assertIn("Could not stop the PiCar host.", failed.stdout)
        self.assertIn("The Donkey service may still be running on the Pi.", failed.stdout)
        self.assertIn("Harness files stay in /home/piracer/mycar on the PiCar.", failed.stdout)
        self.assertFalse((self.root / "car").exists())

        write_host_fixture(self.root, "car", provider="picar")
        with patch.object(service, "run_step", return_value=0):
            stopped = invoke_automa(
                "hosts", "delete", "--name", "car", "--force", runtime_root=self.root, check=False,
            )
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assertIn("Stopped host car.", stopped.stdout)
        self.assertNotIn("may still be running", stopped.stdout)
        self.assertFalse((self.root / "car").exists())

    def test_created_picar_receipt_carries_the_synced_core(self):
        core = CoreResult(0, "synced", receipt={"schema": "host_core_update", "dry_run": False})
        release = UpdateResult(0, "remote", receipt={"schema": "host_autonomy_update"})
        with patch.object(service, "install_picar_core", return_value=core), \
             patch.object(service, "update_host", return_value=release):
            result = service.create_host(host_name="car", provider="picar", url="http://example:8887")
        body = json.loads(render_create(result, json_output=True))
        self.assertEqual((body["dry_run"], body["core"], body["release"]), (False, core.receipt, release.receipt))

    def test_failed_new_creation_removes_the_directory(self):
        with patch.object(service, "install_picar_core", return_value=CoreResult(2, "error", "sync failed")):
            result = service.create_host(host_name="car", provider="picar", url="http://example:8887")
        self.assertEqual(result.exit_code, 2)
        self.assertFalse((self.root / "car").exists())

    def test_delete_aborts_when_an_active_run_cannot_stop(self):
        write_host_fixture(self.root, "car", provider="picar")
        with patch.object(service, "host_state", return_value={"state": "running"}), \
             patch("cli.automa_cli.hosts.deployment.service.stop_host_automation",
                   return_value=StopResult(2, "release_failed", "car", error="could not release control")), \
             patch.object(service, "run_step") as remote:
            for force in (False, True):
                with self.subTest(force=force):
                    result = service.delete_host(host_name="car", force=force)
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
        rendered = render_update(UpdateResult(0, "local", receipt=receipt), json_output=False)
        self.assertEqual(
            rendered,
            "Dry run: would package car's controller release (tree abcdef123456, 3 files)\n"
            "Restart the host onto it: no",
        )
        self.assertEqual(json.loads(render_update(UpdateResult(0, "local", receipt=receipt), json_output=True))["source"]["file_count"], 3)
        self.assertEqual(render_update(UpdateResult(2, "error", "missing"), json_output=True), "missing")

    def test_create_chase_dry_run_prints_the_create_receipt_with_json(self):
        result = invoke_automa(
            "hosts", "create", "--name", "car", "--provider", "chase-sim",
            "--url", "http://localhost:5050", "--dry-run", "--json",
            runtime_root=self.root, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "automa_host_create")
        self.assertTrue(payload["dry_run"])
        self.assertIsNone(payload["core"])
        self.assertIsNone(payload["host_state"])
        self.assertEqual(payload["configuration"]["host_name"], "car")
        self.assertTrue(payload["release"]["dry_run"])
        self.assertEqual(payload["release"]["host_name"], "car")
        self.assertIn("tree_sha256", payload["release"]["source"])
        plain = invoke_automa(
            "hosts", "create", "--name", "car", "--provider", "chase-sim",
            "--url", "http://localhost:5050", "--dry-run",
            runtime_root=self.root, check=False,
        )
        self.assertEqual(plain.returncode, 0, plain.stdout + plain.stderr)
        self.assertTrue(plain.stdout.startswith("Dry run: would package car's controller release"), plain.stdout)
        self.assertFalse(self.root.exists())

    def test_created_receipt_has_the_create_schema(self):
        configuration = {"host_name": "car", "provider": "chase-sim", "vehicle_kind": "chase-sim-ws",
                         "connection": {"ws_url": "ws://localhost:5050/ws/control"}}
        release = UpdateResult(0, "local", host_present=False, receipt={
            "host_name": "car", "dry_run": False, "restarted": False, "generation_id": "g-1",
            "release": {"archive": "a.tar.gz", "archive_sha256": "abc"},
            "steps": {"perception": ["frame"]}, "presets": {},
        })
        created = CreateResult(0, "created", configuration=configuration, release=release,
                               host_state={"state": "idle", "url": "http://127.0.0.1:9"})
        body = json.loads(render_create(created, json_output=True))
        self.assertEqual(
            list(body),
            ["configuration", "core", "dry_run", "host_state", "release", "schema"],
        )
        self.assertEqual(body["schema"], "automa_host_create")
        self.assertFalse(body["dry_run"])
        self.assertIsNone(body["core"])
        self.assertEqual(list(body["configuration"]), sorted(configuration))
        self.assertEqual(list(body["release"]), sorted(release.receipt))
        self.assertEqual(body["release"], release.receipt)
        self.assertEqual(list(body["host_state"]), ["state", "url"])
        # The release lines appear once, without the update's own heading and restart line.
        self.assertEqual(render_create(created, json_output=False).splitlines(), [
            "Host car created for the chaser at http://localhost:5050/",
            "Release: a.tar.gz",
            "Archive SHA-256: abc",
            "Perception: frame",
            "Decision generation: g-1",
            "Host car  idle",
            "  chaser at http://localhost:5050/",
            "  attached",
            "  http://127.0.0.1:9",
        ])
        self.assertEqual(render_create(CreateResult(2, "error", "no simulator"), json_output=True), "no simulator")

    def test_a_third_provider_is_selected_by_registration(self):
        probe = Mock(side_effect=AssertionError("bench probe"))
        bench = replace(
            PICAR, name="bench", heading="Bench", machine="Bench", label="bench",
            vehicle_kind="bench", local_host=False, probe=probe,
        )

        def update_bench(configuration, *, dry_run, restart, drive_args, verbose, emit):
            return UpdateResult(0, "receipt", receipt={
                "schema": "bench_update", "host_name": configuration["host_name"], "dry_run": dry_run,
            })

        register_backend("bench", DeploymentBackend(create=None, update=update_bench, delete=None))
        self.addCleanup(BACKENDS.pop, "bench", None)
        write_host_configuration({
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
            self.assertEqual(json.loads(render_update(updated, json_output=False)), updated.receipt)
            self.assertTrue(updated.receipt["dry_run"])
            for flags in ([], ["--json"]):
                command = invoke_automa("hosts", "update", "--name", "car", "--dry-run", *flags,
                                        runtime_root=self.root, check=False)
                self.assertEqual(command.returncode, 0, command.stderr)
                self.assertEqual(json.loads(command.stdout), updated.receipt)
            self.assertEqual(render_update(updated, json_output=True).count("bench_update"), 1)
            removed = service.delete_host(host_name="car")
            self.assertEqual(removed.message, "Provider 'bench' does not support hosts delete.")
            self.assertEqual(removed.exit_code, 2)
            self.assertTrue((self.root / "car" / "host-configuration.json").exists())
            created = service.create_host(host_name="other", provider="bench", url="http://bench.example")
            self.assertEqual(created.message, "Provider 'bench' does not support hosts create.")
            self.assertEqual(created.exit_code, 2)
            self.assertFalse((self.root / "other").exists())
        probe.assert_not_called()

    def test_simulators_is_not_a_host_name(self):
        result = service.create_host(host_name="simulators", provider="chase-sim", url="http://localhost:5050")
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.message, "'simulators' is reserved for created simulators' records; choose another host name.")
        self.assertFalse(self.root.exists())
        self.assertEqual(service.delete_host(host_name="simulators").message, result.message)

    def test_host_name_cannot_escape_runtime_root(self):
        for name in ("..", ".", "../car", "car/name"):
            with self.subTest(name=name):
                result = service.create_host(host_name=name, provider="picar", url="http://example:8887")
                self.assertEqual(result.exit_code, 2)
        self.assertFalse(self.root.exists())
