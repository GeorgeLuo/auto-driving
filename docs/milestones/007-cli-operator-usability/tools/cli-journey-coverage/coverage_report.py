#!/usr/bin/env python3
"""Canonical reporting primitives for M007 CLI journey coverage.

Only documented Python and Coverage.py APIs are used here.  Raw databases are
kept as opaque CoverageData inputs; this module never queries Coverage.py's
SQLite schema or relies on parallel-data filenames for process identity.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import sysconfig
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from coverage import CoverageData


REPORT_SCHEMA = "m007_cli_journey_coverage_v1"
MANIFEST_SCHEMA = "m007_cli_coverage_manifest_v1"
MEASUREMENT_PREFIX = "m007-run/"
EVIDENCE_PREFIX = (
    "docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/"
)
RELEVANT_PATHS = (
    ".coveragerc",
    "requirements.txt",
    "requirements-test.txt",
    "autonomy",
    "implementations",
    "cli/automa_cli",
    "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner",
    "docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage",
)
LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
LOWER_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
NORMALIZED_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
PEP440_VERSION = re.compile(
    r"""
    ^v?
    (?:(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*)
    (?:[-_.]?(?:alpha|a|beta|b|preview|pre|c|rc)[-_.]?[0-9]*)?
    (?:-[0-9]+|[-_.]?(?:post|rev|r)[-_.]?[0-9]*)?
    (?:[-_.]?dev[-_.]?[0-9]*)?
    (?:\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)


class CoverageContractError(RuntimeError):
    """A fail-closed coverage integrity or reproducibility violation."""


def canonical_json_bytes(value: Any) -> bytes:
    _reject_floats(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_file_bytes(value: Any) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoverageContractError(f"cannot read JSON receipt {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CoverageContractError(f"JSON receipt is not an object: {path}")
    return value


def _reject_floats(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise CoverageContractError(
            f"floats are forbidden in canonical report at {path}"
        )
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise CoverageContractError(f"non-string JSON key at {path}")
            _reject_floats(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_floats(child, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (bool, int, str)):
        raise CoverageContractError(
            f"unsupported canonical report value {type(value).__name__} at {path}"
        )


def write_canonical(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_file_bytes(value))


def normalize_distribution_name(name: str) -> str:
    normalized = re.sub(r"[-_.]+", "-", name).lower()
    if not NORMALIZED_NAME.fullmatch(normalized):
        raise CoverageContractError(f"invalid distribution name: {name!r}")
    return normalized


def normalize_distribution_version(version: str) -> str:
    normalized = version.strip()
    if not normalized or not PEP440_VERSION.fullmatch(normalized):
        raise CoverageContractError(f"invalid distribution version: {version!r}")
    return normalized


def dependency_environment(repo_root: Path) -> dict[str, Any]:
    requirements: list[dict[str, str]] = []
    for relative in ("requirements.txt", "requirements-test.txt"):
        path = repo_root / relative
        if not path.is_file():
            raise CoverageContractError(
                f"required dependency declaration missing: {relative}"
            )
        requirements.append({"path": relative, "sha256": sha256_file(path)})

    distributions: list[dict[str, str]] = []
    for distribution in importlib.metadata.distributions():
        raw_name = distribution.metadata.get("Name")
        raw_version = distribution.version
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise CoverageContractError(
                "installed distribution has no valid Name metadata"
            )
        if not isinstance(raw_version, str) or not raw_version.strip():
            raise CoverageContractError(
                f"installed distribution {raw_name!r} has no valid version"
            )
        try:
            version = normalize_distribution_version(raw_version)
        except CoverageContractError as exc:
            raise CoverageContractError(
                f"installed distribution {raw_name!r} has invalid version {raw_version!r}"
            ) from exc
        entry = {
            "name": normalize_distribution_name(raw_name.strip()),
            "version": version,
        }
        direct_url = distribution.read_text("direct_url.json")
        if direct_url is not None:
            entry["direct_url_sha256"] = sha256_bytes(direct_url.encode("utf-8"))
        distributions.append(entry)

    distributions.sort(key=lambda item: (item["name"], item["version"]))
    duplicate_names = sorted(
        name
        for name, count in Counter(item["name"] for item in distributions).items()
        if count > 1
    )
    if duplicate_names:
        raise CoverageContractError(
            "duplicate normalized installed distributions: "
            + ", ".join(duplicate_names)
        )

    executable = Path(sys.executable).resolve(strict=True)
    executable_path_digest = sha256_bytes(str(executable).encode("utf-8"))
    interpreter = {
        "implementation": platform.python_implementation(),
        "full_version": sys.version,
        "abi": str(sysconfig.get_config_var("SOABI") or ""),
        "cache_tag": str(sys.implementation.cache_tag or ""),
        "executable_basename": executable.name,
        "executable_path_sha256": executable_path_digest,
        "executable_sha256": sha256_file(executable),
    }
    return {
        "requirements": requirements,
        "interpreter": interpreter,
        "distributions": distributions,
    }


def _git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise CoverageContractError(
            f"git {' '.join(args)} failed: {(completed.stderr or completed.stdout).strip()}"
        )
    return completed.stdout


def worktree_status(repo_root: Path) -> list[str]:
    return [
        line
        for line in _git(
            repo_root, "status", "--porcelain=v1", "--untracked-files=all"
        ).splitlines()
        if line
    ]


def relevant_file_identity(repo_root: Path) -> dict[str, Any]:
    raw = subprocess.run(
        ["git", "ls-files", "-z", "--", *RELEVANT_PATHS],
        cwd=repo_root,
        check=False,
        capture_output=True,
    )
    if raw.returncode != 0:
        raise CoverageContractError(
            f"git ls-files failed: {raw.stderr.decode('utf-8', errors='replace').strip()}"
        )
    paths = sorted(part.decode("utf-8") for part in raw.stdout.split(b"\0") if part)
    if not paths:
        raise CoverageContractError("relevant tracked-file identity is empty")
    files = []
    for relative in paths:
        path = repo_root / relative
        if not path.is_file():
            raise CoverageContractError(
                f"tracked relevant input is not a file: {relative}"
            )
        files.append({"path": relative, "sha256": sha256_file(path)})
    return {
        "files": files,
        "tree_sha256": sha256_bytes(canonical_json_bytes(files)),
    }


def source_identity(repo_root: Path, *, require_clean: bool) -> dict[str, Any]:
    status = worktree_status(repo_root)
    if require_clean and status:
        raise CoverageContractError(
            "canonical collection requires a clean worktree: " + "; ".join(status[:8])
        )
    return {
        "commit": _git(repo_root, "rev-parse", "HEAD").strip(),
        "clean": not status,
        "worktree_status": status,
        "relevant": relevant_file_identity(repo_root),
    }


def verify_source_freshness(
    repo_root: Path,
    recorded: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    subject = str(recorded.get("commit") or "")
    if not LOWER_HEX_40.fullmatch(subject):
        return False, ["recorded source commit is invalid"]
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", subject, "HEAD"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if ancestor.returncode != 0:
        reasons.append("recorded subject is not an ancestor of current HEAD")
    else:
        changed = [
            line
            for line in _git(
                repo_root, "diff", "--name-only", f"{subject}..HEAD"
            ).splitlines()
            if line
        ]
        outside = [path for path in changed if not path.startswith(EVIDENCE_PREFIX)]
        if outside:
            reasons.append(
                "non-evidence paths changed after subject: " + ", ".join(outside)
            )

    current_relevant = relevant_file_identity(repo_root)
    if current_relevant != recorded.get("relevant"):
        reasons.append("relevant source/config/tool identity changed")

    dirty_outside: list[str] = []
    for line in worktree_status(repo_root):
        path = line[3:].strip().strip('"') if len(line) >= 4 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if not path.startswith(EVIDENCE_PREFIX):
            dirty_outside.append(path)
    if dirty_outside:
        reasons.append(
            "non-evidence worktree changes present: " + ", ".join(dirty_outside)
        )
    return not reasons, reasons


def snapshot_repository_coverage(repo_root: Path) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for path in sorted(repo_root.glob(".coverage*")):
        stat = path.lstat()
        record: dict[str, Any] = {
            "name": path.name,
            "mode": stat.st_mode,
            "size": stat.st_size,
            "is_symlink": path.is_symlink(),
        }
        if path.is_file() and not path.is_symlink():
            record["sha256"] = sha256_file(path)
        elif path.is_symlink():
            record["target"] = os.readlink(path)
        snapshots.append(record)
    return snapshots


def parse_measurement_context(
    context: str,
    *,
    collection_id: str,
    logical_contexts: set[str],
) -> str:
    prefix = f"{MEASUREMENT_PREFIX}{collection_id}/"
    if not context.startswith(prefix):
        raise CoverageContractError(f"foreign measurement context: {context!r}")
    logical = context[len(prefix) :]
    if not logical or logical not in logical_contexts:
        raise CoverageContractError(f"unknown logical context in shard: {logical!r}")
    if context != f"{prefix}{logical}":
        raise CoverageContractError(f"lossy measurement context mapping: {context!r}")
    return logical


def canonical_source_path(filename: str, repo_root: Path, roots: Sequence[str]) -> str:
    candidate = Path(filename)
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (repo_root / candidate).resolve()
    )
    try:
        relative = resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise CoverageContractError(
            f"measured source escapes repository: {filename}"
        ) from exc
    if not any(relative == root or relative.startswith(f"{root}/") for root in roots):
        raise CoverageContractError(
            f"measured source is outside owned roots: {relative}"
        )
    return relative


def inspect_and_combine_shards(
    *,
    session_root: Path,
    repo_root: Path,
    collection_id: str,
    logical_contexts: set[str],
    owned_roots: Sequence[str],
) -> tuple[list[dict[str, Any]], Path]:
    raw_dir = session_root / "raw"
    shard_paths = sorted(path for path in raw_dir.glob(".coverage.*") if path.is_file())
    if not shard_paths:
        raise CoverageContractError("no parallel coverage shards were produced")

    inspections: list[tuple[Path, CoverageData, dict[str, Any]]] = []
    seen_hashes: set[str] = set()
    casefold_paths: dict[str, str] = {}
    for path in shard_paths:
        digest = sha256_file(path)
        if digest in seen_hashes:
            raise CoverageContractError(f"duplicate raw shard content: {digest}")
        seen_hashes.add(digest)
        data = CoverageData(basename=str(path))
        try:
            data.read()
        except Exception as exc:  # noqa: BLE001 - public API can raise backend errors
            raise CoverageContractError(
                f"unreadable coverage shard {digest}: {exc}"
            ) from exc
        contexts = data.measured_contexts()
        if len(contexts) != 1 or "" in contexts:
            raise CoverageContractError(
                f"shard {digest} must contain exactly one nonempty context: {sorted(contexts)!r}"
            )
        context = next(iter(contexts))
        logical = parse_measurement_context(
            context,
            collection_id=collection_id,
            logical_contexts=logical_contexts,
        )
        if not data.has_arcs():
            raise CoverageContractError(f"shard {digest} has no branch/arc data")
        measured_files = sorted(data.measured_files())
        if not measured_files:
            raise CoverageContractError(f"shard {digest} contains no owned execution")
        sources: list[str] = []
        for filename in measured_files:
            relative = canonical_source_path(filename, repo_root, owned_roots)
            folded = relative.casefold()
            prior = casefold_paths.setdefault(folded, relative)
            if prior != relative:
                raise CoverageContractError(
                    f"case-fold source collision: {prior!r} versus {relative!r}"
                )
            sources.append(relative)
        record = {
            "shard_sha256": digest,
            "measurement_context": context,
            "logical_context_id": logical,
            "readable": True,
            "branch_arcs": True,
            "measured_sources": sorted(set(sources)),
        }
        inspections.append((path, data, record))

    inspections.sort(key=lambda item: (item[2]["shard_sha256"], item[0].name))
    records: list[dict[str, Any]] = []
    combined_dir = session_root / "combined"
    combined_dir.mkdir(mode=0o700, exist_ok=False)
    combined_path = combined_dir / ".coverage"
    combined = CoverageData(basename=str(combined_path))
    for index, (_path, data, record) in enumerate(inspections):
        output = dict(record)
        output["shard_id"] = f"shard-{index:03d}-{record['shard_sha256'][:16]}"
        records.append(output)
        combined.update(data)
    combined.write()
    if not combined_path.is_file():
        raise CoverageContractError(
            "explicit combined coverage database was not written"
        )
    return records, combined_path


def extract_context_execution(
    *,
    combined_path: Path,
    repo_root: Path,
    measurement_to_logical: Mapping[str, str],
    owned_roots: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, dict[str, set[Any]]]]]:
    data = CoverageData(basename=str(combined_path))
    data.read()
    files_by_path: dict[str, dict[str, Any]] = {}
    execution: dict[str, dict[str, dict[str, set[Any]]]] = defaultdict(dict)
    original_by_relative: dict[str, str] = {}
    for filename in sorted(data.measured_files()):
        relative = canonical_source_path(filename, repo_root, owned_roots)
        if (
            relative in original_by_relative
            and original_by_relative[relative] != filename
        ):
            raise CoverageContractError(f"source alias collision for {relative}")
        original_by_relative[relative] = filename

    for measurement, logical in sorted(
        measurement_to_logical.items(), key=lambda item: item[1]
    ):
        data.set_query_context(measurement)
        for relative, filename in sorted(original_by_relative.items()):
            lines = sorted(set(data.lines(filename) or []))
            arcs = sorted(set(data.arcs(filename) or []))
            if not lines and not arcs:
                continue
            execution[logical][relative] = {
                "lines": set(lines),
                "arcs": set(arcs),
            }
            file_record = files_by_path.setdefault(
                relative, {"path": relative, "contexts": []}
            )
            file_record["contexts"].append(
                {
                    "logical_context_id": logical,
                    "measurement_context": measurement,
                    "executed_lines": lines,
                    "executed_arcs": [[start, end] for start, end in arcs],
                }
            )
    data.set_query_contexts(None)
    files = []
    for relative in sorted(files_by_path):
        record = files_by_path[relative]
        record["contexts"].sort(key=lambda item: item["logical_context_id"])
        files.append(record)
    return files, execution


def bootstrap_comparison(
    execution: Mapping[str, Mapping[str, Mapping[str, set[Any]]]],
    *,
    bootstrap_logical_id: str,
) -> dict[str, Any]:
    bootstrap = execution.get(bootstrap_logical_id, {})
    contexts: list[dict[str, Any]] = []
    for logical in sorted(execution):
        if logical == bootstrap_logical_id:
            continue
        paths = sorted(set(bootstrap) | set(execution[logical]))
        files: list[dict[str, Any]] = []
        for path in paths:
            base = bootstrap.get(path, {"lines": set(), "arcs": set()})
            raw = execution[logical].get(path, {"lines": set(), "arcs": set()})
            raw_lines = set(raw["lines"])
            base_lines = set(base["lines"])
            raw_arcs = set(raw["arcs"])
            base_arcs = set(base["arcs"])
            files.append(
                {
                    "path": path,
                    "shared_with_bootstrap_lines": sorted(raw_lines & base_lines),
                    "command_specific_lines": sorted(raw_lines - base_lines),
                    "bootstrap_only_lines": sorted(base_lines - raw_lines),
                    "shared_with_bootstrap_arcs": [
                        list(arc) for arc in sorted(raw_arcs & base_arcs)
                    ],
                    "command_specific_arcs": [
                        list(arc) for arc in sorted(raw_arcs - base_arcs)
                    ],
                    "bootstrap_only_arcs": [
                        list(arc) for arc in sorted(base_arcs - raw_arcs)
                    ],
                }
            )
        contexts.append({"logical_context_id": logical, "files": files})
    return {"bootstrap_logical_context_id": bootstrap_logical_id, "contexts": contexts}


def function_body_range(path: Path, function_name: str) -> tuple[int, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    if len(matches) != 1 or matches[0].end_lineno is None:
        raise CoverageContractError(
            f"cannot identify unique worker probe function {function_name!r} in {path}"
        )
    return matches[0].lineno + 1, matches[0].end_lineno


def validate_worker_execution(
    *,
    commands: Sequence[Mapping[str, Any]],
    shards: Sequence[Mapping[str, Any]],
    execution: Mapping[str, Mapping[str, Mapping[str, set[Any]]]],
    repo_root: Path,
    worker_probe: Mapping[str, Any],
) -> list[dict[str, Any]]:
    path = str(worker_probe.get("path") or "")
    function_name = str(worker_probe.get("function") or "")
    start, end = function_body_range(repo_root / path, function_name)
    shard_counts = Counter(
        str(shard.get("logical_context_id") or "") for shard in shards
    )
    results: list[dict[str, Any]] = []
    for command in commands:
        if command.get("expects_background_worker") is not True:
            continue
        logical = str(command.get("logical_context_id") or "")
        executed_lines = set(
            (execution.get(logical, {}).get(path) or {}).get("lines", set())
        )
        worker_lines = sorted(line for line in executed_lines if start <= line <= end)
        count = shard_counts.get(logical, 0)
        ok = count >= 2 and bool(worker_lines)
        results.append(
            {
                "logical_context_id": logical,
                "shard_count": count,
                "worker_probe_path": path,
                "worker_probe_function": function_name,
                "worker_only_executed_lines": worker_lines,
                "complete": ok,
            }
        )
    return results


def report_digest(report: Mapping[str, Any]) -> str:
    projection = json.loads(json.dumps(report))
    integrity = projection.get("integrity")
    if not isinstance(integrity, dict):
        raise CoverageContractError("report integrity section is missing")
    integrity.pop("report_sha256", None)
    return sha256_bytes(canonical_json_bytes(projection))


def finalize_report_digest(report: dict[str, Any]) -> dict[str, Any]:
    integrity = report.setdefault("integrity", {})
    if not isinstance(integrity, dict):
        raise CoverageContractError("report integrity section is not an object")
    integrity.pop("report_sha256", None)
    integrity["report_sha256"] = report_digest(report)
    return report


def verify_canonical_report(
    report_path: Path,
    *,
    repo_root: Path,
) -> dict[str, Any]:
    raw = report_path.read_bytes()
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise CoverageContractError("canonical report must end with exactly one LF")
    try:
        report = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoverageContractError(
            f"report is not canonical UTF-8 JSON: {exc}"
        ) from exc
    if not isinstance(report, dict) or report.get("schema") != REPORT_SCHEMA:
        raise CoverageContractError(f"report schema must be {REPORT_SCHEMA}")
    required = {
        "result",
        "subject",
        "inputs",
        "dependency_environment",
        "commands",
        "process_completeness",
        "contexts",
        "files",
        "bootstrap_comparison",
        "aggregates",
        "integrity",
        "non_claims",
    }
    missing = sorted(required - set(report))
    if missing:
        raise CoverageContractError("report missing sections: " + ", ".join(missing))
    if canonical_file_bytes(report) != raw:
        raise CoverageContractError(
            "report bytes do not match canonical JSON serialization"
        )
    recorded_digest = (report.get("integrity") or {}).get("report_sha256")
    if not isinstance(recorded_digest, str) or not LOWER_HEX_64.fullmatch(
        recorded_digest
    ):
        raise CoverageContractError("report_sha256 is not strict lowercase SHA-256 hex")
    actual_digest = report_digest(report)
    if actual_digest != recorded_digest:
        raise CoverageContractError(
            f"report digest mismatch: recorded {recorded_digest}, computed {actual_digest}"
        )
    non_claims = report.get("non_claims")
    expected_non_claims = {
        "behavioral_correctness": False,
        "dead_code": False,
        "production_value": False,
        "numeric_coverage_gate": False,
    }
    if non_claims != expected_non_claims:
        raise CoverageContractError(
            "report non_claims must contain the four exact false values"
        )

    dependency_now = dependency_environment(repo_root)
    if dependency_now != report.get("dependency_environment"):
        raise CoverageContractError("dependency environment changed after collection")
    source_ok, source_reasons = verify_source_freshness(
        repo_root,
        (report.get("subject") or {}).get("source_identity") or {},
    )
    if not source_ok:
        raise CoverageContractError(
            "source freshness failed: " + "; ".join(source_reasons)
        )
    return report
