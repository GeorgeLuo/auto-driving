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
import stat
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


def sha256_regular_file(path: Path, *, root: Path) -> str:
    """Hash one contained regular file without following a final symlink."""

    root_resolved = root.resolve(strict=True)
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise CoverageContractError(f"owned root is missing: {root}") from exc
    if root.is_symlink() or not stat.S_ISDIR(root_stat.st_mode):
        raise CoverageContractError(f"owned root is not a no-follow directory: {root}")
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise CoverageContractError(f"file escapes owned root: {path}") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise CoverageContractError(f"invalid owned relative path: {path}")
    ancestor = root
    for part in relative.parts[:-1]:
        ancestor = ancestor / part
        try:
            ancestor_stat = ancestor.lstat()
        except OSError as exc:
            raise CoverageContractError(f"file ancestor is missing: {path}") from exc
        if ancestor.is_symlink() or not stat.S_ISDIR(ancestor_stat.st_mode):
            raise CoverageContractError(
                f"file has a non-directory or symlink ancestor: {path}"
            )
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise CoverageContractError(f"file parent is missing: {path}") from exc
    try:
        parent.relative_to(root_resolved)
    except ValueError as exc:
        raise CoverageContractError(f"file escapes owned root: {path}") from exc
    try:
        before = path.lstat()
    except OSError as exc:
        raise CoverageContractError(f"sealed regular file is missing: {path}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise CoverageContractError(f"file is not a regular no-follow input: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise CoverageContractError(
            f"cannot open sealed regular file {path}: {exc}"
        ) from exc
    digest = hashlib.sha256()
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (before.st_dev, before.st_ino):
            raise CoverageContractError(f"file identity changed before hashing: {path}")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    finally:
        os.close(fd)
    after = path.lstat()
    identity_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    if any(
        getattr(before, field) != getattr(after, field) for field in identity_fields
    ):
        raise CoverageContractError(f"file changed while hashing: {path}")
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
    raw_stat = raw_dir.lstat()
    if not stat.S_ISDIR(raw_stat.st_mode) or raw_dir.is_symlink():
        raise CoverageContractError(
            "raw coverage root is not a session-owned directory"
        )
    if raw_dir.resolve(strict=True).parent != session_root.resolve(strict=True):
        raise CoverageContractError("raw coverage root escapes the session")
    shard_paths: list[Path] = []
    for path in sorted(raw_dir.iterdir(), key=lambda candidate: candidate.name):
        if not path.name.startswith(".coverage."):
            continue
        candidate_stat = path.lstat()
        if not stat.S_ISREG(candidate_stat.st_mode):
            raise CoverageContractError(
                f"raw coverage shard is not a regular no-follow input: {path.name}"
            )
        shard_paths.append(path)
    if not shard_paths:
        raise CoverageContractError("no parallel coverage shards were produced")

    inspections: list[tuple[Path, CoverageData, dict[str, Any]]] = []
    seen_hashes: set[str] = set()
    casefold_paths: dict[str, str] = {}
    for path in shard_paths:
        digest = sha256_regular_file(path, root=raw_dir)
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
            "raw_session_path": path.relative_to(session_root).as_posix(),
            "measurement_context": context,
            "logical_context_id": logical,
            "readable": True,
            "branch_arcs": True,
            "measured_sources": sorted(set(sources)),
        }
        if sha256_regular_file(path, root=raw_dir) != digest:
            raise CoverageContractError(
                f"raw coverage shard changed during inspection: {path.name}"
            )
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


def union_execution(
    execution: Mapping[str, Mapping[str, Mapping[str, set[Any]]]],
    logical_context_ids: Sequence[str],
) -> dict[str, dict[str, set[Any]]]:
    union: dict[str, dict[str, set[Any]]] = {}
    for logical in sorted(set(logical_context_ids)):
        for path, values in execution.get(logical, {}).items():
            target = union.setdefault(path, {"lines": set(), "arcs": set()})
            target["lines"].update(values.get("lines", set()))
            target["arcs"].update(values.get("arcs", set()))
    return union


def execution_files(
    execution: Mapping[str, Mapping[str, set[Any]]],
) -> list[dict[str, Any]]:
    return [
        {
            "path": path,
            "executed_lines": sorted(values.get("lines", set())),
            "executed_arcs": [list(arc) for arc in sorted(values.get("arcs", set()))],
        }
        for path, values in sorted(execution.items())
    ]


def journey_context_groups(
    commands: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for command in commands:
        if command.get("role") != "journey_command":
            continue
        group_id = str(command.get("family_id") or "primary")
        grouped[group_id].append(str(command.get("logical_context_id") or ""))
    return [
        {
            "journey_id": group_id,
            "logical_context_ids": sorted(set(logical_ids)),
        }
        for group_id, logical_ids in sorted(grouped.items())
    ]


def _compare_with_bootstrap(
    raw_execution: Mapping[str, Mapping[str, set[Any]]],
    bootstrap: Mapping[str, Mapping[str, set[Any]]],
) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(set(bootstrap) | set(raw_execution)):
        base = bootstrap.get(path, {"lines": set(), "arcs": set()})
        raw = raw_execution.get(path, {"lines": set(), "arcs": set()})
        raw_lines = set(raw.get("lines", set()))
        base_lines = set(base.get("lines", set()))
        raw_arcs = set(raw.get("arcs", set()))
        base_arcs = set(base.get("arcs", set()))
        files.append(
            {
                "path": path,
                "raw_lines": sorted(raw_lines),
                "raw_arcs": [list(arc) for arc in sorted(raw_arcs)],
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
    return files


def bootstrap_comparison(
    execution: Mapping[str, Mapping[str, Mapping[str, set[Any]]]],
    *,
    bootstrap_logical_id: str,
    commands: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    bootstrap = execution.get(bootstrap_logical_id, {})
    command_rows = [
        {
            "logical_context_id": logical,
            "files": _compare_with_bootstrap(execution[logical], bootstrap),
        }
        for logical in sorted(execution)
        if logical != bootstrap_logical_id
    ]
    journey_rows = []
    for group in journey_context_groups(commands):
        raw = union_execution(execution, group["logical_context_ids"])
        journey_rows.append(
            {
                **group,
                "files": _compare_with_bootstrap(raw, bootstrap),
            }
        )
    return {
        "bootstrap_logical_context_id": bootstrap_logical_id,
        "commands": command_rows,
        "journeys": journey_rows,
    }


def aggregate_rollups(
    execution: Mapping[str, Mapping[str, Mapping[str, set[Any]]]],
    commands: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    groups: list[tuple[str, str, list[str]]] = []
    for group in journey_context_groups(commands):
        groups.append(
            (
                "journey",
                str(group["journey_id"]),
                list(group["logical_context_ids"]),
            )
        )
    support_roles = {"supplemental_capture", "precondition"}
    groups.extend(
        [
            (
                "support",
                "support",
                [
                    str(command.get("logical_context_id") or "")
                    for command in commands
                    if command.get("role") in support_roles
                ],
            ),
            (
                "cleanup",
                "cleanup",
                [
                    str(command.get("logical_context_id") or "")
                    for command in commands
                    if command.get("role") == "cleanup"
                ],
            ),
            (
                "all_contexts",
                "all_contexts",
                [str(command.get("logical_context_id") or "") for command in commands],
            ),
        ]
    )
    rollups: list[dict[str, Any]] = []
    for kind, group_id, logical_ids in groups:
        stable_ids = sorted(set(logical_ids))
        union = union_execution(execution, stable_ids)
        files = execution_files(union)
        rollups.append(
            {
                "kind": kind,
                "id": group_id,
                "logical_context_ids": stable_ids,
                "contexts": len(stable_ids),
                "files": files,
                "file_count": len(files),
                "executed_lines": sum(len(item["executed_lines"]) for item in files),
                "executed_arcs": sum(len(item["executed_arcs"]) for item in files),
            }
        )
    return rollups


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
    worker_lifecycles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    def canonical_digest_list(value: Any) -> bool:
        return (
            isinstance(value, list)
            and all(
                isinstance(item, str) and LOWER_HEX_64.fullmatch(item) for item in value
            )
            and value == sorted(set(value))
        )

    def is_worker_status(command: Mapping[str, Any]) -> bool:
        argv = list(command.get("resolved_argv") or [])
        try:
            vehicles = argv.index("vehicles")
        except ValueError:
            return False
        tail = argv[vehicles + 1 :]
        return tail[:1] == ["status"] or tail[:2] == ["automation", "status"]

    path = str(worker_probe.get("path") or "")
    function_name = str(worker_probe.get("function") or "")
    start, end = function_body_range(repo_root / path, function_name)
    shards_by_logical: dict[str, set[str]] = defaultdict(set)
    for shard in shards:
        logical = str(shard.get("logical_context_id") or "")
        digest = str(shard.get("shard_sha256") or "")
        if LOWER_HEX_64.fullmatch(digest):
            shards_by_logical[logical].add(digest)
    lifecycles_by_logical: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for lifecycle in worker_lifecycles:
        launch = lifecycle.get("launch")
        if isinstance(launch, Mapping):
            lifecycles_by_logical[str(launch.get("logical_context_id") or "")].append(
                lifecycle
            )
    results: list[dict[str, Any]] = []
    for command_index, command in enumerate(commands):
        if command.get("expects_background_worker") is not True:
            continue
        logical = str(command.get("logical_context_id") or "")
        executed_lines = set(
            (execution.get(logical, {}).get(path) or {}).get("lines", set())
        )
        worker_lines = sorted(line for line in executed_lines if start <= line <= end)
        final_shards = shards_by_logical.get(logical, set())
        foreground_values = command.get("new_shard_sha256_visible_at_return")
        foreground_shards = {
            str(value)
            for value in foreground_values or []
            if LOWER_HEX_64.fullmatch(str(value))
        }
        candidates = lifecycles_by_logical.get(logical, [])
        lifecycle = candidates[0] if len(candidates) == 1 else {}
        launch = lifecycle.get("launch") if isinstance(lifecycle, Mapping) else {}
        launch = launch if isinstance(launch, Mapping) else {}
        launch_visible_values = launch.get("raw_shard_sha256_visible")
        launch_visible = {
            str(value)
            for value in launch_visible_values or []
            if LOWER_HEX_64.fullmatch(str(value))
        }
        observations = lifecycle.get("observations") if lifecycle else []
        observations = observations if isinstance(observations, list) else []
        expected_command = {
            "catalog_id": str(command.get("catalog_id") or ""),
            "role": str(command.get("role") or ""),
            "step_id": str(command.get("step_id") or ""),
            "command_ordinal": command.get("command_ordinal"),
        }
        measurement = str(command.get("measurement_context") or "")
        same_generation = [
            item
            for item in observations
            if isinstance(item, Mapping)
            and item.get("same_generation") is True
            and item.get("pid") == launch.get("pid")
            and item.get("run_id") == launch.get("run_id")
            and item.get("generation_matches") is True
            and item.get("launch_command") == expected_command
            and item.get("logical_context_id") == logical
            and item.get("measurement_context") == measurement
        ]
        terminal = [
            item
            for item in same_generation
            if item.get("kind") in {"termination", "terminal_status"}
            and item.get("pid_alive") is False
            and item.get("status") in {"stopped", "completed"}
        ]
        terminal_visible = {
            str(value)
            for item in terminal
            for value in item.get("raw_shard_sha256_visible") or []
            if LOWER_HEX_64.fullmatch(str(value))
        }
        post_termination_shards = sorted(
            (final_shards - launch_visible) & terminal_visible
        )
        later_catalog_commands: list[Mapping[str, Any]] = []
        for later in commands[command_index + 1 :]:
            if later.get("catalog_id") != command.get("catalog_id"):
                continue
            if later.get("expects_background_worker") is True:
                break
            later_catalog_commands.append(later)
        status_required = any(
            is_worker_status(later) for later in later_catalog_commands
        )
        status_observed = any(
            item.get("kind") in {"status", "terminal_status"}
            for item in same_generation
        )
        lifecycle_checks = {
            "single_lifecycle": len(candidates) == 1,
            "lifecycle_receipts_canonical": (
                canonical_digest_list(foreground_values)
                and canonical_digest_list(launch_visible_values)
                and isinstance(observations, list)
                and all(
                    isinstance(item, Mapping)
                    and item.get("kind") in {"status", "termination", "terminal_status"}
                    and canonical_digest_list(item.get("raw_shard_sha256_visible"))
                    for item in observations
                )
            ),
            "launch_context_bound": (
                launch.get("command") == expected_command
                and launch.get("logical_context_id") == logical
                and launch.get("measurement_context") == measurement
            ),
            "launch_identity": (
                lifecycle.get("schema") == "m007_cli_coverage_worker_lifecycle_v1"
                and isinstance(launch.get("pid"), int)
                and int(launch.get("pid")) > 0
                and isinstance(launch.get("run_id"), str)
                and bool(launch.get("run_id"))
                and launch.get("generation_matches") is True
                and launch.get("stdout_pid_matches") is True
                and launch.get("pid_alive") is True
            ),
            "foreground_shard_bound": bool(foreground_shards)
            and foreground_shards <= final_shards
            and foreground_shards <= launch_visible,
            "later_same_generation_observed": bool(same_generation),
            "required_status_observed": not status_required or status_observed,
            "terminal_death_observed": bool(terminal),
            "distinct_post_termination_shard": bool(post_termination_shards)
            and not foreground_shards.intersection(post_termination_shards),
            "worker_code_executed": bool(worker_lines),
        }
        results.append(
            {
                "logical_context_id": logical,
                "shard_count": len(final_shards),
                "foreground_shard_sha256": sorted(foreground_shards),
                "post_termination_shard_sha256": post_termination_shards,
                "launch_pid": launch.get("pid"),
                "launch_run_id": launch.get("run_id"),
                "same_generation_observations": len(same_generation),
                "later_status_required": status_required,
                "later_status_observed": status_observed,
                "terminal_observations": len(terminal),
                "worker_probe_path": path,
                "worker_probe_function": function_name,
                "worker_only_executed_lines": worker_lines,
                "checks": lifecycle_checks,
                "complete": all(lifecycle_checks.values()),
            }
        )
    return results


def _report_execution(
    files: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, dict[str, set[Any]]]]:
    """Rebuild the execution sets used to derive every report summary."""

    execution: dict[str, dict[str, dict[str, set[Any]]]] = defaultdict(dict)
    seen_paths: set[str] = set()
    for file_record in files:
        if not isinstance(file_record, Mapping):
            raise CoverageContractError("report file record is not an object")
        path = str(file_record.get("path") or "")
        if not path or path.startswith("/") or path in seen_paths:
            raise CoverageContractError(
                f"report source path is invalid or duplicate: {path!r}"
            )
        seen_paths.add(path)
        contexts = file_record.get("contexts")
        if not isinstance(contexts, list):
            raise CoverageContractError(f"report source contexts are malformed: {path}")
        seen_contexts: set[str] = set()
        for context in contexts:
            if not isinstance(context, Mapping):
                raise CoverageContractError(
                    f"report source context is malformed: {path}"
                )
            logical = str(context.get("logical_context_id") or "")
            if not logical or logical in seen_contexts:
                raise CoverageContractError(
                    f"report source context is invalid or duplicate: {path}:{logical}"
                )
            seen_contexts.add(logical)
            lines = context.get("executed_lines")
            arcs = context.get("executed_arcs")
            if (
                not isinstance(lines, list)
                or any(not isinstance(line, int) for line in lines)
                or lines != sorted(set(lines))
                or not isinstance(arcs, list)
                or any(
                    not isinstance(arc, list)
                    or len(arc) != 2
                    or any(not isinstance(value, int) for value in arc)
                    for arc in arcs
                )
            ):
                raise CoverageContractError(
                    f"report execution values are malformed: {path}:{logical}"
                )
            arc_tuples = [tuple(arc) for arc in arcs]
            if arc_tuples != sorted(set(arc_tuples)):
                raise CoverageContractError(
                    f"report executed arcs are not unique and sorted: {path}:{logical}"
                )
            execution[logical][path] = {
                "lines": set(lines),
                "arcs": set(arc_tuples),
            }
    return execution


def _reject_local_absolute_paths(value: Any, path: str = "$") -> None:
    if isinstance(value, str):
        if (
            value.startswith("/")
            or value.startswith("file:/")
            or re.search(r"(?:^|[= \t])/(?!/)", value)
            or re.search(r"(?:^|[= \t])[A-Za-z]:[\\/]", value)
        ):
            raise CoverageContractError(
                f"canonical evidence contains a local absolute path at {path}"
            )
    elif isinstance(value, Mapping):
        for key, child in value.items():
            _reject_local_absolute_paths(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_local_absolute_paths(child, f"{path}[{index}]")


def _flatten_worker_lifecycles(
    runner_results: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    lifecycles: list[Mapping[str, Any]] = []
    for result in runner_results:
        values = result.get("worker_lifecycles")
        if not isinstance(values, list):
            raise CoverageContractError("runner worker lifecycle receipt is malformed")
        if any(not isinstance(value, Mapping) for value in values):
            raise CoverageContractError("runner worker lifecycle entry is malformed")
        lifecycles.extend(values)
    return lifecycles


def expected_contract_from_expanded(expanded: Mapping[str, Any]) -> dict[str, Any]:
    """Project the accepted expanded manifest into the verifier contract.

    Required manifest commands must appear in the report. Optional support
    templates may be present only when they match a registered expansion row.
    Worker expectations are owned by the expansion, never by mutable report flags.
    """

    commands = expanded.get("commands")
    catalogs = expanded.get("catalogs")
    if not isinstance(commands, list) or not isinstance(catalogs, list):
        raise CoverageContractError("expanded manifest contract is malformed")
    registered_commands = [
        command for command in commands if isinstance(command, Mapping)
    ]
    required_commands = [
        command
        for command in registered_commands
        if command.get("required") is True
        or command.get("role")
        in {"bootstrap", "journey_command", "supplemental_capture"}
    ]
    # Config probe is required even though role is precondition.
    for command in registered_commands:
        if (
            command.get("catalog_id") == "_collector"
            and command.get("step_id") == "_config_probe"
            and command not in required_commands
        ):
            required_commands.append(command)

    def _key(command: Mapping[str, Any]) -> tuple[str, str, str, int]:
        return (
            str(command.get("catalog_id") or ""),
            str(command.get("role") or ""),
            str(command.get("step_id") or ""),
            int(command.get("command_ordinal")),
        )

    # Required order is collection order, not raw expansion append order:
    # measured config probe, bootstrap, then catalog commands in expansion order.
    collector_required = [
        command
        for command in required_commands
        if command.get("catalog_id") == "_collector"
    ]
    catalog_required = [
        command
        for command in required_commands
        if command.get("catalog_id") != "_collector"
    ]

    def _collector_rank(command: Mapping[str, Any]) -> int:
        step = str(command.get("step_id") or "")
        if step == "_config_probe":
            return 0
        if step == "_bootstrap":
            return 1
        return 2

    ordered_required = (
        sorted(collector_required, key=_collector_rank) + catalog_required
    )
    required_keys = [_key(command) for command in ordered_required]
    required_logical = [
        str(command.get("logical_context_id") or "") for command in ordered_required
    ]
    registered_keys = [_key(command) for command in registered_commands]
    worker_ids = sorted(
        str(command.get("logical_context_id") or "")
        for command in registered_commands
        if command.get("expects_background_worker") is True
    )
    offline_ids = sorted(
        str(command.get("logical_context_id") or "")
        for command in registered_commands
        if command.get("family_id") == "continuity.offline_perception"
        and command.get("role") == "journey_command"
    )
    catalog_ids = sorted(
        str(item.get("id") or "") for item in catalogs if isinstance(item, Mapping)
    )
    catalog_records = sorted(
        [
            {
                "id": str(item.get("id") or ""),
                "path": str(item.get("path") or ""),
                "sha256": str(item.get("sha256") or ""),
            }
            for item in catalogs
            if isinstance(item, Mapping)
        ],
        key=lambda item: item["id"],
    )
    worker_probe = expanded.get("worker_probe")
    if not isinstance(worker_probe, Mapping):
        raise CoverageContractError("expanded manifest worker probe is malformed")

    def _tokenized_argv(argv: Any) -> list[str]:
        if not isinstance(argv, list) or any(not isinstance(part, str) for part in argv):
            raise CoverageContractError("manifest argv template is malformed")
        tokenized: list[str] = []
        for part in argv:
            try:
                if Path(part).resolve(strict=True) == Path(sys.executable).resolve(
                    strict=True
                ):
                    tokenized.append("$PYTHON")
                    continue
            except (OSError, RuntimeError):
                pass
            tokenized.append(part)
        return tokenized

    return {
        "manifest_path": str(expanded.get("manifest_path") or ""),
        "manifest_sha256": str(expanded.get("manifest_sha256") or ""),
        "catalogs": catalog_records,
        "catalog_ids": catalog_ids,
        "required_command_keys": required_keys,
        "required_logical_context_ids": required_logical,
        "registered_command_keys": registered_keys,
        "worker_logical_ids": worker_ids,
        "offline_journey_logical_ids": offline_ids,
        "bootstrap_logical_context_id": str(
            expanded.get("bootstrap_logical_context_id") or ""
        ),
        "worker_probe": {
            "path": str(worker_probe.get("path") or ""),
            "function": str(worker_probe.get("function") or ""),
        },
        "command_expectations": {
            _key(command): {
                "logical_context_id": str(command.get("logical_context_id") or ""),
                "expects_background_worker": command.get("expects_background_worker")
                is True,
                "expected_exit": int(command.get("expected_exit", 0)),
                "family_id": command.get("family_id"),
                "argv_template": _tokenized_argv(command.get("argv_template")),
                "required": command.get("required") is True
                or command.get("role")
                in {"bootstrap", "journey_command", "supplemental_capture"}
                or (
                    command.get("catalog_id") == "_collector"
                    and command.get("step_id") == "_config_probe"
                ),
            }
            for command in registered_commands
        },
    }


def sealed_offline_lineage_content(
    raw_lineage: Mapping[str, Any],
    *,
    source_identity: str,
) -> dict[str, Any]:
    """Return a path-tokenized raw-lineage projection safe for canonical evidence."""

    if raw_lineage.get("ok") is not True or raw_lineage.get("schema") != (
        "continuity_source_lineage_v1"
    ):
        raise CoverageContractError(
            "offline source lineage is missing or unsuccessful"
        )
    if not source_identity.startswith("$REPO"):
        raise CoverageContractError(
            "sealed offline lineage source identity must be repository-tokenized"
        )
    frames = raw_lineage.get("frames")
    if not isinstance(frames, list):
        raise CoverageContractError("offline source lineage frames are malformed")
    return {
        "schema": "continuity_source_lineage_v1",
        "ok": True,
        "src_dir": source_identity,
        "src_dir_redacted": source_identity,
        "manifest_sha256": str(raw_lineage.get("manifest_sha256") or ""),
        "ordered_input_sha256": str(raw_lineage.get("ordered_input_sha256") or ""),
        "frame_count": raw_lineage.get("frame_count"),
        "frames": frames,
    }


def derive_offline_lineage_identity(
    raw_lineage: Mapping[str, Any],
    *,
    catalog_id: str,
    repo_root: Path,
) -> dict[str, Any]:
    """Recompute the promoted offline lineage identity from sealed raw content."""

    if raw_lineage.get("ok") is not True or raw_lineage.get("schema") != (
        "continuity_source_lineage_v1"
    ):
        raise CoverageContractError(
            "offline source lineage is missing or unsuccessful"
        )
    manifest_sha = str(raw_lineage.get("manifest_sha256") or "")
    ordered_sha = str(raw_lineage.get("ordered_input_sha256") or "")
    if not LOWER_HEX_64.fullmatch(manifest_sha) or not LOWER_HEX_64.fullmatch(
        ordered_sha
    ):
        raise CoverageContractError(
            "offline source lineage digests are not canonical SHA-256 values"
        )
    frames = raw_lineage.get("frames")
    frame_count = raw_lineage.get("frame_count")
    if (
        not isinstance(frames, list)
        or not isinstance(frame_count, int)
        or frame_count <= 0
        or len(frames) != frame_count
        or any(
            not isinstance(frame, dict)
            or not LOWER_HEX_64.fullmatch(str(frame.get("sha256") or ""))
            for frame in frames
        )
    ):
        raise CoverageContractError(
            "offline source lineage frame receipt is malformed"
        )
    source_text = str(
        raw_lineage.get("src_dir_redacted") or raw_lineage.get("src_dir") or ""
    )
    repo_text = str(repo_root.resolve())
    if source_text == repo_text or source_text == "<repo>":
        normalized_source = "$REPO"
    elif source_text.startswith(repo_text + os.sep):
        normalized_source = (
            "$REPO/" + Path(source_text).relative_to(repo_root.resolve()).as_posix()
        )
    elif source_text.startswith("<repo>/"):
        normalized_source = "$REPO/" + source_text[len("<repo>/") :]
    elif source_text.startswith("$REPO/"):
        normalized_source = source_text
    else:
        raise CoverageContractError(
            "offline source lineage path is not repository-owned"
        )
    return {
        "schema": "m007_cli_coverage_offline_lineage_v1",
        "catalog_id": catalog_id,
        "source_identity": normalized_source,
        "manifest_sha256": manifest_sha,
        "ordered_input_sha256": ordered_sha,
        "frame_count": frame_count,
        "frame_receipt_sha256": sha256_bytes(canonical_json_bytes(frames)),
    }


def _reconcile_manifest_authority(
    report: Mapping[str, Any],
    *,
    repo_root: Path,
    expected_contract: Mapping[str, Any],
) -> None:
    """Bind report input identities to the accepted manifest contract."""

    inputs = report.get("inputs")
    subject = report.get("subject")
    if not isinstance(inputs, Mapping) or not isinstance(subject, Mapping):
        raise CoverageContractError("report inputs/subject are malformed")
    manifest_meta = inputs.get("manifest")
    if not isinstance(manifest_meta, Mapping):
        raise CoverageContractError("report manifest identity is malformed")
    path = str(manifest_meta.get("path") or "")
    digest = str(manifest_meta.get("sha256") or "")
    if path != expected_contract.get("manifest_path"):
        raise CoverageContractError(
            "report manifest path contradicts accepted manifest authority"
        )
    if digest != expected_contract.get("manifest_sha256"):
        raise CoverageContractError(
            "report manifest digest contradicts accepted manifest authority"
        )
    if not LOWER_HEX_64.fullmatch(digest):
        raise CoverageContractError("report manifest digest is not SHA-256 hex")
    live_path = repo_root / path
    if not live_path.is_file():
        raise CoverageContractError(f"accepted manifest is missing: {path}")
    live_digest = sha256_file(live_path)
    if live_digest != digest:
        raise CoverageContractError(
            "live manifest digest contradicts report and accepted authority"
        )
    relevant_map = inputs.get("relevant_file_sha256")
    if not isinstance(relevant_map, Mapping) or relevant_map.get(path) != digest:
        raise CoverageContractError(
            "inputs.relevant_file_sha256 does not bind the accepted manifest"
        )
    source_identity = subject.get("source_identity")
    if not isinstance(source_identity, Mapping):
        raise CoverageContractError("subject source identity is malformed")
    relevant = source_identity.get("relevant")
    if not isinstance(relevant, Mapping):
        raise CoverageContractError("subject relevant identity is malformed")
    files = relevant.get("files")
    if not isinstance(files, list):
        raise CoverageContractError("subject relevant file list is malformed")
    relevant_files = {
        str(item.get("path") or ""): str(item.get("sha256") or "")
        for item in files
        if isinstance(item, Mapping)
    }
    if relevant_files.get(path) != digest:
        raise CoverageContractError(
            "subject relevant identity does not bind the accepted manifest"
        )
    catalogs = inputs.get("catalogs")
    if not isinstance(catalogs, list):
        raise CoverageContractError("report catalog identity list is malformed")
    reported_catalogs = sorted(
        [
            {
                "id": str(item.get("id") or ""),
                "path": str(item.get("path") or ""),
                "sha256": str(item.get("sha256") or ""),
            }
            for item in catalogs
            if isinstance(item, Mapping)
        ],
        key=lambda item: item["id"],
    )
    if reported_catalogs != expected_contract.get("catalogs"):
        raise CoverageContractError(
            "report catalogs contradict accepted manifest authority"
        )
    for catalog in reported_catalogs:
        catalog_path = str(catalog["path"])
        catalog_digest = str(catalog["sha256"])
        if relevant_map.get(catalog_path) != catalog_digest:
            raise CoverageContractError(
                f"relevant_file_sha256 does not bind catalog {catalog['id']}"
            )
        if relevant_files.get(catalog_path) != catalog_digest:
            raise CoverageContractError(
                f"subject relevant identity does not bind catalog {catalog['id']}"
            )
        live_catalog = repo_root / catalog_path
        if not live_catalog.is_file() or sha256_file(live_catalog) != catalog_digest:
            raise CoverageContractError(
                f"live catalog digest contradicts accepted authority: {catalog_path}"
            )
    worker_probe = inputs.get("worker_probe")
    if worker_probe != expected_contract.get("worker_probe"):
        raise CoverageContractError(
            "worker probe contradicts accepted manifest authority"
        )


def validate_acceptance_semantics(
    *,
    claimed_result: str,
    reason_codes: Sequence[Any],
    commands: Sequence[Mapping[str, Any]],
    shards: Sequence[Mapping[str, Any]],
    worker_checks: Sequence[Mapping[str, Any]],
    cleanup: Mapping[str, Any],
    collection_checks: Mapping[str, Any],
    runner_results: Sequence[Mapping[str, Any]],
    offline_source_lineages: Sequence[Mapping[str, Any]],
    contexts: Mapping[str, Any],
    freshness: Mapping[str, Any],
    expected_contract: Mapping[str, Any],
    repo_root: Path,
) -> None:
    """Own pass/fail cross-field semantics at every acceptance boundary."""

    if claimed_result not in {"pass", "incomplete", "failed"}:
        raise CoverageContractError(f"invalid claimed result: {claimed_result!r}")
    logical_ids = [str(command.get("logical_context_id") or "") for command in commands]
    logical_id_set = set(logical_ids)
    command_keys = [
        (
            str(command.get("catalog_id") or ""),
            str(command.get("role") or ""),
            str(command.get("step_id") or ""),
            command.get("command_ordinal"),
        )
        for command in commands
    ]
    if (
        not logical_ids
        or any(not value for value in logical_ids)
        or len(logical_ids) != len(set(logical_ids))
        or len(command_keys) != len(set(command_keys))
        or any(
            command.get("role")
            not in {
                "bootstrap",
                "journey_command",
                "supplemental_capture",
                "precondition",
                "cleanup",
            }
            or type(command.get("command_ordinal")) is not int
            or int(command.get("command_ordinal")) < 0
            or type(command.get("expected_exit")) is not int
            or type(command.get("observed_exit")) is not int
            or not isinstance(command.get("argv_template"), list)
            or not isinstance(command.get("resolved_argv"), list)
            or any(
                not isinstance(value, str)
                for value in [
                    *(command.get("argv_template") or []),
                    *(command.get("resolved_argv") or []),
                ]
            )
            or command.get("normalized_working_directory") != "$REPO"
            for command in commands
        )
    ):
        raise CoverageContractError("command receipts are malformed or duplicate")

    required_keys = list(expected_contract.get("required_command_keys") or [])
    registered_keys = list(expected_contract.get("registered_command_keys") or [])
    registered_key_set = set(registered_keys)
    expected_workers = list(expected_contract.get("worker_logical_ids") or [])
    expected_offline = list(expected_contract.get("offline_journey_logical_ids") or [])
    expected_catalogs = list(expected_contract.get("catalog_ids") or [])
    expectations = expected_contract.get("command_expectations")
    if not isinstance(expectations, Mapping):
        raise CoverageContractError("expected command contract is malformed")
    observed_key_set = set(command_keys)
    missing_required = [key for key in required_keys if key not in observed_key_set]
    if missing_required:
        raise CoverageContractError(
            "command receipts omit required accepted-manifest commands"
        )
    if any(key not in registered_key_set for key in observed_key_set):
        raise CoverageContractError(
            "command receipts include commands absent from accepted manifest authority"
        )
    # Required commands must appear in expansion order (not merely as a set).
    observed_required_order = [key for key in command_keys if key in set(required_keys)]
    if observed_required_order != required_keys:
        raise CoverageContractError(
            "required command order contradicts accepted manifest expansion order"
        )
    for command in commands:
        key = (
            str(command.get("catalog_id") or ""),
            str(command.get("role") or ""),
            str(command.get("step_id") or ""),
            int(command.get("command_ordinal")),
        )
        expected_row = expectations.get(key)
        if not isinstance(expected_row, Mapping):
            raise CoverageContractError(
                f"command {key!r} is absent from accepted manifest authority"
            )
        if command.get("logical_context_id") != expected_row.get("logical_context_id"):
            raise CoverageContractError(
                f"command {key!r} logical context contradicts manifest authority"
            )
        if command.get("expects_background_worker") is not (
            expected_row.get("expects_background_worker") is True
        ):
            raise CoverageContractError(
                f"command {key!r} worker expectation contradicts manifest authority"
            )
        if command.get("expected_exit") != expected_row.get("expected_exit"):
            raise CoverageContractError(
                f"command {key!r} expected exit contradicts manifest authority"
            )
        if command.get("family_id") != expected_row.get("family_id"):
            raise CoverageContractError(
                f"command {key!r} family contradicts manifest authority"
            )
        expected_argv = list(expected_row.get("argv_template") or [])
        observed_argv = list(command.get("argv_template") or [])
        resolved_argv = list(command.get("resolved_argv") or [])
        is_config_probe = (
            command.get("catalog_id") == "_collector"
            and command.get("step_id") == "_config_probe"
        )
        if is_config_probe:
            # Expansion records a placeholder probe body; collection substitutes
            # the measured probe source while keeping the interpreter token.
            if (
                len(observed_argv) != 3
                or observed_argv[0] != "$PYTHON"
                or observed_argv[1] != "-c"
                or not observed_argv[2]
                or resolved_argv != observed_argv
            ):
                raise CoverageContractError(
                    f"command {key!r} config-probe argv contradicts accepted template shape"
                )
        else:
            if observed_argv != expected_argv:
                raise CoverageContractError(
                    f"command {key!r} argv template contradicts accepted manifest authority"
                )
            variables = command.get("variables")
            if variables is None:
                variables = {}
            if not isinstance(variables, Mapping) or any(
                not isinstance(name, str) or not isinstance(value, str)
                for name, value in variables.items()
            ):
                raise CoverageContractError(
                    f"command {key!r} substitution variables are malformed"
                )
            # Reconstruct resolved argv from the authoritative template plus the
            # receipt's recorded substitutions; refuse missing/unknown tokens.
            expected_resolved: list[str] = []
            for part in expected_argv:
                value = part
                for name, replacement in variables.items():
                    value = value.replace("{" + name + "}", replacement)
                if re.search(r"\{[a-zA-Z0-9_]+\}", value):
                    raise CoverageContractError(
                        f"command {key!r} has unresolved argv token after substitutions: {value!r}"
                    )
                expected_resolved.append(value)
            if resolved_argv != expected_resolved:
                raise CoverageContractError(
                    f"command {key!r} resolved argv contradicts template substitutions"
                )

    shard_ids: list[str] = []
    shard_hashes: list[str] = []
    shard_logical_ids: list[str] = []
    for shard in shards:
        shard_id = str(shard.get("shard_id") or "")
        digest = str(shard.get("shard_sha256") or "")
        logical = str(shard.get("logical_context_id") or "")
        if (
            not shard_id
            or not LOWER_HEX_64.fullmatch(digest)
            or logical not in logical_id_set
            or shard.get("readable") is not True
            or shard.get("branch_arcs") is not True
            or not isinstance(shard.get("measured_sources"), list)
            or not shard.get("measured_sources")
            or shard.get("measured_sources")
            != sorted(set(shard.get("measured_sources") or []))
            or any(
                not isinstance(source, str)
                or not source
                or source.startswith("/")
                or ".." in Path(source).parts
                for source in shard.get("measured_sources") or []
            )
        ):
            raise CoverageContractError(f"invalid shard receipt: {shard_id!r}")
        shard_ids.append(shard_id)
        shard_hashes.append(digest)
        shard_logical_ids.append(logical)
    if len(shard_ids) != len(set(shard_ids)) or len(shard_hashes) != len(
        set(shard_hashes)
    ):
        raise CoverageContractError("shard identities or content digests are duplicate")

    expected = sorted(logical_ids)
    observed = sorted(set(shard_logical_ids))
    if contexts.get("expected_logical_contexts") != expected:
        raise CoverageContractError(
            "expected logical-context summary contradicts commands"
        )
    if contexts.get("observed_logical_contexts") != observed:
        raise CoverageContractError(
            "observed logical-context summary contradicts shards"
        )
    if any(
        contexts.get(name) != []
        for name in ("empty_contexts", "foreign_contexts", "unknown_contexts")
    ):
        raise CoverageContractError("context anomaly summaries must be empty")
    collection_id = str(contexts.get("collection_id") or "")
    if not collection_id:
        raise CoverageContractError("collection identity is missing")
    if any(
        command.get("collection_id") != collection_id
        or command.get("measurement_context")
        != f"m007-run/{collection_id}/{command.get('logical_context_id')}"
        for command in commands
    ):
        raise CoverageContractError(
            "command measurement context contradicts collection"
        )
    if any(
        shard.get("measurement_context")
        != f"m007-run/{collection_id}/{shard.get('logical_context_id')}"
        for shard in shards
    ):
        raise CoverageContractError("shard measurement context contradicts collection")
    counts = Counter(shard_logical_ids)
    expected_rows = [
        {
            "logical_context_id": logical,
            "measurement_context": f"m007-run/{collection_id}/{logical}",
            "shard_count": counts.get(logical, 0),
        }
        for logical in expected
    ]
    if contexts.get("measurement_to_logical") != expected_rows:
        raise CoverageContractError(
            "measurement-context summary is not derived from shards"
        )

    # Worker expectations come only from the accepted manifest contract, never
    # from mutable report command flags alone.
    expected_worker_ids = list(expected_workers)
    observed_worker_ids = sorted(
        str(check.get("logical_context_id") or "") for check in worker_checks
    )
    if expected_worker_ids != observed_worker_ids:
        raise CoverageContractError(
            "worker checks do not exactly cover expected workers"
        )
    lifecycle_ids: list[str] = []
    for result in runner_results:
        lifecycles = result.get("worker_lifecycles")
        if not isinstance(lifecycles, list):
            raise CoverageContractError("runner worker lifecycle receipt is malformed")
        for lifecycle in lifecycles:
            launch = lifecycle.get("launch") if isinstance(lifecycle, Mapping) else None
            if not isinstance(launch, Mapping):
                raise CoverageContractError("worker lifecycle launch is malformed")
            lifecycle_ids.append(str(launch.get("logical_context_id") or ""))
    if sorted(lifecycle_ids) != expected_worker_ids:
        raise CoverageContractError(
            "worker lifecycles do not exactly cover expected launches"
        )

    observed_catalogs = sorted(
        str(result.get("catalog_id") or "") for result in runner_results
    )
    if expected_catalogs != observed_catalogs:
        raise CoverageContractError(
            "runner results do not exactly cover measured catalogs"
        )

    offline_commands = [
        command
        for command in commands
        if command.get("family_id") == "continuity.offline_perception"
        and command.get("role") == "journey_command"
    ]
    if (
        sorted(str(command.get("logical_context_id") or "") for command in offline_commands)
        != expected_offline
        or len(offline_commands) != 3
        or len(offline_source_lineages) != 1
    ):
        raise CoverageContractError("offline replay lineage is incomplete")
    lineage = offline_source_lineages[0]
    identity_keys = (
        "schema",
        "catalog_id",
        "source_identity",
        "manifest_sha256",
        "ordered_input_sha256",
        "frame_count",
        "frame_receipt_sha256",
    )
    identity = {key: lineage.get(key) for key in identity_keys}
    if (
        identity["schema"] != "m007_cli_coverage_offline_lineage_v1"
        or identity["catalog_id"] != "m007-continuity"
        or not str(identity["source_identity"] or "").startswith("$REPO/")
        or not LOWER_HEX_64.fullmatch(str(identity["manifest_sha256"] or ""))
        or not LOWER_HEX_64.fullmatch(str(identity["ordered_input_sha256"] or ""))
        or not LOWER_HEX_64.fullmatch(str(identity["frame_receipt_sha256"] or ""))
        or not isinstance(identity["frame_count"], int)
        or int(identity["frame_count"]) <= 0
    ):
        raise CoverageContractError("offline replay lineage identity is malformed")
    raw_lineage_receipt = lineage.get("raw_receipt")
    if (
        not isinstance(raw_lineage_receipt, Mapping)
        or raw_lineage_receipt.get("path")
        != "runner/m007-continuity/offline-source-lineage.json"
        or not LOWER_HEX_64.fullmatch(str(raw_lineage_receipt.get("sha256") or ""))
        or not isinstance(raw_lineage_receipt.get("content"), Mapping)
    ):
        raise CoverageContractError("offline replay raw lineage receipt is malformed")
    raw_content = raw_lineage_receipt["content"]
    if not isinstance(raw_content, Mapping):
        raise CoverageContractError("offline raw lineage content is malformed")
    if sha256_bytes(canonical_json_bytes(raw_content)) != raw_lineage_receipt.get(
        "sha256"
    ):
        raise CoverageContractError(
            "offline raw lineage content does not match sealed receipt hash"
        )
    derived_identity = derive_offline_lineage_identity(
        raw_content,
        catalog_id="m007-continuity",
        repo_root=repo_root,
    )
    if any(identity[key] != derived_identity[key] for key in identity_keys):
        raise CoverageContractError(
            "promoted offline lineage identity is not derived from sealed raw receipt"
        )
    for command in offline_commands:
        bound = command.get("offline_source_lineage")
        if not isinstance(bound, Mapping) or any(
            bound.get(key) != identity[key] for key in identity_keys
        ):
            raise CoverageContractError(
                "offline command lineage contradicts sealed identity"
            )
        expected_relation = (
            "produced" if command.get("step_id") == "offline-capture" else "consumed"
        )
        if bound.get("relation") != expected_relation:
            raise CoverageContractError("offline command lineage relation is incorrect")

    if claimed_result != "pass":
        return
    failures: list[str] = []
    if list(reason_codes):
        failures.append("pass has reason codes")
    if any(
        command.get("observed_exit") != command.get("expected_exit")
        for command in commands
    ):
        failures.append("command exit mismatch")
    if observed != expected or any(counts.get(logical, 0) < 1 for logical in expected):
        failures.append("expected contexts are not all observed")
    if cleanup.get("all_workers_stopped") is not True:
        failures.append("cleanup did not stop all workers")
    cleanup_catalogs = cleanup.get("catalogs")
    if (
        not isinstance(cleanup_catalogs, list)
        or sorted(
            str(item.get("catalog_id") or "")
            for item in cleanup_catalogs
            if isinstance(item, Mapping)
        )
        != expected_catalogs
        or any(
            not isinstance(item, Mapping)
            or item.get("worker_stopped") is not True
            or item.get("pid_alive") is not False
            for item in cleanup_catalogs
        )
    ):
        failures.append("cleanup catalog state is not terminal")
    checks = collection_checks.get("checks")
    reasons = collection_checks.get("reasons")
    required_check_names = {
        "manifest_complete",
        "all_command_exits_expected",
        "all_executed_contexts_have_shards",
        "background_workers_complete",
        "offline_replay_lineage_complete",
        "runner_machine_preflight",
        "cleanup",
        "dependency_environment_unchanged",
        "relevant_source_unchanged",
        "metrics_ui_identity_unchanged",
        "repository_coverage_unchanged",
        "measured_config_probe",
    }
    required_reason_names = {
        "missing_required_commands",
        "unexpected_command_exits",
        "missing_foreground_contexts",
        "incomplete_background_contexts",
        "missing_offline_source_lineage",
        "failed_machine_preflight_catalogs",
    }
    if (
        collection_checks.get("result") != "pass"
        or not isinstance(checks, Mapping)
        or set(checks) != required_check_names
        or any(value is not True for value in checks.values())
        or not isinstance(reasons, Mapping)
        or set(reasons) != required_reason_names
        or any(value != [] for value in reasons.values())
    ):
        failures.append("collection checks are not an unqualified pass")
    if any(check.get("complete") is not True for check in worker_checks):
        failures.append("background worker lifecycle is incomplete")
    if any(
        result.get("machine_preflight_verdict") != "pass"
        or result.get("behavioral_verdict") != "not_evaluated"
        or (result.get("cleanup") or {}).get("worker_stopped") is not True
        or (result.get("cleanup") or {}).get("pid_alive") is not False
        for result in runner_results
    ):
        failures.append("runner machine preflight or cleanup is not passing")
    if (
        set(freshness) != {"source_ok", "source_reasons", "dependency_ok"}
        or freshness.get("source_ok") is not True
        or freshness.get("dependency_ok") is not True
    ):
        failures.append("source or dependency freshness is not passing")
    if freshness.get("source_reasons") != []:
        failures.append("source freshness has reasons")
    if failures:
        raise CoverageContractError(
            "pass report contradicts acceptance state: " + "; ".join(failures)
        )


def _validate_immutable_receipt_authority(report: Mapping[str, Any]) -> None:
    """Derive seal/finalization digests, timestamps, and lineage from embedded receipts."""

    integrity = report.get("integrity")
    timestamps = report.get("timestamps")
    inputs = report.get("inputs")
    if not isinstance(integrity, Mapping) or not isinstance(timestamps, Mapping):
        raise CoverageContractError("integrity/timestamps sections are malformed")
    if not isinstance(inputs, Mapping):
        raise CoverageContractError("inputs section is malformed")

    seal = integrity.get("session_seal")
    final_receipt = integrity.get("finalization_receipt")
    session_start = integrity.get("session_start")
    if not all(isinstance(value, Mapping) for value in (seal, final_receipt, session_start)):
        raise CoverageContractError(
            "immutable session/finalization/start receipt contents are missing"
        )

    seal_digest = integrity.get("session_seal_sha256")
    final_digest = integrity.get("finalization_receipt_sha256")
    if not isinstance(seal_digest, str) or not LOWER_HEX_64.fullmatch(seal_digest):
        raise CoverageContractError("session_seal_sha256 is not strict SHA-256 hex")
    if not isinstance(final_digest, str) or not LOWER_HEX_64.fullmatch(final_digest):
        raise CoverageContractError(
            "finalization_receipt_sha256 is not strict SHA-256 hex"
        )
    if sha256_bytes(canonical_file_bytes(seal)) != seal_digest:
        raise CoverageContractError(
            "session_seal_sha256 is not derived from embedded session seal content"
        )
    if sha256_bytes(canonical_file_bytes(final_receipt)) != final_digest:
        raise CoverageContractError(
            "finalization_receipt_sha256 is not derived from embedded finalization content"
        )
    if final_receipt.get("session_seal_sha256") != seal_digest:
        raise CoverageContractError(
            "finalization receipt does not bind the embedded session seal digest"
        )
    if seal.get("schema") != "m007_cli_coverage_session_seal_v1":
        raise CoverageContractError("session seal schema is invalid")
    if final_receipt.get("schema") != "m007_cli_coverage_finalization_receipt_v1":
        raise CoverageContractError("finalization receipt schema is invalid")

    if session_start.get("schema") != "m007_cli_coverage_session_start_v1":
        raise CoverageContractError("session-start schema is invalid")
    if timestamps.get("collection_started_at_utc") != session_start.get(
        "collection_started_at_utc"
    ):
        raise CoverageContractError(
            "collection_started_at_utc is not derived from session-start receipt"
        )
    if timestamps.get("collection_ended_at_utc") != seal.get("collection_ended_at_utc"):
        raise CoverageContractError(
            "collection_ended_at_utc is not derived from session seal content"
        )
    if timestamps.get("finalized_at_utc") != final_receipt.get("finalized_at_utc"):
        raise CoverageContractError(
            "finalized_at_utc is not derived from finalization receipt content"
        )
    # Fail closed on reversed/forged chronology once contents are authoritative.
    started = str(timestamps.get("collection_started_at_utc") or "")
    ended = str(timestamps.get("collection_ended_at_utc") or "")
    finalized = str(timestamps.get("finalized_at_utc") or "")
    if not (started and ended and finalized) or not (started <= ended <= finalized):
        raise CoverageContractError(
            "receipt timestamps are not monotonically ordered"
        )

    sealed_inputs = seal.get("sealed_inputs")
    if not isinstance(sealed_inputs, list):
        raise CoverageContractError("session seal sealed_inputs are malformed")
    sealed_by_path = {
        str(item.get("path") or ""): str(item.get("sha256") or "")
        for item in sealed_inputs
        if isinstance(item, Mapping)
    }
    if len(sealed_by_path) != len(sealed_inputs):
        raise CoverageContractError("session seal sealed_inputs paths are not unique")

    start_path = "session-start.json"
    start_digest = sealed_by_path.get(start_path)
    if not start_digest or not LOWER_HEX_64.fullmatch(start_digest):
        raise CoverageContractError(
            "session seal omits session-start sealed-input digest"
        )
    if sha256_bytes(canonical_file_bytes(session_start)) != start_digest:
        raise CoverageContractError(
            "embedded session-start is not the sealed session-start.json content"
        )

    subject = report.get("subject")
    contexts = report.get("contexts")
    commands = report.get("commands")
    if not isinstance(subject, Mapping) or not isinstance(contexts, Mapping):
        raise CoverageContractError("subject/contexts are malformed for collection binding")
    if not isinstance(commands, list):
        raise CoverageContractError("commands are malformed for collection binding")
    collection_ids = {
        str(session_start.get("collection_id") or ""),
        str(seal.get("collection_id") or ""),
        str(subject.get("collection_id") or ""),
        str(contexts.get("collection_id") or ""),
    }
    command_collection_ids = {
        str(command.get("collection_id") or "")
        for command in commands
        if isinstance(command, Mapping)
    }
    collection_ids |= command_collection_ids
    if (
        len(collection_ids) != 1
        or not re.fullmatch(r"[0-9a-f]{32}", next(iter(collection_ids)))
    ):
        raise CoverageContractError(
            "collection ID is not singular across start, seal, subject, contexts, and commands"
        )

    lineage_path = "receipts/offline-source-lineages.json"
    lineage_digest = sealed_by_path.get(lineage_path)
    lineages = inputs.get("offline_source_lineages")
    if not isinstance(lineages, list):
        raise CoverageContractError("offline source lineages are malformed")
    if not lineage_digest or not LOWER_HEX_64.fullmatch(lineage_digest):
        raise CoverageContractError(
            "session seal omits offline lineage sealed-input digest"
        )
    if sha256_bytes(canonical_file_bytes(lineages)) != lineage_digest:
        raise CoverageContractError(
            "offline source lineages are not the sealed session-input content"
        )
    # Each lineage raw receipt must stay bound to the promoted identity and its
    # own content digest (already checked in acceptance); the seal binding above
    # proves that exact lineage list was an immutable collection input.


def validate_report_semantics(
    report: Mapping[str, Any],
    *,
    repo_root: Path,
    expected_contract: Mapping[str, Any] | None = None,
) -> None:
    """Validate deterministic derivations and acceptance semantics in one place."""

    _reject_local_absolute_paths(report)
    commands = report.get("commands")
    files = report.get("files")
    process = report.get("process_completeness")
    inputs = report.get("inputs")
    contexts = report.get("contexts")
    aggregates = report.get("aggregates")
    integrity = report.get("integrity")
    if not isinstance(commands, list) or any(
        not isinstance(item, Mapping) for item in commands
    ):
        raise CoverageContractError("report commands are malformed")
    if not isinstance(files, list) or any(
        not isinstance(item, Mapping) for item in files
    ):
        raise CoverageContractError("report files are malformed")
    if not all(
        isinstance(value, Mapping)
        for value in (process, inputs, contexts, aggregates, integrity)
    ):
        raise CoverageContractError("report semantic sections are malformed")
    shards = process.get("shards")
    worker_checks = process.get("worker_checks")
    runner_results = process.get("runner_results")
    collection_checks = process.get("collection_checks")
    cleanup = process.get("cleanup")
    lineages = inputs.get("offline_source_lineages")
    worker_probe = inputs.get("worker_probe")
    if not all(
        isinstance(value, list)
        for value in (shards, worker_checks, runner_results, lineages)
    ):
        raise CoverageContractError("report process/input receipts are malformed")
    if not all(
        isinstance(value, Mapping)
        for value in (collection_checks, cleanup, worker_probe)
    ):
        raise CoverageContractError("report process gates are malformed")
    if report.get("cleanup") != cleanup:
        raise CoverageContractError("duplicate cleanup summaries contradict each other")
    subject = report.get("subject")
    if not isinstance(subject, Mapping) or subject.get("collection_id") != contexts.get(
        "collection_id"
    ):
        raise CoverageContractError("subject and context collection identities differ")
    if expected_contract is None:
        raise CoverageContractError(
            "acceptance contract from accepted manifest authority is required"
        )
    _reconcile_manifest_authority(
        report, repo_root=repo_root, expected_contract=expected_contract
    )

    execution = _report_execution(files)
    collection_id = str(contexts.get("collection_id") or "")
    if not re.fullmatch(r"[0-9a-f]{32}", collection_id):
        raise CoverageContractError("collection identity is malformed")
    expected_measurements = {
        str(command.get("logical_context_id") or ""): (
            f"m007-run/{collection_id}/{command.get('logical_context_id')}"
        )
        for command in commands
    }
    if set(execution) != set(expected_measurements):
        raise CoverageContractError(
            "file execution contexts do not exactly cover command receipts"
        )
    for file_record in files:
        for context in file_record.get("contexts") or []:
            logical = str(context.get("logical_context_id") or "")
            if context.get("measurement_context") != expected_measurements.get(logical):
                raise CoverageContractError(
                    "file execution has a contradictory measurement context"
                )

    lifecycles = _flatten_worker_lifecycles(runner_results)
    derived_worker_checks = validate_worker_execution(
        commands=commands,
        shards=shards,
        execution=execution,
        repo_root=repo_root,
        worker_probe=worker_probe,
        worker_lifecycles=lifecycles,
    )
    if worker_checks != derived_worker_checks:
        raise CoverageContractError(
            "worker checks are not derived from lifecycle receipts"
        )

    bootstrap = report.get("bootstrap_comparison")
    if not isinstance(bootstrap, Mapping):
        raise CoverageContractError("bootstrap comparison is malformed")
    bootstrap_id = str(bootstrap.get("bootstrap_logical_context_id") or "")
    contract_bootstrap = str(
        expected_contract.get("bootstrap_logical_context_id") or ""
    )
    bootstrap_commands = [
        command for command in commands if command.get("role") == "bootstrap"
    ]
    if (
        len(bootstrap_commands) != 1
        or bootstrap_commands[0].get("logical_context_id") != bootstrap_id
        or bootstrap_id != contract_bootstrap
    ):
        raise CoverageContractError("bootstrap identity contradicts command receipts")
    expected_bootstrap = bootstrap_comparison(
        execution,
        bootstrap_logical_id=bootstrap_id,
        commands=commands,
    )
    if bootstrap != expected_bootstrap:
        raise CoverageContractError(
            "bootstrap comparison is not deterministically derived"
        )

    command_roles = {
        role: len([command for command in commands if command.get("role") == role])
        for role in (
            "bootstrap",
            "journey_command",
            "supplemental_capture",
            "precondition",
            "cleanup",
        )
    }
    expected_aggregates = {
        "command_roles": command_roles,
        "commands": len(commands),
        "contexts": len(
            {str(command.get("logical_context_id") or "") for command in commands}
        ),
        "shards": len(shards),
        "files": len(files),
        "executed_line_entries": sum(
            len(context.get("executed_lines") or [])
            for file_record in files
            for context in file_record.get("contexts") or []
        ),
        "executed_arc_entries": sum(
            len(context.get("executed_arcs") or [])
            for file_record in files
            for context in file_record.get("contexts") or []
        ),
        "rollups": aggregate_rollups(execution, commands),
        "numeric_gate": False,
    }
    if aggregates != expected_aggregates:
        raise CoverageContractError(
            "aggregate summaries are not deterministically derived"
        )

    expected_canonical_json = {
        "ensure_ascii": False,
        "allow_nan": False,
        "sort_keys": True,
        "separators": [",", ":"],
        "trailing_lf": 1,
        "digest_projection_omits": ["integrity.report_sha256"],
    }
    if integrity.get("canonical_json") != expected_canonical_json:
        raise CoverageContractError("canonical JSON declaration is malformed")
    _validate_immutable_receipt_authority(report)

    freshness = integrity.get("freshness")
    if not isinstance(freshness, Mapping):
        raise CoverageContractError("freshness receipt is malformed")
    validate_acceptance_semantics(
        claimed_result=str(report.get("result") or ""),
        reason_codes=report.get("reason_codes") or [],
        commands=commands,
        shards=shards,
        worker_checks=worker_checks,
        cleanup=cleanup,
        collection_checks=collection_checks,
        runner_results=runner_results,
        offline_source_lineages=lineages,
        contexts=contexts,
        freshness=freshness,
        expected_contract=expected_contract,
        repo_root=repo_root,
    )


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
    expected_contract: Mapping[str, Any] | None = None,
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
        "reason_codes",
        "timestamps",
        "cleanup",
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

    validate_report_semantics(
        report, repo_root=repo_root, expected_contract=expected_contract
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
