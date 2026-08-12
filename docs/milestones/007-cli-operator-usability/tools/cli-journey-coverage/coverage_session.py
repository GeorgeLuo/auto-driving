#!/usr/bin/env python3
"""Internal implementation for the M007 CLI journey coverage session.

Use the sibling ``coverage_session`` POSIX launcher.  Direct Python execution
is rejected before any collection, finalization, or verification work begins.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import yaml
from coverage import Coverage


TOOL_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOL_DIR.parents[4]
MANIFEST_PATH = TOOL_DIR / "manifest.json"
RUNNER_DIR = TOOL_DIR.parent / "live-cli-session-runner"
RUNNER_PATH = RUNNER_DIR / "session_runner.py"
REPORT_PATH = TOOL_DIR / "coverage_report.py"
COLLECTION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
LOGICAL_CONTEXT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,159}$")
_PUBLIC_LAUNCH_AUTHORIZED = False


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


reporting = _load_module("m007_cli_coverage_report", REPORT_PATH)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: Any) -> None:
    reporting.write_canonical(path, value)


def _atomic_json_once(path: Path, value: Any) -> None:
    """Atomically publish one immutable JSON receipt without replacement."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(temporary, flags, 0o600)
    try:
        data = reporting.canonical_file_bytes(value)
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(fd)
        fd = -1
        os.link(temporary, path)
    except FileExistsError as exc:
        raise reporting.CoverageContractError(
            f"immutable receipt already exists: {path}"
        ) from exc
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _path_in_repo(relative: str) -> Path:
    path = (REPO_ROOT / relative).resolve(strict=True)
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise reporting.CoverageContractError(
            f"manifest path escapes repository: {relative}"
        ) from exc
    return path


def _load_catalog(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise reporting.CoverageContractError(f"catalog is not a mapping: {path}")
    return value


def _is_background_worker_command(argv: Sequence[str]) -> bool:
    joined = list(argv)
    try:
        start = joined.index("vehicles")
    except ValueError:
        return False
    return (
        joined[start : start + 3] == ["vehicles", "automation", "run"]
        and "--help" not in joined
    )


def _logical_for_command(
    *,
    catalog: Mapping[str, Any],
    catalog_manifest: Mapping[str, Any],
    step: Mapping[str, Any],
    role: str,
    ordinal: int,
) -> str:
    step_id = str(step.get("id") or "")
    if role == "journey_command":
        scope = (
            str(step.get("family_id") or "")
            if catalog.get("track") == "continuity"
            else str(catalog_manifest.get("scope_id") or "")
        )
        return f"m007/journey/{scope}/{step_id}/cmd-{ordinal:02d}"
    return (
        f"m007/support/supplemental_capture/"
        f"{catalog.get('id')}-{step_id}/cmd-{ordinal:02d}"
    )


def expand_and_validate_manifest() -> dict[str, Any]:
    manifest = reporting.load_json(MANIFEST_PATH)
    if manifest.get("schema") != reporting.MANIFEST_SCHEMA:
        raise reporting.CoverageContractError(
            f"manifest schema must be {reporting.MANIFEST_SCHEMA}"
        )
    if manifest.get("logical_context_pattern") != LOGICAL_CONTEXT_PATTERN.pattern:
        raise reporting.CoverageContractError(
            "manifest logical-context pattern is not canonical"
        )
    if (
        manifest.get("context_template")
        != "m007-run/{collection_id}/{logical_context_id}"
    ):
        raise reporting.CoverageContractError(
            "manifest measurement-context template is not canonical"
        )

    commands: list[dict[str, Any]] = []
    catalog_records: list[dict[str, Any]] = []
    catalogs = manifest.get("catalogs")
    if not isinstance(catalogs, list) or len(catalogs) != 2:
        raise reporting.CoverageContractError(
            "manifest must bind exactly two accepted catalogs"
        )
    support = manifest.get("support_commands")
    if not isinstance(support, dict):
        raise reporting.CoverageContractError("manifest support_commands is missing")

    for catalog_manifest in catalogs:
        if not isinstance(catalog_manifest, dict):
            raise reporting.CoverageContractError(
                "catalog manifest entry is not an object"
            )
        path_text = str(catalog_manifest.get("path") or "")
        path = _path_in_repo(path_text)
        digest = reporting.sha256_file(path)
        if digest != catalog_manifest.get("sha256"):
            raise reporting.CoverageContractError(
                f"catalog digest mismatch for {path_text}: {digest}"
            )
        catalog = _load_catalog(path)
        catalog_id = str(catalog_manifest.get("id") or "")
        if catalog.get("id") != catalog_id or catalog.get(
            "track"
        ) != catalog_manifest.get("track"):
            raise reporting.CoverageContractError(
                f"catalog identity mismatch for {path_text}"
            )
        steps = [
            step for step in (catalog.get("steps") or []) if isinstance(step, dict)
        ]
        command_steps = [
            step for step in steps if step.get("commands") or step.get("capture_json")
        ]
        actual_ids = [str(step.get("id") or "") for step in command_steps]
        required_ids = [
            str(value) for value in catalog_manifest.get("required_step_ids") or []
        ]
        if actual_ids != required_ids:
            raise reporting.CoverageContractError(
                f"catalog command-step order differs from manifest for {catalog_id}: {actual_ids!r}"
            )
        if catalog.get("track") == "continuity":
            actual_families = sorted(
                {str(step.get("family_id") or "") for step in command_steps}
            )
            required_families = sorted(
                str(value)
                for value in catalog_manifest.get("required_family_ids") or []
            )
            if actual_families != required_families:
                raise reporting.CoverageContractError(
                    f"continuity family set differs from manifest: {actual_families!r}"
                )
        for step in command_steps:
            argv_lists = step.get("commands") or []
            if not isinstance(argv_lists, list):
                raise reporting.CoverageContractError(
                    f"commands is not a list in {catalog_id}"
                )
            expected_exit = int(step.get("expect_exit", 0))
            for ordinal, argv in enumerate(argv_lists):
                if not isinstance(argv, list) or not all(
                    isinstance(part, str) for part in argv
                ):
                    raise reporting.CoverageContractError(
                        f"invalid argv template in {catalog_id}/{step.get('id')}"
                    )
                commands.append(
                    {
                        "catalog_id": catalog_id,
                        "track": catalog.get("track"),
                        "family_id": step.get("family_id"),
                        "step_id": str(step.get("id") or ""),
                        "command_ordinal": ordinal,
                        "role": "journey_command",
                        "argv_template": argv,
                        "logical_context_id": _logical_for_command(
                            catalog=catalog,
                            catalog_manifest=catalog_manifest,
                            step=step,
                            role="journey_command",
                            ordinal=ordinal,
                        ),
                        "expected_exit": expected_exit,
                        "required": True,
                        "expects_background_worker": _is_background_worker_command(
                            argv
                        ),
                    }
                )
            capture = step.get("capture_json")
            if isinstance(capture, dict) and isinstance(capture.get("command"), list):
                ordinal = len(argv_lists)
                capture_argv = capture["command"]
                if not all(isinstance(part, str) for part in capture_argv):
                    raise reporting.CoverageContractError(
                        "invalid capture_json argv template"
                    )
                commands.append(
                    {
                        "catalog_id": catalog_id,
                        "track": catalog.get("track"),
                        "family_id": step.get("family_id"),
                        "step_id": str(step.get("id") or ""),
                        "command_ordinal": ordinal,
                        "role": "supplemental_capture",
                        "argv_template": capture_argv,
                        "logical_context_id": _logical_for_command(
                            catalog=catalog,
                            catalog_manifest=catalog_manifest,
                            step=step,
                            role="supplemental_capture",
                            ordinal=ordinal,
                        ),
                        "expected_exit": 0,
                        "required": True,
                        "expects_background_worker": False,
                    }
                )

        for role in ("precondition", "cleanup"):
            templates = support.get(role)
            if not isinstance(templates, list):
                raise reporting.CoverageContractError(
                    f"support template list missing: {role}"
                )
            for ordinal, argv in enumerate(templates):
                if not isinstance(argv, list) or not all(
                    isinstance(part, str) for part in argv
                ):
                    raise reporting.CoverageContractError(
                        f"invalid {role} support argv"
                    )
                commands.append(
                    {
                        "catalog_id": catalog_id,
                        "track": catalog.get("track"),
                        "family_id": None,
                        "step_id": f"_{role}_cleanup"
                        if role == "precondition"
                        else "_cleanup",
                        "command_ordinal": ordinal,
                        "role": role,
                        "argv_template": argv,
                        "logical_context_id": f"m007/support/{role}/{catalog_id}/cmd-{ordinal:02d}",
                        "expected_exit": 0,
                        "required": False,
                        "expects_background_worker": False,
                    }
                )
        catalog_records.append(
            {
                "id": catalog_id,
                "track": catalog.get("track"),
                "path": path_text,
                "sha256": digest,
            }
        )

    bootstrap = manifest.get("bootstrap")
    config_probe = manifest.get("config_probe")
    if not isinstance(bootstrap, dict) or not isinstance(config_probe, dict):
        raise reporting.CoverageContractError(
            "bootstrap/config probe manifest entries are missing"
        )
    commands.extend(
        [
            {
                "catalog_id": "_collector",
                "track": "support",
                "family_id": None,
                "step_id": "_bootstrap",
                "command_ordinal": 0,
                "role": "bootstrap",
                "argv_template": bootstrap.get("argv"),
                "logical_context_id": bootstrap.get("logical_context_id"),
                "expected_exit": 0,
                "required": True,
                "expects_background_worker": False,
            },
            {
                "catalog_id": "_collector",
                "track": "support",
                "family_id": None,
                "step_id": "_config_probe",
                "command_ordinal": 0,
                "role": "precondition",
                "argv_template": [sys.executable, "-c", "$CONFIG_PROBE"],
                "logical_context_id": config_probe.get("logical_context_id"),
                "expected_exit": 0,
                "required": True,
                "expects_background_worker": False,
            },
        ]
    )

    logical_ids = [str(command.get("logical_context_id") or "") for command in commands]
    invalid = [
        value
        for value in logical_ids
        if not LOGICAL_CONTEXT_PATTERN.fullmatch(value) or "|" in value
    ]
    duplicates = sorted(
        value
        for value, count in __import__("collections").Counter(logical_ids).items()
        if count > 1
    )
    if invalid or duplicates:
        raise reporting.CoverageContractError(
            f"manifest logical contexts invalid={invalid!r} duplicate={duplicates!r}"
        )
    keys = [
        (
            command["catalog_id"],
            command["role"],
            command["step_id"],
            command["command_ordinal"],
        )
        for command in commands
    ]
    if len(keys) != len(set(keys)):
        raise reporting.CoverageContractError(
            "manifest command lookup keys are not unique"
        )

    owned_roots = manifest.get("owned_source_roots")
    if owned_roots != ["autonomy", "implementations", "cli/automa_cli"]:
        raise reporting.CoverageContractError(
            "owned source roots differ from accepted contract"
        )
    if manifest.get("non_claims") != {
        "behavioral_correctness": False,
        "dead_code": False,
        "production_value": False,
        "numeric_coverage_gate": False,
    }:
        raise reporting.CoverageContractError(
            "manifest non_claims are not the four exact false values"
        )
    worker_probe = manifest.get("worker_probe")
    if not isinstance(worker_probe, dict):
        raise reporting.CoverageContractError("worker probe is missing")
    _path_in_repo(str(worker_probe.get("path") or ""))
    return {
        "schema": reporting.MANIFEST_SCHEMA,
        "manifest_path": MANIFEST_PATH.relative_to(REPO_ROOT).as_posix(),
        "manifest_sha256": reporting.sha256_file(MANIFEST_PATH),
        "catalogs": catalog_records,
        "owned_source_roots": owned_roots,
        "worker_probe": worker_probe,
        "bootstrap_logical_context_id": str(bootstrap.get("logical_context_id")),
        "commands": commands,
    }


def _substitute(argv: Sequence[str], variables: Mapping[str, str]) -> list[str]:
    rendered: list[str] = []
    for part in argv:
        value = part
        for key, replacement in variables.items():
            value = value.replace("{" + key + "}", replacement)
        if re.search(r"\{[a-zA-Z0-9_]+\}", value):
            raise reporting.CoverageContractError(
                f"unresolved manifest argv token: {value!r}"
            )
        rendered.append(value)
    return rendered


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


class RunnerCoverageHook:
    """Opt-in runner hook that owns only context-bound child environments."""

    def __init__(
        self,
        *,
        session_root: Path,
        collection_id: str,
        expanded_manifest: Mapping[str, Any],
    ) -> None:
        self.session_root = session_root
        self.collection_id = collection_id
        self.expanded_manifest = expanded_manifest
        self.config_dir = session_root / "configs"
        self.raw_dir = session_root / "raw"
        self.receipts: list[dict[str, Any]] = []
        self._pending: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        self._entries: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        for entry in expanded_manifest["commands"]:
            key = self._key(entry)
            self._entries[key] = dict(entry)
        base = self._load_config_without_coverage_environment(REPO_ROOT / ".coveragerc")
        base_checks = {
            "branch": base.get_option("run:branch") is True,
            "relative_files": base.get_option("run:relative_files") is True,
            "source": base.get_option("run:source")
            == ["autonomy", "implementations", "cli/automa_cli"],
            "omit": base.get_option("run:omit") == ["*/__init__.py"],
            "subprocess_patch": "subprocess" in (base.get_option("run:patch") or []),
            "sigterm": base.get_option("run:sigterm") is True,
        }
        if not all(base_checks.values()):
            raise reporting.CoverageContractError(
                f"repository .coveragerc differs from accepted owned-code baseline: {base_checks}"
            )

    @staticmethod
    def _key(entry: Mapping[str, Any]) -> tuple[str, str, str, int]:
        return (
            str(entry.get("catalog_id") or ""),
            str(entry.get("role") or ""),
            str(entry.get("step_id") or ""),
            int(entry.get("command_ordinal", -1)),
        )

    def _lookup(
        self,
        *,
        catalog_id: str,
        role: str,
        step_id: str,
        command_ordinal: int,
    ) -> tuple[tuple[str, str, str, int], dict[str, Any]]:
        key = (catalog_id, role, step_id, command_ordinal)
        entry = self._entries.get(key)
        if entry is None:
            raise reporting.CoverageContractError(
                f"unregistered measured command: {key!r}"
            )
        return key, entry

    @staticmethod
    def _load_config_without_coverage_environment(path: Path) -> Coverage:
        inherited_coverage = {
            name: value
            for name, value in os.environ.items()
            if name.startswith("COVERAGE_")
        }
        for name in inherited_coverage:
            os.environ.pop(name, None)
        try:
            configured = Coverage(config_file=str(path))
            configured.load()
            return configured
        finally:
            os.environ.update(inherited_coverage)

    def _write_config(self, entry: Mapping[str, Any]) -> Path:
        logical = str(entry["logical_context_id"])
        measurement = f"m007-run/{self.collection_id}/{logical}"
        filename = reporting.sha256_bytes(logical.encode("utf-8"))[:24] + ".coveragerc"
        path = self.config_dir / filename
        data_file = self.raw_dir / ".coverage"
        content = "\n".join(
            [
                "[run]",
                "branch = True",
                "relative_files = True",
                "source =",
                "    autonomy",
                "    implementations",
                "    cli/automa_cli",
                "omit =",
                "    */__init__.py",
                "patch =",
                "    subprocess",
                "parallel = True",
                "sigterm = True",
                f"data_file = {data_file}",
                f"context = {measurement}",
                "",
            ]
        )
        if path.exists():
            if path.read_text(encoding="utf-8") != content:
                raise reporting.CoverageContractError(
                    f"coverage config collision: {path}"
                )
        else:
            path.write_text(content, encoding="utf-8")
            path.chmod(0o600)

        # Coverage.py honors a number of COVERAGE_* overrides while loading a
        # file.  The public launcher already refuses them, and the hook repeats
        # that boundary here so even in-process test callers cannot influence
        # effective containment validation.
        configured = self._load_config_without_coverage_environment(path)
        effective_data = Path(str(configured.get_option("run:data_file"))).resolve()
        checks = {
            "data_file_contained": _within(effective_data, self.session_root),
            "branch": configured.get_option("run:branch") is True,
            "relative_files": configured.get_option("run:relative_files") is True,
            "context": configured.get_option("run:context") == measurement,
            "parallel": configured.get_option("run:parallel") is True,
            "sigterm": configured.get_option("run:sigterm") is True,
            "subprocess_patch": "subprocess"
            in (configured.get_option("run:patch") or []),
        }
        if not all(checks.values()):
            raise reporting.CoverageContractError(
                f"effective command coverage config failed containment/options: {checks}"
            )
        return path

    def _raw_shard_digests(self) -> list[str]:
        digests: list[str] = []
        for path in sorted(
            self.raw_dir.iterdir(), key=lambda candidate: candidate.name
        ):
            if not path.name.startswith(".coverage."):
                continue
            digests.append(reporting.sha256_regular_file(path, root=self.raw_dir))
        return sorted(digests)

    def environment_for(
        self,
        *,
        catalog_id: str,
        role: str,
        step_id: str,
        command_ordinal: int,
        argv_template: Sequence[str],
        resolved_argv: Sequence[str],
        variables: Mapping[str, str],
    ) -> dict[str, str]:
        key, entry = self._lookup(
            catalog_id=catalog_id,
            role=role,
            step_id=step_id,
            command_ordinal=command_ordinal,
        )
        if key in self._pending or any(
            self._key(receipt) == key for receipt in self.receipts
        ):
            raise reporting.CoverageContractError(
                f"measured command executed more than once: {key!r}"
            )
        if list(argv_template) != list(entry["argv_template"]):
            raise reporting.CoverageContractError(
                f"argv template differs from manifest for {key!r}: {list(argv_template)!r}"
            )
        expected_resolved = _substitute(entry["argv_template"], variables)
        if expected_resolved != list(resolved_argv):
            raise reporting.CoverageContractError(
                f"resolved argv differs from manifest for {key!r}: {list(resolved_argv)!r}"
            )
        config_path = self._write_config(entry)
        before = self._raw_shard_digests()
        self._pending[key] = {
            "entry": entry,
            "config_path": config_path,
            "before_shards": before,
        }
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("COVERAGE_")
        }
        python3 = shutil.which("python3", path=environment.get("PATH"))
        if python3 is None or Path(python3).resolve() != Path(sys.executable).resolve():
            raise reporting.CoverageContractError(
                "measured CLI PATH does not resolve python3 to the collector interpreter"
            )
        environment["COVERAGE_PROCESS_START"] = str(config_path.resolve())
        return environment

    def command_completed(
        self,
        *,
        catalog_id: str,
        role: str,
        step_id: str,
        command_ordinal: int,
        argv_template: Sequence[str],
        resolved_argv: Sequence[str],
        variables: Mapping[str, str],
        outcome: Any,
    ) -> None:
        del argv_template
        key, entry = self._lookup(
            catalog_id=catalog_id,
            role=role,
            step_id=step_id,
            command_ordinal=command_ordinal,
        )
        pending = self._pending.pop(key, None)
        if pending is None:
            raise reporting.CoverageContractError(
                f"command completion has no prepared environment: {key!r}"
            )
        after = self._raw_shard_digests()
        created = sorted(set(after) - set(pending["before_shards"]))
        logical = str(entry["logical_context_id"])
        repo_text = str(REPO_ROOT.resolve())
        session_text = str(self.session_root.resolve())
        python_path = Path(sys.executable).resolve(strict=True)

        def normalize(value: str) -> str:
            normalized = value.replace(session_text, "$SESSION").replace(
                repo_text, "$REPO"
            )
            if normalized.startswith("/"):
                try:
                    if Path(normalized).resolve(strict=True) == python_path:
                        return "$PYTHON"
                except (OSError, RuntimeError):
                    pass
            return normalized

        normalized_variables = {
            name: normalize(value) for name, value in sorted(variables.items())
        }
        self.receipts.append(
            {
                **entry,
                "argv_template": [
                    normalize(str(value)) for value in entry["argv_template"]
                ],
                "collection_id": self.collection_id,
                "measurement_context": f"m007-run/{self.collection_id}/{logical}",
                "resolved_argv": [normalize(str(value)) for value in resolved_argv],
                "normalized_working_directory": "$REPO",
                "observed_exit": int(outcome.exit_code),
                "elapsed_ms": int(outcome.elapsed_ms),
                "started_at_utc": str(outcome.started_at_utc),
                "ended_at_utc": str(outcome.ended_at_utc),
                "variables": normalized_variables,
                "new_shards_visible_at_return": len(created),
                "new_shard_sha256_visible_at_return": created,
            }
        )

    def worker_lifecycle_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        launch_command = event.get("launch_command") or event.get("command")
        if not isinstance(launch_command, Mapping):
            raise reporting.CoverageContractError(
                "worker lifecycle event has no launch command identity"
            )
        _key, entry = self._lookup(
            catalog_id=str(launch_command.get("catalog_id") or ""),
            role=str(launch_command.get("role") or ""),
            step_id=str(launch_command.get("step_id") or ""),
            command_ordinal=int(launch_command.get("command_ordinal", -1)),
        )
        if entry.get("expects_background_worker") is not True:
            raise reporting.CoverageContractError(
                "worker lifecycle event is not bound to an expected worker command"
            )
        logical = str(entry["logical_context_id"])
        return {
            **dict(event),
            "logical_context_id": logical,
            "measurement_context": f"m007-run/{self.collection_id}/{logical}",
            "raw_shard_sha256_visible": self._raw_shard_digests(),
        }

    def bind_offline_source_lineage(
        self,
        *,
        catalog_id: str,
        lineage: Mapping[str, Any],
    ) -> dict[str, Any]:
        identity = reporting.derive_offline_lineage_identity(
            lineage,
            catalog_id=catalog_id,
            repo_root=REPO_ROOT,
        )
        bound = 0
        for receipt in self.receipts:
            if (
                receipt.get("catalog_id") != catalog_id
                or receipt.get("family_id") != "continuity.offline_perception"
                or receipt.get("role") != "journey_command"
            ):
                continue
            relation = (
                "produced"
                if receipt.get("step_id") == "offline-capture"
                else "consumed"
            )
            if relation == "consumed":
                observed_source = (receipt.get("variables") or {}).get("src_dir")
                if observed_source != identity["source_identity"]:
                    raise reporting.CoverageContractError(
                        "offline apply command is not bound to the sealed source lineage"
                    )
            receipt["offline_source_lineage"] = {
                **identity,
                "relation": relation,
            }
            bound += 1
        if bound != 3:
            raise reporting.CoverageContractError(
                f"offline source lineage bound {bound} commands instead of 3"
            )
        return identity


def _standalone_command(
    hook: RunnerCoverageHook,
    *,
    entry: Mapping[str, Any],
    argv: Sequence[str],
    probe_output: Path,
) -> subprocess.CompletedProcess[str]:
    variables: dict[str, str] = {}
    environment = hook.environment_for(
        catalog_id=str(entry["catalog_id"]),
        role=str(entry["role"]),
        step_id=str(entry["step_id"]),
        command_ordinal=int(entry["command_ordinal"]),
        argv_template=entry["argv_template"],
        resolved_argv=argv,
        variables=variables,
    )
    started_at = _utc_now()
    started = time.monotonic()
    completed = subprocess.run(
        list(argv),
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    ended_at = _utc_now()
    elapsed_ms = int((time.monotonic() - started) * 1000)
    _write_json(
        probe_output,
        {
            "argv": list(argv),
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        },
    )
    hook.command_completed(
        catalog_id=str(entry["catalog_id"]),
        role=str(entry["role"]),
        step_id=str(entry["step_id"]),
        command_ordinal=int(entry["command_ordinal"]),
        argv_template=entry["argv_template"],
        resolved_argv=argv,
        variables=variables,
        outcome=SimpleNamespace(
            exit_code=completed.returncode,
            elapsed_ms=elapsed_ms,
            started_at_utc=started_at,
            ended_at_utc=ended_at,
        ),
    )
    return completed


def _git_identity(path: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=path, check=False, capture_output=True, text=True
        )
        if completed.returncode != 0:
            raise reporting.CoverageContractError(
                f"git identity failed for {path}: {(completed.stderr or completed.stdout).strip()}"
            )
        return completed.stdout.strip()

    status = [
        line
        for line in run(
            "status", "--porcelain=v1", "--untracked-files=all"
        ).splitlines()
        if line
    ]
    return {
        "commit": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "clean": not status,
        "worktree_status": status,
        "remote_url_sha256": reporting.sha256_bytes(
            run("remote", "get-url", "origin").encode("utf-8")
        ),
    }


def _create_session_root(path: Path) -> Path:
    requested = path.expanduser()
    if requested.exists() or requested.is_symlink():
        raise reporting.CoverageContractError(
            f"session root must not already exist in any form: {requested}"
        )
    parent = requested.parent.resolve(strict=True)
    root = parent / requested.name
    os.mkdir(root, 0o700)
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise reporting.CoverageContractError("session root was not created owner-only")
    for name in ("configs", "raw", "runner", "receipts"):
        (root / name).mkdir(mode=0o700)
    return root.resolve()


def _verify_seal(session_root: Path) -> dict[str, Any]:
    seal_path = session_root / "session-seal.json"
    seal = reporting.load_json(seal_path)
    if seal.get("schema") != "m007_cli_coverage_session_seal_v1":
        raise reporting.CoverageContractError("session seal schema is invalid")
    sealed_inputs = seal.get("sealed_inputs")
    raw_shards = seal.get("raw_shards")
    if not isinstance(sealed_inputs, list) or not isinstance(raw_shards, list):
        raise reporting.CoverageContractError("session seal inputs are malformed")
    sealed_paths: set[str] = set()
    for entry in sealed_inputs:
        if not isinstance(entry, dict):
            raise reporting.CoverageContractError("invalid sealed input entry")
        relative = str(entry.get("path") or "")
        digest = str(entry.get("sha256") or "")
        if (
            not relative
            or Path(relative).is_absolute()
            or relative in sealed_paths
            or not reporting.LOWER_HEX_64.fullmatch(digest)
        ):
            raise reporting.CoverageContractError(
                f"invalid or duplicate sealed input: {relative!r}"
            )
        sealed_paths.add(relative)
        path = session_root / relative
        if reporting.sha256_regular_file(path, root=session_root) != digest:
            raise reporting.CoverageContractError(f"sealed input changed: {relative}")

    raw_dir = session_root / "raw"
    if raw_dir.is_symlink() or not stat.S_ISDIR(raw_dir.lstat().st_mode):
        raise reporting.CoverageContractError("sealed raw shard root is invalid")
    declared_raw_paths: set[str] = set()
    declared_raw_ids: set[str] = set()
    for entry in raw_shards:
        if not isinstance(entry, dict):
            raise reporting.CoverageContractError("invalid sealed raw shard entry")
        relative = str(entry.get("path") or "")
        shard_id = str(entry.get("shard_id") or "")
        digest = str(entry.get("sha256") or "")
        if (
            not relative.startswith("raw/.coverage.")
            or Path(relative).is_absolute()
            or relative in declared_raw_paths
            or not shard_id
            or shard_id in declared_raw_ids
            or not reporting.LOWER_HEX_64.fullmatch(digest)
        ):
            raise reporting.CoverageContractError(
                f"invalid or duplicate sealed raw shard: {relative!r}"
            )
        declared_raw_paths.add(relative)
        declared_raw_ids.add(shard_id)
        if (
            reporting.sha256_regular_file(session_root / relative, root=session_root)
            != digest
        ):
            raise reporting.CoverageContractError(
                f"sealed raw shard changed: {relative}"
            )
    actual_raw_paths = {
        path.relative_to(session_root).as_posix()
        for path in raw_dir.iterdir()
        if path.name.startswith(".coverage.")
    }
    if actual_raw_paths != declared_raw_paths:
        raise reporting.CoverageContractError(
            "sealed raw shard inventory differs from session raw directory"
        )
    try:
        shard_receipts = json.loads(
            (session_root / "shards.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise reporting.CoverageContractError(
            f"cannot read sealed shard receipt: {exc}"
        ) from exc
    if not isinstance(shard_receipts, list) or any(
        not isinstance(shard, dict) for shard in shard_receipts
    ):
        raise reporting.CoverageContractError("sealed shard receipt is malformed")
    expected_raw_shards = [
        {
            "path": str(shard.get("raw_session_path") or ""),
            "shard_id": str(shard.get("shard_id") or ""),
            "sha256": str(shard.get("shard_sha256") or ""),
        }
        for shard in shard_receipts
    ]
    if expected_raw_shards != raw_shards:
        raise reporting.CoverageContractError(
            "sealed raw shard inventory contradicts shard inspection receipt"
        )
    return seal


def _load_runner() -> Any:
    return _load_module("m007_live_cli_session_runner_coverage", RUNNER_PATH)


def collect(
    *,
    requested_root: Path,
    metrics_ui_origin: str,
    metrics_ui_repo: Path,
    timeout_s: float,
) -> dict[str, Any]:
    if os.name != "posix":
        raise reporting.CoverageContractError(
            "canonical collection requires POSIX process semantics"
        )
    expanded = expand_and_validate_manifest()
    source_before = reporting.source_identity(REPO_ROOT, require_clean=True)
    metrics_root = metrics_ui_repo.expanduser().resolve(strict=True)
    metrics_before = _git_identity(metrics_root)
    if not metrics_before["clean"]:
        raise reporting.CoverageContractError(
            "canonical collection requires a clean Metrics UI worktree"
        )
    dependencies_before = reporting.dependency_environment(REPO_ROOT)
    outside_before = reporting.snapshot_repository_coverage(REPO_ROOT)
    session_root = _create_session_root(requested_root)
    collection_id = secrets.token_hex(16)
    if not COLLECTION_ID_PATTERN.fullmatch(
        collection_id
    ):  # pragma: no cover - secrets contract
        raise reporting.CoverageContractError("CSPRNG collection ID is not canonical")
    start_receipt = {
        "schema": "m007_cli_coverage_session_start_v1",
        "collection_id": collection_id,
        "collection_started_at_utc": _utc_now(),
        "source_identity": source_before,
        "metrics_ui_identity": metrics_before,
        "platform": sys.platform,
        "coverage_version": __import__("coverage").__version__,
    }
    _atomic_json_once(session_root / "session-start.json", start_receipt)
    _write_json(
        session_root / "receipts" / "environment-before.json", dependencies_before
    )
    _write_json(
        session_root / "receipts" / "repository-coverage-before.json", outside_before
    )
    _write_json(session_root / "manifest-expanded.json", expanded)

    hook = RunnerCoverageHook(
        session_root=session_root,
        collection_id=collection_id,
        expanded_manifest=expanded,
    )
    entry_by_key = {
        (
            entry["catalog_id"],
            entry["role"],
            entry["step_id"],
            entry["command_ordinal"],
        ): entry
        for entry in expanded["commands"]
    }
    probe_entry = entry_by_key[("_collector", "precondition", "_config_probe", 0)]
    probe_code = "\n".join(
        [
            "import json, sys",
            "from coverage import Coverage",
            "sys.path.insert(0, 'cli')",
            "from automa_cli import app",
            "cov = Coverage.current()",
            "assert cov is not None",
            "print('M007_COVERAGE_PROBE=' + json.dumps({",
            "  'data_file': cov.get_option('run:data_file'),",
            "  'branch': cov.get_option('run:branch'),",
            "  'context': cov.get_option('run:context'),",
            "  'parallel': cov.get_option('run:parallel'),",
            "  'sigterm': cov.get_option('run:sigterm'),",
            "  'patch': cov.get_option('run:patch'),",
            "}, sort_keys=True))",
        ]
    )
    probe_argv = [sys.executable, "-c", probe_code]
    probe_entry = dict(probe_entry)
    probe_entry["argv_template"] = probe_argv
    hook._entries[hook._key(probe_entry)] = probe_entry
    probe_result = _standalone_command(
        hook,
        entry=probe_entry,
        argv=probe_argv,
        probe_output=session_root / "receipts" / "config-probe-command.json",
    )
    probe_line = next(
        (
            line
            for line in probe_result.stdout.splitlines()
            if line.startswith("M007_COVERAGE_PROBE=")
        ),
        None,
    )
    if probe_result.returncode != 0 or probe_line is None:
        raise reporting.CoverageContractError(
            f"measured effective-config probe failed: {probe_result.stderr.strip()}"
        )
    probe_payload = json.loads(probe_line.split("=", 1)[1])
    if not _within(Path(str(probe_payload.get("data_file"))), session_root):
        raise reporting.CoverageContractError(
            "measured config probe data file escapes session"
        )
    expected_probe_context = hook.receipts[-1]["measurement_context"]
    if not (
        probe_payload.get("branch") is True
        and probe_payload.get("parallel") is True
        and probe_payload.get("sigterm") is True
        and probe_payload.get("context") == expected_probe_context
        and "subprocess" in (probe_payload.get("patch") or [])
    ):
        raise reporting.CoverageContractError(
            f"measured config probe mismatch: {probe_payload}"
        )
    _write_json(session_root / "receipts" / "config-probe.json", probe_payload)

    bootstrap_entry = entry_by_key[("_collector", "bootstrap", "_bootstrap", 0)]
    bootstrap_result = _standalone_command(
        hook,
        entry=bootstrap_entry,
        argv=bootstrap_entry["argv_template"],
        probe_output=session_root / "receipts" / "bootstrap-command.json",
    )
    if bootstrap_result.returncode != 0:
        raise reporting.CoverageContractError("bootstrap help probe failed")

    runner = _load_runner()
    runner_results: list[dict[str, Any]] = []
    offline_source_lineages: list[dict[str, Any]] = []
    lineage_seal_names: list[str] = []
    for catalog_record in expanded["catalogs"]:
        catalog_path = REPO_ROOT / catalog_record["path"]
        catalog = runner._load_catalog(catalog_path)  # runner owns catalog parsing
        runner_session = session_root / "runner" / str(catalog_record["id"])
        result = runner.run_session(
            catalog=catalog,
            session_dir=runner_session,
            repo_root=REPO_ROOT,
            metrics_ui_origin=metrics_ui_origin,
            metrics_ui_repo=metrics_root,
            browser_name=None,
            browser_version=None,
            prompt=lambda _message: "skip",
            non_interactive=True,
            auto_visual="skip",
            command_timeout_s=timeout_s,
            dry_run=False,
            browser_view_path=None,
            operator=None,
            catalog_path=catalog_path,
            machine_only=True,
            command_hook=hook,
            coverage_only=True,
        )
        worker_lifecycles = result.get("coverage_worker_lifecycles")
        if not isinstance(worker_lifecycles, list):
            raise reporting.CoverageContractError(
                "coverage-only runner omitted worker lifecycle receipts"
            )
        if catalog_record["id"] == "m007-continuity":
            lineage_path = runner_session / "offline-source-lineage.json"
            raw_lineage = reporting.load_json(lineage_path)
            identity = hook.bind_offline_source_lineage(
                catalog_id=str(catalog_record["id"]),
                lineage=raw_lineage,
            )
            relative_lineage = lineage_path.relative_to(session_root).as_posix()
            # Seal a path-tokenized content projection of the raw receipt so
            # verification can re-derive the promoted identity without local
            # absolute paths or trusting mutable report fields alone.
            sealed_content = reporting.sealed_offline_lineage_content(
                raw_lineage,
                source_identity=str(identity["source_identity"]),
            )
            offline_source_lineages.append(
                {
                    **identity,
                    "raw_receipt": {
                        "path": relative_lineage,
                        "sha256": reporting.sha256_bytes(
                            reporting.canonical_json_bytes(sealed_content)
                        ),
                        "content": sealed_content,
                    },
                }
            )
            lineage_seal_names.append(relative_lineage)
        runner_results.append(
            {
                "catalog_id": catalog_record["id"],
                "result": result.get("result"),
                "behavioral_verdict": result.get("behavioral_verdict"),
                "machine_preflight_verdict": (
                    result.get("machine_preflight") or {}
                ).get("verdict"),
                "cleanup": result.get("cleanup"),
                "ordered_step_ids": [
                    step.get("id") for step in result.get("ordered_step_outcomes") or []
                ],
                "worker_lifecycles": worker_lifecycles,
            }
        )

    _write_json(session_root / "commands.json", hook.receipts)
    _write_json(session_root / "runner-results.json", runner_results)
    _write_json(
        session_root / "receipts" / "offline-source-lineages.json",
        offline_source_lineages,
    )
    cleanup_receipt = {
        "all_workers_stopped": all(
            (result.get("cleanup") or {}).get("worker_stopped") is True
            for result in runner_results
        ),
        "catalogs": [
            {
                "catalog_id": result["catalog_id"],
                "worker_stopped": (result.get("cleanup") or {}).get("worker_stopped"),
                "pid_alive": (result.get("cleanup") or {}).get("pid_alive"),
            }
            for result in runner_results
        ],
    }
    _write_json(session_root / "receipts" / "cleanup.json", cleanup_receipt)

    dependencies_after = reporting.dependency_environment(REPO_ROOT)
    source_after = reporting.source_identity(REPO_ROOT, require_clean=False)
    metrics_after = _git_identity(metrics_root)
    outside_after = reporting.snapshot_repository_coverage(REPO_ROOT)
    _write_json(
        session_root / "receipts" / "environment-after.json", dependencies_after
    )
    _write_json(session_root / "receipts" / "source-after.json", source_after)
    _write_json(session_root / "receipts" / "metrics-ui-after.json", metrics_after)
    _write_json(
        session_root / "receipts" / "repository-coverage-after.json", outside_after
    )

    # Optional cleanup preconditions are catalog-declared but execute only when
    # the initial status finds a live/stale worker. Canonical expected contexts
    # are therefore the exact command receipts, while required-key validation
    # below still proves every unconditional manifest command ran.
    logical_contexts = {
        str(receipt["logical_context_id"]) for receipt in hook.receipts
    }
    shards, combined_path = reporting.inspect_and_combine_shards(
        session_root=session_root,
        repo_root=REPO_ROOT,
        collection_id=collection_id,
        logical_contexts=logical_contexts,
        owned_roots=expanded["owned_source_roots"],
    )
    _write_json(session_root / "shards.json", shards)
    measurement_to_logical = {
        str(receipt["measurement_context"]): str(receipt["logical_context_id"])
        for receipt in hook.receipts
    }
    _files, execution = reporting.extract_context_execution(
        combined_path=combined_path,
        repo_root=REPO_ROOT,
        measurement_to_logical=measurement_to_logical,
        owned_roots=expanded["owned_source_roots"],
    )
    worker_checks = reporting.validate_worker_execution(
        commands=hook.receipts,
        shards=shards,
        execution=execution,
        repo_root=REPO_ROOT,
        worker_probe=expanded["worker_probe"],
        worker_lifecycles=[
            lifecycle
            for result in runner_results
            for lifecycle in result["worker_lifecycles"]
        ],
    )
    _write_json(session_root / "receipts" / "worker-checks.json", worker_checks)

    receipt_keys = {hook._key(receipt) for receipt in hook.receipts}
    required_keys = {
        hook._key(entry)
        for entry in expanded["commands"]
        if entry.get("required") is True
    }
    missing_required = sorted(repr(key) for key in required_keys - receipt_keys)
    context_counts = __import__("collections").Counter(
        str(shard["logical_context_id"]) for shard in shards
    )
    missing_foreground = sorted(
        str(receipt["logical_context_id"])
        for receipt in hook.receipts
        if context_counts.get(str(receipt["logical_context_id"]), 0) < 1
    )
    nonzero = [
        str(receipt["logical_context_id"])
        for receipt in hook.receipts
        if int(receipt["observed_exit"]) != int(receipt["expected_exit"])
    ]
    integrity_checks = {
        "manifest_complete": not missing_required,
        "all_command_exits_expected": not nonzero,
        "all_executed_contexts_have_shards": not missing_foreground,
        "background_workers_complete": all(
            check["complete"] for check in worker_checks
        ),
        "offline_replay_lineage_complete": len(offline_source_lineages) == 1,
        "runner_machine_preflight": all(
            result.get("machine_preflight_verdict") == "pass"
            for result in runner_results
        ),
        "cleanup": cleanup_receipt["all_workers_stopped"] is True,
        "dependency_environment_unchanged": dependencies_before == dependencies_after,
        "relevant_source_unchanged": source_before["relevant"]
        == source_after["relevant"]
        and source_before["commit"] == source_after["commit"],
        "metrics_ui_identity_unchanged": metrics_before == metrics_after,
        "repository_coverage_unchanged": outside_before == outside_after,
        "measured_config_probe": True,
    }
    reasons = {
        "missing_required_commands": missing_required,
        "unexpected_command_exits": nonzero,
        "missing_foreground_contexts": missing_foreground,
        "incomplete_background_contexts": [
            check["logical_context_id"]
            for check in worker_checks
            if not check["complete"]
        ],
        "missing_offline_source_lineage": (
            [] if len(offline_source_lineages) == 1 else ["m007-continuity"]
        ),
        "failed_machine_preflight_catalogs": [
            str(result["catalog_id"])
            for result in runner_results
            if result.get("machine_preflight_verdict") != "pass"
        ],
    }
    if all(integrity_checks.values()):
        collection_result = "pass"
    elif all(
        integrity_checks[name]
        for name in (
            "dependency_environment_unchanged",
            "relevant_source_unchanged",
            "metrics_ui_identity_unchanged",
            "repository_coverage_unchanged",
            "cleanup",
        )
    ):
        collection_result = "incomplete"
    else:
        collection_result = "failed"
    collection_checks = {
        "result": collection_result,
        "checks": integrity_checks,
        "reasons": reasons,
    }
    expected_logical = sorted(logical_contexts)
    observed_logical = sorted(context_counts)
    reporting.validate_acceptance_semantics(
        claimed_result=collection_result,
        reason_codes=[],
        commands=hook.receipts,
        shards=shards,
        worker_checks=worker_checks,
        cleanup=cleanup_receipt,
        collection_checks=collection_checks,
        runner_results=runner_results,
        offline_source_lineages=offline_source_lineages,
        contexts={
            "collection_id": collection_id,
            "expected_logical_contexts": expected_logical,
            "observed_logical_contexts": observed_logical,
            "measurement_to_logical": [
                {
                    "logical_context_id": logical,
                    "measurement_context": f"m007-run/{collection_id}/{logical}",
                    "shard_count": context_counts.get(logical, 0),
                }
                for logical in expected_logical
            ],
            "empty_contexts": [],
            "foreign_contexts": [],
            "unknown_contexts": [],
        },
        freshness={
            "source_ok": integrity_checks["relevant_source_unchanged"],
            "source_reasons": [],
            "dependency_ok": integrity_checks["dependency_environment_unchanged"],
        },
        expected_contract=expected_acceptance_contract(expanded),
        repo_root=REPO_ROOT,
    )
    _write_json(session_root / "receipts" / "collection-checks.json", collection_checks)

    seal_names = [
        "session-start.json",
        "manifest-expanded.json",
        "commands.json",
        "runner-results.json",
        "shards.json",
        "combined/.coverage",
        "receipts/config-probe.json",
        "receipts/environment-before.json",
        "receipts/environment-after.json",
        "receipts/source-after.json",
        "receipts/metrics-ui-after.json",
        "receipts/repository-coverage-before.json",
        "receipts/repository-coverage-after.json",
        "receipts/cleanup.json",
        "receipts/worker-checks.json",
        "receipts/offline-source-lineages.json",
        "receipts/collection-checks.json",
        *lineage_seal_names,
    ]
    sealed_inputs = [
        {
            "path": name,
            "sha256": reporting.sha256_regular_file(
                session_root / name, root=session_root
            ),
        }
        for name in seal_names
    ]
    raw_shards = [
        {
            "shard_id": shard["shard_id"],
            "sha256": shard["shard_sha256"],
            "path": shard["raw_session_path"],
        }
        for shard in shards
    ]
    seal = {
        "schema": "m007_cli_coverage_session_seal_v1",
        "collection_id": collection_id,
        "collection_ended_at_utc": _utc_now(),
        "collection_result": collection_result,
        "sealed_inputs": sealed_inputs,
        "raw_shards": raw_shards,
    }
    _atomic_json_once(session_root / "session-seal.json", seal)
    print(
        json.dumps(
            {
                "result": collection_result,
                "session_root": str(session_root),
                "collection_id": collection_id,
                "commands": len(hook.receipts),
                "shards": len(shards),
            },
            sort_keys=True,
        )
    )
    return collection_checks


def _report_from_session(session_root: Path) -> dict[str, Any]:
    seal = _verify_seal(session_root)
    start = reporting.load_json(session_root / "session-start.json")
    expanded = reporting.load_json(session_root / "manifest-expanded.json")
    commands_doc = json.loads(
        (session_root / "commands.json").read_text(encoding="utf-8")
    )
    shards_doc = json.loads((session_root / "shards.json").read_text(encoding="utf-8"))
    runner_results = json.loads(
        (session_root / "runner-results.json").read_text(encoding="utf-8")
    )
    if not isinstance(commands_doc, list) or not isinstance(shards_doc, list):
        raise reporting.CoverageContractError(
            "sealed command/shard receipt is malformed"
        )
    seal_hash = reporting.sha256_file(session_root / "session-seal.json")
    final_path = session_root / "finalization-receipt.json"
    if final_path.exists():
        final_receipt = reporting.load_json(final_path)
        if final_receipt.get("session_seal_sha256") != seal_hash:
            raise reporting.CoverageContractError(
                "finalization receipt binds a different session seal"
            )
    else:
        final_receipt = {
            "schema": "m007_cli_coverage_finalization_receipt_v1",
            "finalized_at_utc": _utc_now(),
            "session_seal_sha256": seal_hash,
        }
        _atomic_json_once(final_path, final_receipt)

    dependencies = reporting.load_json(
        session_root / "receipts" / "environment-before.json"
    )
    dependencies_after = reporting.load_json(
        session_root / "receipts" / "environment-after.json"
    )
    if (
        dependencies != dependencies_after
        or dependencies != reporting.dependency_environment(REPO_ROOT)
    ):
        raise reporting.CoverageContractError(
            "dependency receipt changed before finalization"
        )
    source_identity = start.get("source_identity") or {}
    source_ok, source_reasons = reporting.verify_source_freshness(
        REPO_ROOT, source_identity
    )
    collection_checks = reporting.load_json(
        session_root / "receipts" / "collection-checks.json"
    )
    cleanup = reporting.load_json(session_root / "receipts" / "cleanup.json")
    worker_checks = json.loads(
        (session_root / "receipts" / "worker-checks.json").read_text(encoding="utf-8")
    )
    offline_source_lineages = json.loads(
        (session_root / "receipts" / "offline-source-lineages.json").read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(worker_checks, list) or not isinstance(
        offline_source_lineages, list
    ):
        raise reporting.CoverageContractError(
            "worker checks or offline lineage receipt is malformed"
        )
    measurement_to_logical = {
        str(command["measurement_context"]): str(command["logical_context_id"])
        for command in commands_doc
    }
    files, execution = reporting.extract_context_execution(
        combined_path=session_root / "combined" / ".coverage",
        repo_root=REPO_ROOT,
        measurement_to_logical=measurement_to_logical,
        owned_roots=expanded["owned_source_roots"],
    )
    bootstrap = reporting.bootstrap_comparison(
        execution,
        bootstrap_logical_id=str(expanded["bootstrap_logical_context_id"]),
        commands=commands_doc,
    )
    report_shards = [
        {key: value for key, value in shard.items() if key != "raw_session_path"}
        for shard in shards_doc
    ]
    observed_logical = sorted(
        {str(shard["logical_context_id"]) for shard in shards_doc}
    )
    expected_logical = sorted(
        {str(command["logical_context_id"]) for command in commands_doc}
    )
    shard_counts = __import__("collections").Counter(
        str(shard["logical_context_id"]) for shard in shards_doc
    )
    context_rows = [
        {
            "logical_context_id": logical,
            "measurement_context": f"m007-run/{start['collection_id']}/{logical}",
            "shard_count": shard_counts.get(logical, 0),
        }
        for logical in expected_logical
    ]
    command_rollup = {
        role: len([command for command in commands_doc if command.get("role") == role])
        for role in (
            "bootstrap",
            "journey_command",
            "supplemental_capture",
            "precondition",
            "cleanup",
        )
    }
    executed_lines = sum(
        len(context["executed_lines"])
        for file_record in files
        for context in file_record["contexts"]
    )
    executed_arcs = sum(
        len(context["executed_arcs"])
        for file_record in files
        for context in file_record["contexts"]
    )
    freshness = {
        "source_ok": source_ok,
        "source_reasons": source_reasons,
        "dependency_ok": True,
    }
    result = (
        "pass"
        if seal.get("collection_result") == "pass" and source_ok
        else "incomplete"
        if seal.get("collection_result") == "incomplete" and source_ok
        else "failed"
    )
    reason_codes = []
    if seal.get("collection_result") != "pass":
        reason_codes.append(f"collection_{seal.get('collection_result')}")
    if not source_ok:
        reason_codes.append("source_stale")
    relevant_files = source_identity.get("relevant", {}).get("files", [])
    input_paths = {
        str(item["path"]): str(item["sha256"])
        for item in relevant_files
        if isinstance(item, dict) and item.get("path") and item.get("sha256")
    }
    report = {
        "schema": reporting.REPORT_SCHEMA,
        "result": result,
        "reason_codes": reason_codes,
        "timestamps": {
            "collection_started_at_utc": start["collection_started_at_utc"],
            "collection_ended_at_utc": seal["collection_ended_at_utc"],
            "finalized_at_utc": final_receipt["finalized_at_utc"],
        },
        "cleanup": cleanup,
        "subject": {
            "source_identity": source_identity,
            "platform": start.get("platform"),
            "coverage_version": start.get("coverage_version"),
            "collection_id": start["collection_id"],
        },
        "inputs": {
            "manifest": {
                "path": expanded["manifest_path"],
                "sha256": expanded["manifest_sha256"],
            },
            "catalogs": expanded["catalogs"],
            "relevant_file_sha256": input_paths,
            "owned_source_roots": expanded["owned_source_roots"],
            "worker_probe": expanded["worker_probe"],
            "offline_source_lineages": offline_source_lineages,
            "metrics_ui_identity": start.get("metrics_ui_identity"),
        },
        "dependency_environment": dependencies,
        "commands": commands_doc,
        "process_completeness": {
            "shards": report_shards,
            "worker_checks": worker_checks,
            "cleanup": cleanup,
            "collection_checks": collection_checks,
            "runner_results": runner_results,
        },
        "contexts": {
            "collection_id": start["collection_id"],
            "expected_logical_contexts": expected_logical,
            "observed_logical_contexts": observed_logical,
            "measurement_to_logical": context_rows,
            "empty_contexts": [],
            "foreign_contexts": [],
            "unknown_contexts": [],
        },
        "files": files,
        "bootstrap_comparison": bootstrap,
        "aggregates": {
            "command_roles": command_rollup,
            "commands": len(commands_doc),
            "contexts": len(expected_logical),
            "shards": len(shards_doc),
            "files": len(files),
            "executed_line_entries": executed_lines,
            "executed_arc_entries": executed_arcs,
            "rollups": reporting.aggregate_rollups(execution, commands_doc),
            "numeric_gate": False,
        },
        "integrity": {
            "canonical_json": {
                "ensure_ascii": False,
                "allow_nan": False,
                "sort_keys": True,
                "separators": [",", ":"],
                "trailing_lf": 1,
                "digest_projection_omits": ["integrity.report_sha256"],
            },
            # Embed immutable receipt contents so offline verification can re-derive
            # hashes/timestamps without trusting digest-shaped report strings alone.
            "session_seal": seal,
            "session_seal_sha256": seal_hash,
            "finalization_receipt": final_receipt,
            "finalization_receipt_sha256": reporting.sha256_file(final_path),
            "session_start": start,
            "freshness": freshness,
        },
        "non_claims": {
            "behavioral_correctness": False,
            "dead_code": False,
            "production_value": False,
            "numeric_coverage_gate": False,
        },
    }
    # Live accepted manifest is the authority; the sealed session expansion must
    # already match it (source freshness also re-checks relevant inputs).
    reporting.validate_report_semantics(
        report,
        repo_root=REPO_ROOT,
        expected_contract=expected_acceptance_contract(),
    )
    return reporting.finalize_report_digest(report)


def finalize(*, session_root: Path, output: Path) -> dict[str, Any]:
    root = session_root.expanduser().resolve(strict=True)
    report = _report_from_session(root)
    reporting.write_canonical(output.expanduser().resolve(), report)
    # A second assembly from the same seal/receipt must be byte-identical.
    regenerated = _report_from_session(root)
    if reporting.canonical_file_bytes(report) != reporting.canonical_file_bytes(
        regenerated
    ):
        raise reporting.CoverageContractError(
            "repeated finalization is not byte-identical"
        )
    reporting.verify_canonical_report(
        output.expanduser().resolve(),
        repo_root=REPO_ROOT,
        expected_contract=expected_acceptance_contract(),
    )
    print(
        json.dumps(
            {
                "result": report["result"],
                "output": str(output),
                "report_sha256": report["integrity"]["report_sha256"],
            },
            sort_keys=True,
        )
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="M007 CLI journey coverage collector")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-manifest")
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--session-dir", type=Path, required=True)
    collect_parser.add_argument("--metrics-ui-origin", required=True)
    collect_parser.add_argument("--metrics-ui-repo", type=Path, required=True)
    collect_parser.add_argument("--timeout-s", type=float, default=120.0)
    finalize_parser = sub.add_parser("finalize")
    finalize_parser.add_argument("--session-dir", type=Path, required=True)
    finalize_parser.add_argument("--output", type=Path, required=True)
    verify_parser = sub.add_parser("verify-report")
    verify_parser.add_argument("report", type=Path)
    return parser


_LAUNCH_CAPABILITY_HEADER = "m007-coverage-launch-v1"
_LAUNCH_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def authorize_public_launch(*, capability_fd: int, launcher_path: Path) -> None:
    """Authorize normal operation from the public POSIX launcher capability FD.

    The supported entrypoint is the reviewed shell script: it refuses ambient
    ``COVERAGE_*`` before Python starts, requires an absolute native
    ``M007_COVERAGE_PYTHON``, then mints an unlinked capability receipt on an
    inherited FD.  Accidental direct module entry without that FD refuses.

    This is not a same-user adversarial trust root.  A process that reimplements
    the full shell capability protocol is treated as using the public launcher
    surface; the enforceable shell guarantee remains pre-interpreter ambient
    refusal when the reviewed script is the entrypoint.
    """

    global _PUBLIC_LAUNCH_AUTHORIZED
    expected = (TOOL_DIR / "coverage_session").resolve(strict=True)
    try:
        provided = launcher_path.resolve(strict=True)
    except OSError as exc:
        raise reporting.CoverageContractError(
            "public launcher path is missing or unreadable"
        ) from exc
    if provided != expected:
        raise reporting.CoverageContractError(
            "public launcher path is not the reviewed coverage_session entrypoint"
        )
    if type(capability_fd) is not int or capability_fd < 0:
        raise reporting.CoverageContractError(
            "public launcher capability FD is malformed"
        )
    try:
        payload = os.read(capability_fd, 4096)
    except OSError as exc:
        raise reporting.CoverageContractError(
            "public launcher capability FD is unreadable"
        ) from exc
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise reporting.CoverageContractError(
            "public launcher capability is not ASCII"
        ) from exc
    lines = text.splitlines()
    if (
        len(lines) != 3
        or lines[0] != _LAUNCH_CAPABILITY_HEADER
        or not _LAUNCH_TOKEN_PATTERN.fullmatch(lines[2])
    ):
        raise reporting.CoverageContractError(
            "public launcher capability receipt is malformed"
        )
    try:
        claimed_launcher = Path(lines[1]).resolve(strict=True)
    except OSError as exc:
        raise reporting.CoverageContractError(
            "public launcher capability path is invalid"
        ) from exc
    if claimed_launcher != expected:
        raise reporting.CoverageContractError(
            "public launcher capability path is not the reviewed entrypoint"
        )
    try:
        fd_stat = os.fstat(capability_fd)
    except OSError as exc:
        raise reporting.CoverageContractError(
            "public launcher capability FD cannot be stated"
        ) from exc
    if not stat.S_ISREG(fd_stat.st_mode):
        raise reporting.CoverageContractError(
            "public launcher capability FD is not a regular file"
        )
    # The public shell unlinks the capability file after open; require nlink==0
    # so a live still-linked path cannot be presented as the shell receipt.
    if fd_stat.st_nlink != 0:
        raise reporting.CoverageContractError(
            "public launcher capability FD is not an unlinked shell receipt"
        )
    _PUBLIC_LAUNCH_AUTHORIZED = True


def require_public_launcher(launcher_path: Path) -> None:
    """Compatibility alias — parent-text authorization is no longer accepted."""

    del launcher_path
    raise reporting.CoverageContractError(
        "public launcher boundary missing: capability FD authorization is required"
    )


def expected_acceptance_contract(
    expanded: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive the acceptance contract from the accepted manifest authority."""

    payload = expanded if expanded is not None else expand_and_validate_manifest()
    return reporting.expected_contract_from_expanded(payload)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        if not _PUBLIC_LAUNCH_AUTHORIZED:
            raise reporting.CoverageContractError(
                "public launcher boundary missing: use the coverage_session entrypoint"
            )
        ambient_coverage = sorted(
            name for name in os.environ if name.startswith("COVERAGE_")
        )
        if ambient_coverage:
            raise reporting.CoverageContractError(
                "inherited COVERAGE_* environment reached the internal boundary: "
                + ", ".join(ambient_coverage)
            )
        args = build_parser().parse_args(argv)
        if args.command == "validate-manifest":
            expanded = expand_and_validate_manifest()
            print(
                json.dumps(
                    {
                        "result": "pass",
                        "commands": len(expanded["commands"]),
                        "logical_contexts": len(
                            {
                                entry["logical_context_id"]
                                for entry in expanded["commands"]
                            }
                        ),
                        "manifest_sha256": expanded["manifest_sha256"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "collect":
            result = collect(
                requested_root=args.session_dir,
                metrics_ui_origin=args.metrics_ui_origin,
                metrics_ui_repo=args.metrics_ui_repo,
                timeout_s=args.timeout_s,
            )
            return 0 if result["result"] == "pass" else 2
        if args.command == "finalize":
            report = finalize(session_root=args.session_dir, output=args.output)
            return 0 if report["result"] == "pass" else 2
        if args.command == "verify-report":
            contract = expected_acceptance_contract()
            report = reporting.verify_canonical_report(
                args.report.expanduser().resolve(strict=True),
                repo_root=REPO_ROOT,
                expected_contract=contract,
            )
            print(
                json.dumps(
                    {
                        "result": report["result"],
                        "report_sha256": report["integrity"]["report_sha256"],
                    },
                    sort_keys=True,
                )
            )
            return 0 if report["result"] == "pass" else 2
        raise AssertionError(args.command)
    except reporting.CoverageContractError as exc:
        print(f"coverage session refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    print(
        "coverage session refused: direct Python entrypoint is unsupported; "
        "use the public coverage_session launcher",
        file=sys.stderr,
    )
    raise SystemExit(2)
