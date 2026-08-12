from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from coverage import CoverageData


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "docs"
    / "milestones"
    / "007-cli-operator-usability"
    / "tools"
    / "cli-journey-coverage"
)
LAUNCHER = TOOL / "coverage_session"
SESSION_MODULE = TOOL / "coverage_session.py"
REPORT_MODULE = TOOL / "coverage_report.py"
RUNNER_MODULE = TOOL.parent / "live-cli-session-runner" / "session_runner.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


session = _load("test_m007_cli_coverage_session", SESSION_MODULE)
report = session.reporting
runner = _load("test_m007_cli_coverage_runner", RUNNER_MODULE)


def _clean_environment(**updates: str) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COVERAGE_")
    }
    environment.update(updates)
    return environment


def _write_arc_shard(
    path: Path,
    *,
    context: str,
    filename: str = "cli/automa_cli/app.py",
    arcs: tuple[tuple[int, int], ...] = ((-1, 1), (1, -1)),
) -> None:
    data = CoverageData(basename=str(path))
    data.set_context(context)
    data.add_arcs({filename: arcs})
    data.write()


def _synthetic_pass_report() -> dict[str, object]:
    collection_id = "7" * 32
    bootstrap = "m007/bootstrap/root-help"
    offline_ids = [
        "m007/journey/continuity.offline_perception/offline-capture/cmd-00",
        "m007/journey/continuity.offline_perception/offline-apply-a/cmd-00",
        "m007/journey/continuity.offline_perception/offline-apply-b/cmd-00",
    ]
    worker_id = "m007/journey/primary/automation-run/cmd-00"
    lineage = {
        "schema": "m007_cli_coverage_offline_lineage_v1",
        "catalog_id": "m007-continuity",
        "source_identity": "$REPO/runtime/offline/source",
        "manifest_sha256": "a" * 64,
        "ordered_input_sha256": "b" * 64,
        "frame_count": 1,
        "frame_receipt_sha256": "c" * 64,
    }
    commands: list[dict[str, object]] = [
        {
            "catalog_id": "_collector",
            "role": "bootstrap",
            "step_id": "_bootstrap",
            "command_ordinal": 0,
            "collection_id": collection_id,
            "logical_context_id": bootstrap,
            "family_id": None,
            "argv_template": ["./cli/automa", "--help"],
            "resolved_argv": ["./cli/automa", "--help"],
            "normalized_working_directory": "$REPO",
            "expected_exit": 0,
            "observed_exit": 0,
            "expects_background_worker": False,
            "measurement_context": f"m007-run/{collection_id}/{bootstrap}",
        }
    ]
    for index, logical in enumerate(offline_ids):
        step_id = ("offline-capture", "offline-apply-a", "offline-apply-b")[index]
        commands.append(
            {
                "catalog_id": "m007-continuity",
                "role": "journey_command",
                "step_id": step_id,
                "command_ordinal": 0,
                "collection_id": collection_id,
                "logical_context_id": logical,
                "family_id": "continuity.offline_perception",
                "argv_template": ["./cli/automa", "offline", step_id],
                "resolved_argv": ["./cli/automa", "offline", step_id],
                "normalized_working_directory": "$REPO",
                "expected_exit": 0,
                "observed_exit": 0,
                "expects_background_worker": False,
                "measurement_context": f"m007-run/{collection_id}/{logical}",
                "offline_source_lineage": {
                    **lineage,
                    "relation": "produced" if index == 0 else "consumed",
                },
            }
        )
    foreground = "5" * 64
    worker = "6" * 64
    commands.append(
        {
            "catalog_id": "m007-acceptance",
            "role": "journey_command",
            "step_id": "automation-run",
            "command_ordinal": 0,
            "collection_id": collection_id,
            "logical_context_id": worker_id,
            "family_id": None,
            "argv_template": ["./cli/automa", "vehicles", "automation", "run"],
            "resolved_argv": ["./cli/automa", "vehicles", "automation", "run"],
            "normalized_working_directory": "$REPO",
            "expected_exit": 0,
            "observed_exit": 0,
            "expects_background_worker": True,
            "measurement_context": f"m007-run/{collection_id}/{worker_id}",
            "new_shard_sha256_visible_at_return": [foreground],
        }
    )
    shard_pairs = [
        (bootstrap, "1" * 64),
        (offline_ids[0], "2" * 64),
        (offline_ids[1], "3" * 64),
        (offline_ids[2], "4" * 64),
        (worker_id, foreground),
        (worker_id, worker),
    ]
    shards = [
        {
            "shard_id": f"shard-{index:03d}-{digest[:16]}",
            "shard_sha256": digest,
            "logical_context_id": logical,
            "measurement_context": f"m007-run/{collection_id}/{logical}",
            "readable": True,
            "branch_arcs": True,
            "measured_sources": ["cli/automa_cli/app.py"],
        }
        for index, (logical, digest) in enumerate(shard_pairs)
    ]
    app_contexts = []
    for line, logical in enumerate([bootstrap, *offline_ids], start=1):
        app_contexts.append(
            {
                "logical_context_id": logical,
                "measurement_context": f"m007-run/{collection_id}/{logical}",
                "executed_lines": [line],
                "executed_arcs": [[-1, line], [line, -1]],
            }
        )
    worker_line, _ = report.function_body_range(
        ROOT / "cli/automa_cli/automation.py", "run_vehicle_automation"
    )
    files = [
        {"path": "cli/automa_cli/app.py", "contexts": app_contexts},
        {
            "path": "cli/automa_cli/automation.py",
            "contexts": [
                {
                    "logical_context_id": worker_id,
                    "measurement_context": f"m007-run/{collection_id}/{worker_id}",
                    "executed_lines": [worker_line],
                    "executed_arcs": [[-1, worker_line], [worker_line, -1]],
                }
            ],
        },
    ]
    worker_command = {
        "catalog_id": "m007-acceptance",
        "role": "journey_command",
        "step_id": "automation-run",
        "command_ordinal": 0,
    }
    worker_measurement = f"m007-run/{collection_id}/{worker_id}"
    lifecycle = {
        "schema": "m007_cli_coverage_worker_lifecycle_v1",
        "launch": {
            "command": worker_command,
            "logical_context_id": worker_id,
            "measurement_context": worker_measurement,
            "pid": 123,
            "run_id": "generation-1",
            "generation_matches": True,
            "stdout_pid_matches": True,
            "pid_alive": True,
            "raw_shard_sha256_visible": [
                "1" * 64,
                "2" * 64,
                "3" * 64,
                "4" * 64,
                foreground,
            ],
        },
        "observations": [
            {
                "kind": "terminal_status",
                "pid": 123,
                "run_id": "generation-1",
                "generation_matches": True,
                "launch_command": worker_command,
                "logical_context_id": worker_id,
                "measurement_context": worker_measurement,
                "same_generation": True,
                "pid_alive": False,
                "status": "stopped",
                "raw_shard_sha256_visible": [
                    digest for _logical, digest in shard_pairs
                ],
            }
        ],
    }
    cleanup = {
        "all_workers_stopped": True,
        "catalogs": [
            {
                "catalog_id": catalog,
                "worker_stopped": True,
                "pid_alive": False,
            }
            for catalog in ("m007-acceptance", "m007-continuity")
        ],
    }
    runner_results = [
        {
            "catalog_id": "m007-acceptance",
            "result": "incomplete",
            "behavioral_verdict": "not_evaluated",
            "machine_preflight_verdict": "pass",
            "cleanup": {"worker_stopped": True, "pid_alive": False},
            "worker_lifecycles": [lifecycle],
        },
        {
            "catalog_id": "m007-continuity",
            "result": "incomplete",
            "behavioral_verdict": "not_evaluated",
            "machine_preflight_verdict": "pass",
            "cleanup": {"worker_stopped": True, "pid_alive": False},
            "worker_lifecycles": [],
        },
    ]
    execution = report._report_execution(files)
    worker_probe = {
        "path": "cli/automa_cli/automation.py",
        "function": "run_vehicle_automation",
    }
    worker_checks = report.validate_worker_execution(
        commands=commands,
        shards=shards,
        execution=execution,
        repo_root=ROOT,
        worker_probe=worker_probe,
        worker_lifecycles=[lifecycle],
    )
    all_true_checks = {
        "manifest_complete": True,
        "all_command_exits_expected": True,
        "all_executed_contexts_have_shards": True,
        "background_workers_complete": True,
        "offline_replay_lineage_complete": True,
        "runner_machine_preflight": True,
        "cleanup": True,
        "dependency_environment_unchanged": True,
        "relevant_source_unchanged": True,
        "metrics_ui_identity_unchanged": True,
        "repository_coverage_unchanged": True,
        "measured_config_probe": True,
    }
    collection_checks = {
        "result": "pass",
        "checks": all_true_checks,
        "reasons": {
            "missing_required_commands": [],
            "unexpected_command_exits": [],
            "missing_foreground_contexts": [],
            "incomplete_background_contexts": [],
            "missing_offline_source_lineage": [],
            "failed_machine_preflight_catalogs": [],
        },
    }
    logical_ids = sorted(str(command["logical_context_id"]) for command in commands)
    counts = {
        logical: sum(item[0] == logical for item in shard_pairs)
        for logical in logical_ids
    }
    contexts = {
        "collection_id": collection_id,
        "expected_logical_contexts": logical_ids,
        "observed_logical_contexts": logical_ids,
        "measurement_to_logical": [
            {
                "logical_context_id": logical,
                "measurement_context": f"m007-run/{collection_id}/{logical}",
                "shard_count": counts[logical],
            }
            for logical in logical_ids
        ],
        "empty_contexts": [],
        "foreign_contexts": [],
        "unknown_contexts": [],
    }
    roles = (
        "bootstrap",
        "journey_command",
        "supplemental_capture",
        "precondition",
        "cleanup",
    )
    aggregates = {
        "command_roles": {
            role: len([command for command in commands if command.get("role") == role])
            for role in roles
        },
        "commands": len(commands),
        "contexts": len(logical_ids),
        "shards": len(shards),
        "files": len(files),
        "executed_line_entries": sum(
            len(context["executed_lines"])
            for file_record in files
            for context in file_record["contexts"]
        ),
        "executed_arc_entries": sum(
            len(context["executed_arcs"])
            for file_record in files
            for context in file_record["contexts"]
        ),
        "rollups": report.aggregate_rollups(execution, commands),
        "numeric_gate": False,
    }
    payload: dict[str, object] = {
        "schema": report.REPORT_SCHEMA,
        "result": "pass",
        "reason_codes": [],
        "timestamps": {
            "collection_started_at_utc": "2026-01-01T00:00:00Z",
            "collection_ended_at_utc": "2026-01-01T00:00:01Z",
            "finalized_at_utc": "2026-01-01T00:00:02Z",
        },
        "cleanup": cleanup,
        "subject": {
            "source_identity": {"commit": "d" * 40, "relevant": {}},
            "collection_id": collection_id,
        },
        "inputs": {
            "manifest": {"path": "manifest.json", "sha256": "e" * 64},
            "catalogs": [],
            "relevant_file_sha256": {},
            "owned_source_roots": ["cli/automa_cli"],
            "worker_probe": worker_probe,
            "offline_source_lineages": [
                {
                    **lineage,
                    "raw_receipt": {
                        "path": "runner/m007-continuity/offline-source-lineage.json",
                        "sha256": "f" * 64,
                    },
                }
            ],
            "metrics_ui_identity": {},
        },
        "dependency_environment": {},
        "commands": commands,
        "process_completeness": {
            "shards": shards,
            "worker_checks": worker_checks,
            "cleanup": cleanup,
            "collection_checks": collection_checks,
            "runner_results": runner_results,
        },
        "contexts": contexts,
        "files": files,
        "bootstrap_comparison": report.bootstrap_comparison(
            execution,
            bootstrap_logical_id=bootstrap,
            commands=commands,
        ),
        "aggregates": aggregates,
        "integrity": {
            "canonical_json": {
                "ensure_ascii": False,
                "allow_nan": False,
                "sort_keys": True,
                "separators": [",", ":"],
                "trailing_lf": 1,
                "digest_projection_omits": ["integrity.report_sha256"],
            },
            "session_seal_sha256": "8" * 64,
            "finalization_receipt_sha256": "9" * 64,
            "freshness": {
                "source_ok": True,
                "source_reasons": [],
                "dependency_ok": True,
            },
        },
        "non_claims": {
            "behavioral_correctness": False,
            "dead_code": False,
            "production_value": False,
            "numeric_coverage_gate": False,
        },
    }
    return report.finalize_report_digest(payload)


class ManifestAndLauncherTests(unittest.TestCase):
    def test_manifest_expands_every_command_to_unique_stable_context(self):
        expanded = session.expand_and_validate_manifest()
        commands = expanded["commands"]
        logical = [entry["logical_context_id"] for entry in commands]
        self.assertEqual(len(logical), len(set(logical)))
        self.assertGreaterEqual(len(logical), 35)
        self.assertTrue(
            all(session.LOGICAL_CONTEXT_PATTERN.fullmatch(value) for value in logical)
        )
        self.assertEqual(
            {item["id"] for item in expanded["catalogs"]},
            {"m007-acceptance", "m007-continuity"},
        )
        self.assertTrue(any(item["role"] == "bootstrap" for item in commands))
        self.assertTrue(
            any(item["role"] == "supplemental_capture" for item in commands)
        )
        self.assertTrue(any(item["expects_background_worker"] for item in commands))

    def test_same_argv_in_different_steps_has_distinct_contexts(self):
        commands = session.expand_and_validate_manifest()["commands"]
        status_commands = [
            item
            for item in commands
            if item["role"] == "journey_command"
            and item["argv_template"][:3] == ["./cli/automa", "vehicles", "status"]
        ]
        self.assertGreaterEqual(len(status_commands), 3)
        self.assertEqual(
            len(status_commands),
            len({item["logical_context_id"] for item in status_commands}),
        )

    def test_public_launcher_validates_manifest_from_clean_environment(self):
        completed = subprocess.run(
            [str(LAUNCHER), "validate-manifest"],
            cwd=ROOT,
            env=_clean_environment(),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["result"], "pass")

    def test_public_launcher_refuses_each_ambient_coverage_control_before_python(self):
        for name in (
            "COVERAGE_PROCESS_CONFIG",
            "COVERAGE_PROCESS_START",
            "COVERAGE_FILE",
            "COVERAGE_RCFILE",
            "COVERAGE_DEBUG",
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                sentinel = Path(temporary) / "outside.coverage"
                sentinel.write_bytes(b"unchanged")
                completed = subprocess.run(
                    [str(LAUNCHER), "validate-manifest"],
                    cwd=ROOT,
                    env=_clean_environment(**{name: str(sentinel)}),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertIn("inherited COVERAGE_*", completed.stderr)
                self.assertEqual(sentinel.read_bytes(), b"unchanged")

    def test_parent_subprocess_patch_environment_is_refused(self):
        completed = subprocess.run(
            [str(LAUNCHER), "validate-manifest"],
            cwd=ROOT,
            env=_clean_environment(COVERAGE_PROCESS_CONFIG='{"run:parallel":true}'),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertNotIn("commands", completed.stdout)

    def test_direct_internal_python_entrypoint_is_refused(self):
        completed = subprocess.run(
            [sys.executable, str(SESSION_MODULE), "validate-manifest"],
            cwd=ROOT,
            env=_clean_environment(),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("direct Python entrypoint is unsupported", completed.stderr)

    def test_path_shadowed_env_cannot_hide_ambient_coverage_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            shim_dir = Path(temporary) / "bin"
            shim_dir.mkdir()
            shim = shim_dir / "env"
            shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            shim.chmod(0o755)
            completed = subprocess.run(
                [str(LAUNCHER), "validate-manifest"],
                cwd=ROOT,
                env=_clean_environment(
                    COVERAGE_DEBUG="trace",
                    PATH=f"{shim_dir}:{os.environ.get('PATH', '')}",
                ),
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("inherited COVERAGE_*", completed.stderr)

    def test_old_pid_fd_path_seal_cannot_enable_direct_entry(self):
        script = "\n".join(
            [
                'exec 9<"$1"',
                "COVERAGE_SESSION_LAUNCH_PID=$$",
                "COVERAGE_SESSION_LAUNCH_FD=9",
                'COVERAGE_SESSION_LAUNCH_PATH="$1"',
                "export COVERAGE_SESSION_LAUNCH_PID COVERAGE_SESSION_LAUNCH_FD COVERAGE_SESSION_LAUNCH_PATH",
                'exec "$2" "$3" validate-manifest',
            ]
        )
        completed = subprocess.run(
            [
                "/bin/sh",
                "-c",
                script,
                "forge-seal",
                str(LAUNCHER),
                sys.executable,
                str(SESSION_MODULE),
            ],
            cwd=ROOT,
            env=_clean_environment(),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("direct Python entrypoint is unsupported", completed.stderr)

    def test_copied_import_bootstrap_cannot_bypass_ambient_refusal(self):
        bootstrap = "\n".join(
            [
                "import importlib.util, pathlib, sys",
                "path = pathlib.Path(sys.argv[1])",
                "spec = importlib.util.spec_from_file_location('copied_bootstrap', path)",
                "module = importlib.util.module_from_spec(spec)",
                "sys.modules[spec.name] = module",
                "spec.loader.exec_module(module)",
                "raise SystemExit(module.main(['validate-manifest']))",
            ]
        )
        completed = subprocess.run(
            [sys.executable, "-c", bootstrap, str(SESSION_MODULE)],
            cwd=ROOT,
            env=_clean_environment(COVERAGE_DEBUG="trace"),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("internal boundary", completed.stderr)
        self.assertNotIn('"result": "pass"', completed.stdout)


class RootAndEnvironmentTests(unittest.TestCase):
    def test_session_root_requires_nonexistent_path_and_owner_only_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "collection"
            created = session._create_session_root(root)
            self.assertEqual(created, root.resolve())
            self.assertEqual(created.stat().st_mode & 0o777, 0o700)
            for name in ("configs", "raw", "runner", "receipts"):
                self.assertTrue((created / name).is_dir())
            with self.assertRaises(report.CoverageContractError):
                session._create_session_root(root)

    def test_session_root_refuses_file_empty_directory_and_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            file_path = parent / "file"
            file_path.write_text("x", encoding="utf-8")
            empty = parent / "empty"
            empty.mkdir()
            link = parent / "link"
            link.symlink_to(empty, target_is_directory=True)
            for path in (file_path, empty, link):
                with self.subTest(path=path), self.assertRaises(
                    report.CoverageContractError
                ):
                    session._create_session_root(path)

    def test_hook_sanitizes_environment_and_generates_contained_effective_config(self):
        expanded = session.expand_and_validate_manifest()
        entry = next(
            item for item in expanded["commands"] if item["role"] == "bootstrap"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("configs", "raw"):
                (root / name).mkdir(parents=True, exist_ok=True)
            hook = session.RunnerCoverageHook(
                session_root=root,
                collection_id="a" * 32,
                expanded_manifest=expanded,
            )
            with mock.patch.dict(
                os.environ,
                {
                    "COVERAGE_FILE": "/tmp/outside",
                    "COVERAGE_PROCESS_CONFIG": "hostile",
                    "M007_KEEP": "yes",
                },
                clear=False,
            ):
                environment = hook.environment_for(
                    catalog_id=entry["catalog_id"],
                    role=entry["role"],
                    step_id=entry["step_id"],
                    command_ordinal=entry["command_ordinal"],
                    argv_template=entry["argv_template"],
                    resolved_argv=entry["argv_template"],
                    variables={},
                )
            coverage_names = sorted(
                name for name in environment if name.startswith("COVERAGE_")
            )
            self.assertEqual(coverage_names, ["COVERAGE_PROCESS_START"])
            config = Path(environment["COVERAGE_PROCESS_START"])
            self.assertTrue(config.resolve().is_relative_to(root.resolve()))
            self.assertNotIn("/tmp/outside", config.read_text(encoding="utf-8"))
            hook.command_completed(
                catalog_id=entry["catalog_id"],
                role=entry["role"],
                step_id=entry["step_id"],
                command_ordinal=entry["command_ordinal"],
                argv_template=entry["argv_template"],
                resolved_argv=entry["argv_template"],
                variables={},
                outcome=SimpleNamespace(
                    exit_code=0,
                    elapsed_ms=1,
                    started_at_utc="2026-01-01T00:00:00Z",
                    ended_at_utc="2026-01-01T00:00:01Z",
                ),
            )
            self.assertEqual(
                hook.receipts[0]["measurement_context"],
                f"m007-run/{'a' * 32}/{entry['logical_context_id']}",
            )

    def test_hook_rejects_changed_argv_and_unregistered_command(self):
        expanded = session.expand_and_validate_manifest()
        entry = next(
            item for item in expanded["commands"] if item["role"] == "bootstrap"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "configs").mkdir()
            (root / "raw").mkdir()
            hook = session.RunnerCoverageHook(
                session_root=root,
                collection_id="b" * 32,
                expanded_manifest=expanded,
            )
            with self.assertRaises(report.CoverageContractError):
                hook.environment_for(
                    catalog_id=entry["catalog_id"],
                    role=entry["role"],
                    step_id=entry["step_id"],
                    command_ordinal=entry["command_ordinal"],
                    argv_template=entry["argv_template"],
                    resolved_argv=["./cli/automa", "vehicles", "help"],
                    variables={},
                )
            with self.assertRaises(report.CoverageContractError):
                hook.environment_for(
                    catalog_id="unknown",
                    role="journey_command",
                    step_id="x",
                    command_ordinal=0,
                    argv_template=["x"],
                    resolved_argv=["x"],
                    variables={},
                )

    def test_runner_disabled_hook_path_does_not_supply_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            session_dir = Path(temporary)
            state = runner.SessionState(
                catalog={"id": "test"},
                session_dir=session_dir,
                repo_root=ROOT,
                variables={},
                execution_mode="test",
                session_id="test",
            )
            captured: dict[str, object] = {}

            def fake_run(argv, **kwargs):
                captured.update(kwargs)
                return runner.CommandOutcome(
                    argv=list(argv),
                    command="x",
                    exit_code=0,
                    elapsed_ms=1,
                    stdout_path="out",
                    stderr_path="err",
                    started_at_utc="t0",
                    ended_at_utc="t1",
                )

            with mock.patch.object(runner, "_run_command", fake_run):
                runner._run_session_command(
                    state,
                    ["x"],
                    role="journey_command",
                    step_id="x",
                    command_ordinal=0,
                    argv_template=["x"],
                    step_dir=session_dir,
                    index=0,
                    timeout_s=1,
                    transcript_path=session_dir / "transcript",
                )
            self.assertNotIn("environment", captured)

    def test_runner_enabled_hook_changes_only_child_environment_and_observes_outcome(
        self,
    ):
        class Hook:
            def __init__(self):
                self.completed = None

            def environment_for(self, **kwargs):
                self.prepared = kwargs
                return {"M007_COVERAGE_TEST": "1"}

            def command_completed(self, **kwargs):
                self.completed = kwargs

        with tempfile.TemporaryDirectory() as temporary:
            session_dir = Path(temporary)
            hook = Hook()
            state = runner.SessionState(
                catalog={"id": "catalog"},
                session_dir=session_dir,
                repo_root=ROOT,
                variables={"vehicle_id": "chase"},
                execution_mode="coverage_only_live",
                session_id="test",
                command_hook=hook,
                coverage_only=True,
            )
            captured: dict[str, object] = {}

            def fake_run(argv, **kwargs):
                captured["argv"] = list(argv)
                captured.update(kwargs)
                return runner.CommandOutcome(
                    argv=list(argv),
                    command="x y",
                    exit_code=0,
                    elapsed_ms=1,
                    stdout_path="out",
                    stderr_path="err",
                    started_at_utc="t0",
                    ended_at_utc="t1",
                )

            with mock.patch.object(runner, "_run_command", fake_run):
                outcome = runner._run_session_command(
                    state,
                    ["x", "y"],
                    role="journey_command",
                    step_id="step",
                    command_ordinal=0,
                    argv_template=["x", "{vehicle_id}"],
                    step_dir=session_dir,
                    index=0,
                    timeout_s=1,
                    transcript_path=session_dir / "transcript",
                )
            self.assertEqual(captured["argv"], ["x", "y"])
            self.assertEqual(captured["environment"], {"M007_COVERAGE_TEST": "1"})
            self.assertEqual(hook.prepared["variables"], {"vehicle_id": "chase"})
            self.assertIs(hook.completed["outcome"], outcome)

    def test_runner_precondition_preserves_manifest_template_before_substitution(self):
        expanded = session.expand_and_validate_manifest()
        vehicle_id = "chase-sim-chaser"
        metrics_ui_origin = "http://localhost:5050"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("configs", "raw"):
                (root / name).mkdir()
            runner_dir = root / "runner"
            (runner_dir / "steps").mkdir(parents=True)
            hook = session.RunnerCoverageHook(
                session_root=root,
                collection_id="c" * 32,
                expanded_manifest=expanded,
            )
            state = runner.SessionState(
                catalog={"id": "m007-acceptance", "track": "acceptance"},
                session_dir=runner_dir,
                repo_root=ROOT,
                variables={
                    "vehicle_id": vehicle_id,
                    "metrics_ui_origin": metrics_ui_origin,
                },
                execution_mode="coverage_only_live",
                session_id="test",
                command_hook=hook,
                coverage_only=True,
            )

            def fake_run(argv, **kwargs):
                step_dir = kwargs["step_dir"]
                step_dir.mkdir(parents=True, exist_ok=True)
                index = kwargs["index"]
                payload = {
                    "schema": runner.STATUS_SCHEMA,
                    "vehicle_id": vehicle_id,
                    "layers": {
                        "automation_worker": {"state": "stopped", "details": {}},
                        "automation_deployment": {"state": "staged"},
                        "perception_view": {"state": "stale"},
                    },
                }
                (step_dir / f"cmd-{index:02d}.stdout.txt").write_text(
                    json.dumps(payload) + "\n", encoding="utf-8"
                )
                (step_dir / f"cmd-{index:02d}.stderr.txt").write_text(
                    "", encoding="utf-8"
                )
                return runner.CommandOutcome(
                    argv=list(argv),
                    command=" ".join(argv),
                    exit_code=0,
                    elapsed_ms=1,
                    stdout_path="steps/_precondition_cleanup/cmd-00.stdout.txt",
                    stderr_path="steps/_precondition_cleanup/cmd-00.stderr.txt",
                    started_at_utc="t0",
                    ended_at_utc="t1",
                )

            with mock.patch.object(runner, "_run_command", fake_run):
                record = runner._run_precondition_cleanup(
                    state,
                    command_timeout_s=5,
                    transcript_path=runner_dir / "transcript.txt",
                    metrics_ui_origin=metrics_ui_origin,
                )
            self.assertTrue(record["ok"], record)
            self.assertEqual(
                hook.receipts[0]["argv_template"],
                [
                    "./cli/automa",
                    "vehicles",
                    "status",
                    "--id",
                    "{vehicle_id}",
                    "--chase-url",
                    "{metrics_ui_origin}",
                    "--json",
                ],
            )
            self.assertEqual(
                hook.receipts[0]["resolved_argv"],
                [
                    "./cli/automa",
                    "vehicles",
                    "status",
                    "--id",
                    vehicle_id,
                    "--chase-url",
                    metrics_ui_origin,
                    "--json",
                ],
            )

    def test_immutable_receipt_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            session._atomic_json_once(path, {"value": 1})
            original = path.read_bytes()
            with self.assertRaises(report.CoverageContractError):
                session._atomic_json_once(path, {"value": 2})
            self.assertEqual(path.read_bytes(), original)

    def test_interpreter_symlink_alias_is_normalized_to_python_token(self):
        expanded = session.expand_and_validate_manifest()
        original = next(
            item for item in expanded["commands"] if item["role"] == "bootstrap"
        )
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            alias = parent / "python-alias"
            alias.symlink_to(Path(sys.executable).resolve())
            root = parent / "session"
            (root / "configs").mkdir(parents=True)
            (root / "raw").mkdir()
            hook = session.RunnerCoverageHook(
                session_root=root,
                collection_id="9" * 32,
                expanded_manifest=expanded,
            )
            entry = {**original, "argv_template": [str(alias)]}
            hook._entries[hook._key(entry)] = entry
            hook.environment_for(
                catalog_id=entry["catalog_id"],
                role=entry["role"],
                step_id=entry["step_id"],
                command_ordinal=entry["command_ordinal"],
                argv_template=entry["argv_template"],
                resolved_argv=entry["argv_template"],
                variables={},
            )
            hook.command_completed(
                catalog_id=entry["catalog_id"],
                role=entry["role"],
                step_id=entry["step_id"],
                command_ordinal=entry["command_ordinal"],
                argv_template=entry["argv_template"],
                resolved_argv=entry["argv_template"],
                variables={},
                outcome=SimpleNamespace(
                    exit_code=0,
                    elapsed_ms=1,
                    started_at_utc="t0",
                    ended_at_utc="t1",
                ),
            )
            self.assertEqual(hook.receipts[0]["argv_template"], ["$PYTHON"])
            self.assertEqual(hook.receipts[0]["resolved_argv"], ["$PYTHON"])

    def test_offline_source_lineage_is_bound_to_producer_and_consumers(self):
        expanded = session.expand_and_validate_manifest()
        commands = [
            dict(item)
            for item in expanded["commands"]
            if item.get("family_id") == "continuity.offline_perception"
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "configs").mkdir()
            (root / "raw").mkdir()
            hook = session.RunnerCoverageHook(
                session_root=root,
                collection_id="8" * 32,
                expanded_manifest=expanded,
            )
            for command in commands:
                command["variables"] = (
                    {}
                    if command["step_id"] == "offline-capture"
                    else {"src_dir": "$REPO/runtime/offline/source"}
                )
            hook.receipts = commands
            identity = hook.bind_offline_source_lineage(
                catalog_id="m007-continuity",
                lineage={
                    "schema": "continuity_source_lineage_v1",
                    "ok": True,
                    "src_dir_redacted": "<repo>/runtime/offline/source",
                    "manifest_sha256": "a" * 64,
                    "ordered_input_sha256": "b" * 64,
                    "frame_count": 1,
                    "frames": [{"sha256": "c" * 64}],
                },
            )
            self.assertEqual(
                identity["source_identity"], "$REPO/runtime/offline/source"
            )
            self.assertEqual(
                [item["offline_source_lineage"]["relation"] for item in hook.receipts],
                ["produced", "consumed", "consumed"],
            )

    def test_recursive_absolute_path_guard_rejects_nested_local_path(self):
        with self.assertRaisesRegex(
            report.CoverageContractError, "local absolute path"
        ):
            report._reject_local_absolute_paths(
                {"commands": [{"resolved_argv": ["/opt/local/bin/python3"]}]}
            )


class ShardAndAttributionTests(unittest.TestCase):
    def _root(self, temporary: str) -> Path:
        root = Path(temporary)
        (root / "raw").mkdir()
        return root

    def test_current_collection_shard_is_inspected_then_explicitly_combined(self):
        logical = "m007/bootstrap/root-help"
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            _write_arc_shard(
                root / "raw" / ".coverage.one",
                context=f"m007-run/{'c' * 32}/{logical}",
            )
            records, combined = report.inspect_and_combine_shards(
                session_root=root,
                repo_root=ROOT,
                collection_id="c" * 32,
                logical_contexts={logical},
                owned_roots=["autonomy", "implementations", "cli/automa_cli"],
            )
            self.assertEqual(records[0]["logical_context_id"], logical)
            self.assertTrue(records[0]["branch_arcs"])
            self.assertTrue(combined.is_file())
            self.assertTrue((root / "raw" / ".coverage.one").is_file())

    def test_same_commit_prior_collection_shard_is_rejected(self):
        logical = "m007/journey/primary/automation-run/cmd-00"
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            _write_arc_shard(
                root / "raw" / ".coverage.old",
                context=f"m007-run/{'d' * 32}/{logical}",
            )
            with self.assertRaisesRegex(
                report.CoverageContractError, "foreign measurement"
            ):
                report.inspect_and_combine_shards(
                    session_root=root,
                    repo_root=ROOT,
                    collection_id="e" * 32,
                    logical_contexts={logical},
                    owned_roots=["autonomy", "implementations", "cli/automa_cli"],
                )

    def test_unknown_empty_and_multiple_context_shards_fail_closed(self):
        logical = "m007/bootstrap/root-help"
        cases = ("unknown", "empty", "multiple")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = self._root(temporary)
                path = root / "raw" / ".coverage.bad"
                data = CoverageData(basename=str(path))
                if case == "unknown":
                    data.set_context(f"m007-run/{'f' * 32}/m007/unknown")
                    data.add_arcs({"cli/automa_cli/app.py": [(-1, 1), (1, -1)]})
                elif case == "empty":
                    data.set_context("")
                    data.add_arcs({"cli/automa_cli/app.py": [(-1, 1), (1, -1)]})
                else:
                    for suffix in ("a", "b"):
                        data.set_context(f"m007-run/{'f' * 32}/{logical}-{suffix}")
                        data.add_arcs({"cli/automa_cli/app.py": [(-1, 1), (1, -1)]})
                data.write()
                with self.assertRaises(report.CoverageContractError):
                    report.inspect_and_combine_shards(
                        session_root=root,
                        repo_root=ROOT,
                        collection_id="f" * 32,
                        logical_contexts={logical},
                        owned_roots=["autonomy", "implementations", "cli/automa_cli"],
                    )

    def test_duplicate_shard_bytes_cannot_inflate_process_count(self):
        logical = "m007/bootstrap/root-help"
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            first = root / "raw" / ".coverage.one"
            second = root / "raw" / ".coverage.two"
            _write_arc_shard(first, context=f"m007-run/{'1' * 32}/{logical}")
            shutil.copyfile(first, second)
            with self.assertRaisesRegex(
                report.CoverageContractError, "duplicate raw shard"
            ):
                report.inspect_and_combine_shards(
                    session_root=root,
                    repo_root=ROOT,
                    collection_id="1" * 32,
                    logical_contexts={logical},
                    owned_roots=["autonomy", "implementations", "cli/automa_cli"],
                )

    def test_symlinked_foreign_shard_is_rejected_before_read(self):
        logical = "m007/bootstrap/root-help"
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "session"
            (root / "raw").mkdir(parents=True)
            outside = parent / "outside.coverage"
            _write_arc_shard(
                outside,
                context=f"m007-run/{'6' * 32}/{logical}",
            )
            (root / "raw" / ".coverage.outside").symlink_to(outside)
            with self.assertRaisesRegex(
                report.CoverageContractError, "regular no-follow"
            ):
                report.inspect_and_combine_shards(
                    session_root=root,
                    repo_root=ROOT,
                    collection_id="6" * 32,
                    logical_contexts={logical},
                    owned_roots=["autonomy", "implementations", "cli/automa_cli"],
                )

    def test_seal_verifies_raw_shard_existence_and_content(self):
        for mutation in ("delete", "change"):
            with self.subTest(
                mutation=mutation
            ), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "session"
                (root / "raw").mkdir(parents=True)
                shard = root / "raw" / ".coverage.one"
                shard.write_bytes(b"sealed shard")
                receipt = root / "shards.json"
                report.write_canonical(
                    receipt,
                    [
                        {
                            "raw_session_path": "raw/.coverage.one",
                            "shard_id": "shard-000-test",
                            "shard_sha256": report.sha256_file(shard),
                        }
                    ],
                )
                report.write_canonical(
                    root / "session-seal.json",
                    {
                        "schema": "m007_cli_coverage_session_seal_v1",
                        "sealed_inputs": [
                            {
                                "path": "shards.json",
                                "sha256": report.sha256_file(receipt),
                            }
                        ],
                        "raw_shards": [
                            {
                                "path": "raw/.coverage.one",
                                "shard_id": "shard-000-test",
                                "sha256": report.sha256_file(shard),
                            }
                        ],
                    },
                )
                session._verify_seal(root)
                if mutation == "delete":
                    shard.unlink()
                else:
                    shard.write_bytes(b"mutated shard")
                with self.assertRaises(report.CoverageContractError):
                    session._verify_seal(root)

    def test_unreadable_or_line_only_shard_does_not_pass_branch_contract(self):
        logical = "m007/bootstrap/root-help"
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            data = CoverageData(basename=str(root / "raw" / ".coverage.lines"))
            data.set_context(f"m007-run/{'2' * 32}/{logical}")
            data.add_lines({"cli/automa_cli/app.py": [1]})
            data.write()
            with self.assertRaisesRegex(report.CoverageContractError, "no branch/arc"):
                report.inspect_and_combine_shards(
                    session_root=root,
                    repo_root=ROOT,
                    collection_id="2" * 32,
                    logical_contexts={logical},
                    owned_roots=["autonomy", "implementations", "cli/automa_cli"],
                )

    def test_context_extraction_preserves_lines_arcs_and_source_path(self):
        logical = "m007/bootstrap/root-help"
        measurement = f"m007-run/{'3' * 32}/{logical}"
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            _write_arc_shard(
                root / "raw" / ".coverage.one",
                context=measurement,
                arcs=((-1, 10), (10, 11), (11, -1)),
            )
            _records, combined = report.inspect_and_combine_shards(
                session_root=root,
                repo_root=ROOT,
                collection_id="3" * 32,
                logical_contexts={logical},
                owned_roots=["autonomy", "implementations", "cli/automa_cli"],
            )
            files, execution = report.extract_context_execution(
                combined_path=combined,
                repo_root=ROOT,
                measurement_to_logical={measurement: logical},
                owned_roots=["autonomy", "implementations", "cli/automa_cli"],
            )
            self.assertEqual(files[0]["path"], "cli/automa_cli/app.py")
            self.assertEqual(
                files[0]["contexts"][0]["executed_arcs"], [[-1, 10], [10, 11], [11, -1]]
            )
            self.assertEqual(
                execution[logical]["cli/automa_cli/app.py"]["lines"], {10, 11}
            )

    def test_bootstrap_set_arithmetic_preserves_raw_classification(self):
        execution = {
            "m007/bootstrap/root-help": {
                "cli/automa_cli/app.py": {"lines": {1, 2}, "arcs": {(-1, 1), (1, 2)}}
            },
            "m007/journey/primary/x/cmd-00": {
                "cli/automa_cli/app.py": {"lines": {2, 3}, "arcs": {(1, 2), (2, 3)}}
            },
        }
        result = report.bootstrap_comparison(
            execution, bootstrap_logical_id="m007/bootstrap/root-help"
        )
        values = result["commands"][0]["files"][0]
        self.assertEqual(values["shared_with_bootstrap_lines"], [2])
        self.assertEqual(values["command_specific_lines"], [3])
        self.assertEqual(values["bootstrap_only_lines"], [1])

    def test_journey_rollups_and_bootstrap_comparisons_are_deterministic(self):
        bootstrap_id = "m007/bootstrap/root-help"
        primary = "m007/journey/primary/a/cmd-00"
        continuity = "m007/journey/continuity.memory_lifecycle/b/cmd-00"
        execution = {
            continuity: {"cli/automa_cli/app.py": {"lines": {3}, "arcs": {(2, 3)}}},
            bootstrap_id: {"cli/automa_cli/app.py": {"lines": {1}, "arcs": {(-1, 1)}}},
            primary: {"cli/automa_cli/app.py": {"lines": {2}, "arcs": {(1, 2)}}},
        }
        commands = [
            {
                "logical_context_id": primary,
                "role": "journey_command",
                "family_id": None,
            },
            {
                "logical_context_id": continuity,
                "role": "journey_command",
                "family_id": "continuity.memory_lifecycle",
            },
            {"logical_context_id": bootstrap_id, "role": "bootstrap"},
        ]
        comparison = report.bootstrap_comparison(
            execution,
            bootstrap_logical_id=bootstrap_id,
            commands=commands,
        )
        self.assertEqual(
            [row["journey_id"] for row in comparison["journeys"]],
            ["continuity.memory_lifecycle", "primary"],
        )
        first = report.aggregate_rollups(execution, commands)
        second = report.aggregate_rollups(
            dict(reversed(list(execution.items()))), list(reversed(commands))
        )
        self.assertEqual(first, second)
        self.assertEqual(
            [(row["kind"], row["id"]) for row in first],
            [
                ("journey", "continuity.memory_lifecycle"),
                ("journey", "primary"),
                ("support", "support"),
                ("cleanup", "cleanup"),
                ("all_contexts", "all_contexts"),
            ],
        )

    def test_missing_worker_shard_cannot_be_masked_by_foreground(self):
        logical = "m007/journey/primary/automation-run/cmd-00"
        collection_id = "7" * 32
        measurement = f"m007-run/{collection_id}/{logical}"
        body_start, _body_end = report.function_body_range(
            ROOT / "cli/automa_cli/automation.py", "run_vehicle_automation"
        )
        foreground = "a" * 64
        worker = "b" * 64
        command = {
            "catalog_id": "m007-acceptance",
            "role": "journey_command",
            "step_id": "automation-run",
            "command_ordinal": 0,
            "logical_context_id": logical,
            "measurement_context": measurement,
            "expects_background_worker": True,
            "new_shard_sha256_visible_at_return": [foreground],
        }
        command_identity = {
            "catalog_id": "m007-acceptance",
            "role": "journey_command",
            "step_id": "automation-run",
            "command_ordinal": 0,
        }
        execution = {
            logical: {
                "cli/automa_cli/automation.py": {
                    "lines": {body_start},
                    "arcs": set(),
                }
            }
        }
        one_shard = [{"logical_context_id": logical, "shard_sha256": foreground}]
        lifecycle = {
            "schema": "m007_cli_coverage_worker_lifecycle_v1",
            "launch": {
                "command": command_identity,
                "logical_context_id": logical,
                "measurement_context": measurement,
                "pid": 123,
                "run_id": "generation-1",
                "generation_matches": True,
                "stdout_pid_matches": True,
                "pid_alive": True,
                "raw_shard_sha256_visible": [foreground],
            },
            "observations": [
                {
                    "kind": "termination",
                    "pid": 123,
                    "run_id": "generation-1",
                    "generation_matches": True,
                    "launch_command": command_identity,
                    "logical_context_id": logical,
                    "measurement_context": measurement,
                    "same_generation": True,
                    "pid_alive": False,
                    "status": "stopped",
                    "raw_shard_sha256_visible": [foreground, worker],
                }
            ],
        }
        checks = report.validate_worker_execution(
            commands=[command],
            shards=one_shard,
            execution=execution,
            repo_root=ROOT,
            worker_probe={
                "path": "cli/automa_cli/automation.py",
                "function": "run_vehicle_automation",
            },
            worker_lifecycles=[lifecycle],
        )
        self.assertFalse(checks[0]["complete"])
        checks = report.validate_worker_execution(
            commands=[command],
            shards=one_shard
            + [{"logical_context_id": logical, "shard_sha256": worker}],
            execution=execution,
            repo_root=ROOT,
            worker_probe={
                "path": "cli/automa_cli/automation.py",
                "function": "run_vehicle_automation",
            },
            worker_lifecycles=[lifecycle],
        )
        self.assertTrue(checks[0]["complete"])
        checks = report.validate_worker_execution(
            commands=[command],
            shards=one_shard * 2,
            execution=execution,
            repo_root=ROOT,
            worker_probe={
                "path": "cli/automa_cli/automation.py",
                "function": "run_vehicle_automation",
            },
            worker_lifecycles=[lifecycle],
        )
        self.assertFalse(checks[0]["complete"])


class CoverageProcessTests(unittest.TestCase):
    def _config(self, root: Path, context: str) -> Path:
        config = root / "session.coveragerc"
        config.write_text(
            "\n".join(
                [
                    "[run]",
                    "branch = True",
                    "parallel = True",
                    "sigterm = True",
                    "patch =",
                    "    subprocess",
                    f"source = {root / 'owned'}",
                    f"data_file = {root / 'raw' / '.coverage'}",
                    f"context = {context}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return config

    def test_foreground_and_python_child_inherit_static_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "raw").mkdir()
            owned = root / "owned"
            owned.mkdir()
            (owned / "sample.py").write_text("VALUE = 7\n", encoding="utf-8")
            child = root / "child.py"
            child.write_text(
                f"import sys; sys.path.insert(0, {str(root)!r}); import owned.sample\n",
                encoding="utf-8",
            )
            parent = root / "parent.py"
            parent.write_text(
                "\n".join(
                    [
                        "import subprocess, sys",
                        f"sys.path.insert(0, {str(root)!r})",
                        "import owned.sample",
                        "raise SystemExit(subprocess.run("
                        f"[sys.executable, {str(child)!r}]).returncode)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            context = "m007-run/" + "4" * 32 + "/m007/journey/test/parent/cmd-00"
            config = self._config(root, context)
            completed = subprocess.run(
                [sys.executable, str(parent)],
                env=_clean_environment(COVERAGE_PROCESS_START=str(config)),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            shards = sorted((root / "raw").glob(".coverage.*"))
            self.assertGreaterEqual(len(shards), 2)
            for shard in shards:
                data = CoverageData(basename=str(shard))
                data.read()
                self.assertEqual(data.measured_contexts(), {context})
                self.assertTrue(data.has_arcs())

    def test_sigterm_flushes_parallel_worker_shard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "raw").mkdir()
            owned = root / "owned"
            owned.mkdir()
            (owned / "sample.py").write_text("VALUE = 9\n", encoding="utf-8")
            worker = root / "worker.py"
            worker.write_text(
                "\n".join(
                    [
                        "import sys, time",
                        f"sys.path.insert(0, {str(root)!r})",
                        "import owned.sample",
                        "print('READY', flush=True)",
                        "while True: time.sleep(0.05)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            context = "m007-run/" + "5" * 32 + "/m007/journey/test/worker/cmd-00"
            config = self._config(root, context)
            process = subprocess.Popen(
                [sys.executable, str(worker)],
                env=_clean_environment(COVERAGE_PROCESS_START=str(config)),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                assert process.stdout is not None
                self.assertEqual(process.stdout.readline().strip(), "READY")
                process.send_signal(signal.SIGTERM)
                process.communicate(timeout=10)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
            shards = sorted((root / "raw").glob(".coverage.*"))
            self.assertEqual(len(shards), 1)
            data = CoverageData(basename=str(shards[0]))
            data.read()
            self.assertEqual(data.measured_contexts(), {context})
            self.assertTrue(data.has_arcs())


class DependencyFreshnessAndDigestTests(unittest.TestCase):
    def test_verifier_rejects_each_semantically_contradictory_pass_report(self):
        baseline = _synthetic_pass_report()

        def verify(payload):
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "report.json"
                report.write_canonical(path, report.finalize_report_digest(payload))
                with mock.patch.object(
                    report, "dependency_environment", return_value={}
                ), mock.patch.object(
                    report, "verify_source_freshness", return_value=(True, [])
                ):
                    return report.verify_canonical_report(path, repo_root=ROOT)

        verify(json.loads(json.dumps(baseline)))
        mutations = {
            "failed collection check": lambda value: value["process_completeness"][
                "collection_checks"
            ]["checks"].__setitem__("background_workers_complete", False),
            "missing observed contexts": lambda value: value["contexts"].__setitem__(
                "observed_logical_contexts", []
            ),
            "incomplete worker": lambda value: value["process_completeness"][
                "worker_checks"
            ][0].__setitem__("complete", False),
            "failed machine preflight": lambda value: value["process_completeness"][
                "runner_results"
            ][0].__setitem__("machine_preflight_verdict", "fail"),
            "failed cleanup": lambda value: (
                value["cleanup"].__setitem__("all_workers_stopped", False),
                value["process_completeness"]["cleanup"].__setitem__(
                    "all_workers_stopped", False
                ),
            ),
            "missing required collection gate": lambda value: value[
                "process_completeness"
            ]["collection_checks"]["checks"].pop("measured_config_probe"),
            "forged lifecycle generation": lambda value: value[
                "process_completeness"
            ]["runner_results"][0]["worker_lifecycles"][0]["observations"][
                0
            ].__setitem__("run_id", "forged-generation"),
            "missing execution context": lambda value: value["files"][0][
                "contexts"
            ].pop(),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                candidate = json.loads(json.dumps(baseline))
                mutate(candidate)
                with self.assertRaises(report.CoverageContractError):
                    verify(candidate)

    def test_dependency_receipt_is_sorted_complete_and_sensitive_urls_are_absent(self):
        receipt = report.dependency_environment(ROOT)
        distributions = receipt["distributions"]
        self.assertGreater(len(distributions), 1)
        self.assertEqual(
            [(entry["name"], entry["version"]) for entry in distributions],
            sorted((entry["name"], entry["version"]) for entry in distributions),
        )
        self.assertEqual(
            len(distributions), len({entry["name"] for entry in distributions})
        )
        self.assertTrue(all("direct_url" not in entry for entry in distributions))
        self.assertTrue(
            all(
                "http://" not in value
                and "https://" not in value
                and "file://" not in value
                for entry in distributions
                for value in entry.values()
            )
        )
        self.assertEqual(
            [entry["path"] for entry in receipt["requirements"]],
            ["requirements.txt", "requirements-test.txt"],
        )
        self.assertEqual(len(receipt["interpreter"]["executable_sha256"]), 64)

    def test_distribution_name_normalization_is_pep503_style(self):
        self.assertEqual(
            report.normalize_distribution_name("Requests_Test.pkg"), "requests-test-pkg"
        )
        with self.assertRaises(report.CoverageContractError):
            report.normalize_distribution_name("---")

    def test_distribution_version_validation_has_no_optional_dependency(self):
        for value in (
            "1.0",
            "v2!1.0rc1.post2.dev3+local.1",
            "1.0-1",
            "2026.08.11",
        ):
            with self.subTest(valid=value):
                self.assertEqual(report.normalize_distribution_version(value), value)
        for value in ("", "release", "1..0", "1.0+bad+local", "1.0\n2"):
            with self.subTest(invalid=value), self.assertRaises(
                report.CoverageContractError
            ):
                report.normalize_distribution_version(value)

    def test_report_digest_omits_only_self_digest_and_is_byte_stable(self):
        value = {
            "schema": report.REPORT_SCHEMA,
            "integrity": {"report_sha256": "0" * 64, "receipt": "fixed"},
            "timestamps": {"finalized_at_utc": "2026-01-01T00:00:00Z"},
            "values": [3, 2, 1],
        }
        finalized = report.finalize_report_digest(value)
        first = report.canonical_file_bytes(finalized)
        second = report.canonical_file_bytes(report.finalize_report_digest(finalized))
        self.assertEqual(first, second)
        self.assertEqual(
            finalized["integrity"]["report_sha256"], report.report_digest(finalized)
        )
        changed = json.loads(first)
        changed["timestamps"]["finalized_at_utc"] = "2026-01-01T00:00:01Z"
        self.assertNotEqual(
            report.report_digest(changed), finalized["integrity"]["report_sha256"]
        )

    def test_canonical_report_forbids_floats_and_nonfinite_values(self):
        with self.assertRaises(report.CoverageContractError):
            report.canonical_json_bytes({"elapsed": 1.5})

    def test_source_freshness_allows_only_evidence_descendant(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            for relative in (
                ".coveragerc",
                "requirements.txt",
                "requirements-test.txt",
            ):
                (repo / relative).write_text(relative + "\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-qm",
                    "subject",
                ],
                cwd=repo,
                check=True,
            )
            recorded = report.source_identity(repo, require_clean=True)
            evidence = repo / report.EVIDENCE_PREFIX / "report.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-qm",
                    "evidence",
                ],
                cwd=repo,
                check=True,
            )
            ok, reasons = report.verify_source_freshness(repo, recorded)
            self.assertTrue(ok, reasons)
            (repo / "requirements.txt").write_text("changed\n", encoding="utf-8")
            ok, reasons = report.verify_source_freshness(repo, recorded)
            self.assertFalse(ok)
            self.assertTrue(
                any("relevant" in reason or "worktree" in reason for reason in reasons)
            )

    def test_repository_coverage_sentinels_are_content_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".coverage").write_bytes(b"developer")
            (root / ".coverage.keep").write_bytes(b"parallel")
            before = report.snapshot_repository_coverage(root)
            self.assertEqual(before, report.snapshot_repository_coverage(root))
            (root / ".coverage.keep").write_bytes(b"changed")
            self.assertNotEqual(before, report.snapshot_repository_coverage(root))


if __name__ == "__main__":
    unittest.main()
