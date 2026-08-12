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
        self.assertIn("direct Python entrypoint refused", completed.stderr)


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

    def test_immutable_receipt_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            session._atomic_json_once(path, {"value": 1})
            original = path.read_bytes()
            with self.assertRaises(report.CoverageContractError):
                session._atomic_json_once(path, {"value": 2})
            self.assertEqual(path.read_bytes(), original)


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
        values = result["contexts"][0]["files"][0]
        self.assertEqual(values["shared_with_bootstrap_lines"], [2])
        self.assertEqual(values["command_specific_lines"], [3])
        self.assertEqual(values["bootstrap_only_lines"], [1])

    def test_missing_worker_shard_cannot_be_masked_by_foreground(self):
        logical = "m007/journey/primary/automation-run/cmd-00"
        body_start, _body_end = report.function_body_range(
            ROOT / "cli/automa_cli/automation.py", "run_vehicle_automation"
        )
        command = {"logical_context_id": logical, "expects_background_worker": True}
        execution = {
            logical: {
                "cli/automa_cli/automation.py": {
                    "lines": {body_start},
                    "arcs": set(),
                }
            }
        }
        one_shard = [{"logical_context_id": logical}]
        checks = report.validate_worker_execution(
            commands=[command],
            shards=one_shard,
            execution=execution,
            repo_root=ROOT,
            worker_probe={
                "path": "cli/automa_cli/automation.py",
                "function": "run_vehicle_automation",
            },
        )
        self.assertFalse(checks[0]["complete"])
        checks = report.validate_worker_execution(
            commands=[command],
            shards=one_shard * 2,
            execution=execution,
            repo_root=ROOT,
            worker_probe={
                "path": "cli/automa_cli/automation.py",
                "function": "run_vehicle_automation",
            },
        )
        self.assertTrue(checks[0]["complete"])


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
