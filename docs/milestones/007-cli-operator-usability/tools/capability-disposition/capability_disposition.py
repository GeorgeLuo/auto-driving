#!/usr/bin/env python3
"""Build and validate the deterministic M007-09 capability disposition record.

The tool consumes the sealed M007-07 journey report, a source-analysis artifact
captured under that report's historical runtime, and one human-authored
capability grouping overlay.  It never changes product code or recaptures the
journeys.  The record is the accountable output; the other evidence files are
derived projections of it.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import re
import subprocess
import sys
import sysconfig
import unicodedata
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping

from coverage import __version__ as COVERAGE_VERSION
from coverage.parser import PythonParser


ROOT = Path(__file__).resolve().parents[5]
M007 = ROOT / "docs" / "milestones" / "007-cli-operator-usability"
TOOL_DIR = M007 / "tools" / "capability-disposition"
EVIDENCE_DIR = M007 / "evidence" / "capability-disposition"

REPORT_REL = (
    "docs/milestones/007-cli-operator-usability/evidence/"
    "cli-journey-coverage/report.json"
)
MANIFEST_REL = (
    "docs/milestones/007-cli-operator-usability/tools/"
    "cli-journey-coverage/manifest.json"
)
GROUPING_REL = (
    "docs/milestones/007-cli-operator-usability/tools/"
    "capability-disposition/grouping.json"
)
SOURCE_ANALYSIS_REL = (
    "docs/milestones/007-cli-operator-usability/tools/"
    "capability-disposition/source_analysis.json"
)
RECORD_REL = (
    "docs/milestones/007-cli-operator-usability/evidence/"
    "capability-disposition/record.json"
)
PASS_REPORT_REL = (
    "docs/milestones/007-cli-operator-usability/evidence/"
    "capability-disposition/report.json"
)
RESIDUALS_REL = (
    "docs/milestones/007-cli-operator-usability/evidence/"
    "capability-disposition/residuals.json"
)
ROLLUP_REL = (
    "docs/milestones/007-cli-operator-usability/evidence/"
    "capability-disposition/rollup.md"
)
HTML_REL = (
    "docs/milestones/007-cli-operator-usability/evidence/"
    "capability-disposition/record.html"
)
DASHBOARD_REL = (
    "docs/milestones/007-cli-operator-usability/evidence/"
    "capability-disposition/dashboard.html"
)

SOURCE_ROOTS = ["autonomy", "implementations", "cli/automa_cli"]
JOURNEY_PREFIX = "m007/journey/"
SOURCE_ANALYSIS_SCHEMA = "m007_capability_source_analysis_v1"
GROUPING_SCHEMA = "m007_capability_grouping_v1"
RECORD_SCHEMA = "m007_capability_disposition_v1"
REPORT_SCHEMA = "m007_capability_disposition_report_v1"
RESIDUALS_SCHEMA = "m007_capability_disposition_residuals_v1"
DASHBOARD_SCHEMA = "m007_capability_dashboard_v4"

DASHBOARD_COVERAGE_CLASSES = (
    {
        "id": "discover-observe",
        "name": "Discover and observe",
        "summary": "Find the supported workflow and inspect a healthy observation-only view.",
        "sequence_ids": ("US-01", "US-02"),
    },
    {
        "id": "perception-workflows",
        "name": "Perception configuration and comparison",
        "summary": "Configure, compare, and swap perception implementations.",
        "sequence_ids": ("US-03", "US-04"),
    },
    {
        "id": "memory-behavior",
        "name": "Perception-to-memory behavior",
        "summary": "Observe how perception becomes retained evidence under lifecycle and timing changes.",
        "sequence_ids": ("US-05", "US-06", "US-07"),
    },
    {
        "id": "memory-recovery",
        "name": "Memory recovery and replay",
        "summary": "Recover suspicious memory state and reproduce an anomaly offline.",
        "sequence_ids": ("US-08", "US-09"),
    },
    {
        "id": "physical-qualification",
        "name": "Physical qualification",
        "summary": "Qualify a candidate against labeled physical-check frames.",
        "sequence_ids": ("US-10",),
    },
)

FROZEN_REPORT_SHA256 = (
    "51801c7686b247055114109e7462d13cb6702a1c8dcd8990a168f68357015789"
)
FROZEN_MANIFEST_SHA256 = (
    "bcb20961c05a850fafc16364f13e0a3bde8ef3a612eca523f35a6c065f515683"
)
FROZEN_SOURCE_COMMIT = "7931fa9a995af5626fabef818f9e28b98c73e299"
FROZEN_SOURCE_TREE_SHA256 = (
    "e9e708b083bd203e1ca6b058404869e838ea5ad8dc1e7c9466302b9ab873bbe0"
)
FROZEN_COVERAGE_CONFIG_SHA256 = (
    "67c08cb411118105b4ce373cda5e5a5d559e91fe221b0f35a9c3be011fdc106a"
)
FROZEN_EXECUTABLE_SHA256 = (
    "32da055a5f026c1615772517ef6dd70df85fc486862ecf571bec5915897c8b74"
)
FROZEN_EXECUTABLE_PATH_SHA256 = (
    "225380e24ac6bf74d3c88512e50f100ef45cae27e9f30d66f376b5f968894c5e"
)

FROZEN_RUNTIME = {
    "implementation": "CPython",
    "full_version": "3.11.7 (main, Dec 15 2023, 12:09:56) [Clang 14.0.6 ]",
    "abi": "cpython-311-darwin",
    "cache_tag": "cpython-311",
    "executable_basename": "python3.11",
    "executable_sha256": FROZEN_EXECUTABLE_SHA256,
    "executable_path_sha256": FROZEN_EXECUTABLE_PATH_SHA256,
}
FROZEN_COVERAGE_ANALYSIS = {
    "version": "7.15.2",
    "config_path": ".coveragerc",
    "config_sha256": FROZEN_COVERAGE_CONFIG_SHA256,
    "branch": True,
    "relative_files": True,
    "omit": ["*/__init__.py"],
}
FROZEN_SOURCE_IDENTITY = {
    "commit": FROZEN_SOURCE_COMMIT,
    "relevant_tree_sha256": FROZEN_SOURCE_TREE_SHA256,
    "owned_source_roots": SOURCE_ROOTS,
}

FROZEN_M007_08_MANIFEST = [
    {
        "id": "audit_report",
        "path": (
            "docs/milestones/007-cli-operator-usability/evidence/"
            "cli-surface-audit/report.json"
        ),
        "schema": "m007_cli_surface_audit_v1",
        "sha256": "11cf7c7696f4995bcc433eff6b5f1d67b4e269e39ad825177d664a5add722b6d",
    },
    {
        "id": "leaf_inventory",
        "path": (
            "docs/milestones/007-cli-operator-usability/tools/"
            "cli-surface-audit/leaf_inventory.json"
        ),
        "schema": "m007_leaf_inventory_v1",
        "sha256": "21efc3a9af9bb551e2bd3b0b949f5ddcc50d7748888d97cd360070983d40d3c4",
    },
    {
        "id": "leaf_overlay",
        "path": (
            "docs/milestones/007-cli-operator-usability/tools/"
            "cli-surface-audit/leaf_overlay.json"
        ),
        "schema": "m007_leaf_overlay_v1",
        "sha256": "41e284ea7284f7ae2c74f312a0dde391330813c6e188cd7e16a391f1d69f869f",
    },
    {
        "id": "live_residuals",
        "path": (
            "docs/milestones/007-cli-operator-usability/tools/"
            "cli-surface-audit/live_residuals.json"
        ),
        "schema": "m007_live_residuals_v1",
        "sha256": "a8a0f2c53d230fc56b20fcc0c27391a09e750529028d84922a8a7b67513ca60c",
    },
    {
        "id": "sequence_registry",
        "path": (
            "docs/milestones/007-cli-operator-usability/tools/"
            "cli-surface-audit/sequence_registry.json"
        ),
        "schema": "m007_sequence_registry_v1",
        "sha256": "005ef8c7d4a715e72ba721e29ba5e4df7c22e301668fdd0bc1b280da125308c2",
        "catalog_digest": "9cf4c8bf139183d10ea51c5b576eb47cef1919a161570d704893b3f7372a0e40",
    },
    {
        "id": "us88_catalog",
        "path": (
            "docs/milestones/007-cli-operator-usability/tools/"
            "cli-surface-audit/us88_catalog.json"
        ),
        "schema": "m007_us88_catalog_v1",
        "sha256": "9cf4c8bf139183d10ea51c5b576eb47cef1919a161570d704893b3f7372a0e40",
    },
]

CANONICAL_JSON_DECLARATION = {
    "ensure_ascii": False,
    "allow_nan": False,
    "sort_keys": True,
    "separators": [",", ":"],
    "trailing_lf": 1,
}

DIMENSIONS = (
    "tests",
    "non_cli_entrypoints",
    "dynamic_paths",
    "platform_paths",
)
DISPOSITIONS = {"expose", "retain", "remove"}
DISPOSITION_DEFINITIONS = (
    (
        "expose",
        "Candidate to add or surface through the CLI in a later unit; this unit does not add the leaf.",
    ),
    (
        "retain",
        "Keep outside the declared CLI journeys with an explicit owner and reason; this is not CLI coverage.",
    ),
    (
        "remove",
        "Candidate for a separately reviewed deletion; this unit does not delete or quarantine the code.",
    ),
)
REASON_CODES = {
    "cli_gap",
    "non_cli_entrypoint",
    "dynamic_path",
    "platform_path",
    "separate_removal_review",
}
REASON_CODE_KINDS = {
    "cli_gap": {"m007_08_sequence", "source_member"},
    "non_cli_entrypoint": {"reconciliation_ref"},
    "dynamic_path": {"reconciliation_ref"},
    "platform_path": {"reconciliation_ref"},
    "separate_removal_review": {"source_member", "m007_08_owner"},
}
REASON_CODE_DIMENSIONS = {
    "non_cli_entrypoint": "non_cli_entrypoints",
    "dynamic_path": "dynamic_paths",
    "platform_path": "platform_paths",
}


class CapabilityDispositionError(ValueError):
    """Raised when a sealed input or M007-09 artifact is invalid."""


def _fail(message: str) -> None:
    raise CapabilityDispositionError(message)


def _reject_noncanonical(value: Any, where: str = "$") -> None:
    if isinstance(value, float):
        _fail(f"floats are forbidden at {where}")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                _fail(f"non-string JSON key at {where}")
            _reject_noncanonical(child, f"{where}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_noncanonical(child, f"{where}[{index}]")
    elif value is not None and not isinstance(value, (bool, int, str)):
        _fail(f"unsupported JSON value at {where}: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    _reject_noncanonical(value)
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
    return sha256_bytes(path.read_bytes())


def write_canonical(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_file_bytes(value))


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _exact_keys(value: Any, expected: Iterable[str], where: str) -> None:
    if not isinstance(value, dict):
        _fail(f"{where} must be an object")
    actual = set(value)
    wanted = set(expected)
    missing = sorted(wanted - actual)
    extra = sorted(actual - wanted)
    if missing or extra:
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unknown " + ", ".join(extra))
        _fail(f"{where} has invalid keys: {'; '.join(detail)}")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot read JSON {path}: {exc}")


def load_canonical_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read canonical JSON {path}: {exc}")
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _fail(f"{path} must end with exactly one LF")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"{path} is not canonical UTF-8 JSON: {exc}")
    if canonical_file_bytes(value) != raw:
        _fail(f"{path} bytes are not canonical JSON")
    return value


def _normalized_path(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{where} must be a non-empty POSIX path")
    if "\x00" in value:
        _fail(f"{where} must not contain NUL")
    if "\\" in value:
        _fail(f"{where} must use POSIX separators")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        _fail(f"{where} is not a normalized repository-relative path: {value!r}")
    normalized = pure.as_posix()
    if normalized != value:
        _fail(f"{where} is not canonical: {value!r}")
    return normalized


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _is_source_path(path: str, source_paths: Mapping[str, str]) -> bool:
    return path in source_paths


class FrozenGitSource:
    """Resolve and verify source/config bytes from the sealed Git commit.

    The optional byte-reader and Git-runner seams are intentionally narrow and
    exist only so the resolver's failure modes can be tested without changing
    the repository object database. Production validation uses the exact
    local Git object addressed by ``FROZEN_SOURCE_COMMIT``.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        blob_reader: Callable[[str], bytes] | None = None,
        git_runner: Callable[..., Any] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self._blob_reader = blob_reader
        self._git_runner = git_runner or subprocess.run
        self._expected_hashes: dict[str, str] = {}
        self._cache: dict[str, bytes] = {}

    def bind(self, expected_hashes: Mapping[str, str]) -> None:
        admitted: dict[str, str] = {}
        for path, digest in expected_hashes.items():
            normalized = _normalized_path(path, "frozen source path")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                _fail(f"frozen source hash is malformed for {normalized}")
            if normalized in admitted:
                _fail(f"duplicate frozen source path: {normalized}")
            admitted[normalized] = digest
        if not admitted:
            _fail("frozen source admission set is empty")
        if self._expected_hashes and self._expected_hashes != admitted:
            _fail("frozen source admission set changed")
        self._expected_hashes = admitted

    def expected_sha256(self, path: str) -> str:
        normalized = _normalized_path(path, "frozen source path")
        digest = self._expected_hashes.get(normalized)
        if digest is None:
            _fail(f"frozen source path is not admitted: {normalized}")
        return digest

    def _run_git(self, args: list[str]) -> Any:
        command = ["git", "--no-replace-objects", *args]
        try:
            return self._git_runner(
                command,
                cwd=self.repo_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _fail(f"cannot invoke Git for frozen source resolution: {exc}")

    def _read_git_blob(self, path: str) -> bytes:
        commit_spec = f"{FROZEN_SOURCE_COMMIT}^{{commit}}"
        commit_result = self._run_git(["cat-file", "-e", commit_spec])
        if commit_result.returncode != 0:
            _fail(f"frozen source commit is missing or unusable: {FROZEN_SOURCE_COMMIT}")
        if commit_result.stdout != b"" or commit_result.stderr != b"":
            _fail(f"frozen source commit resolution was not exact: {FROZEN_SOURCE_COMMIT}")

        object_spec = f"{FROZEN_SOURCE_COMMIT}:{path}"
        type_result = self._run_git(["cat-file", "-t", object_spec])
        if type_result.returncode != 0:
            error = type_result.stderr.lower() if isinstance(type_result.stderr, bytes) else b""
            if any(
                marker in error
                for marker in (b"does not exist", b"not a valid object", b"missing")
            ):
                _fail(f"frozen source blob is missing: {path}")
            _fail(f"frozen source object is unreadable: {path}")
        if type_result.stderr != b"" or type_result.stdout != b"blob\n":
            object_type = (
                type_result.stdout.decode("ascii", errors="replace").strip()
                if isinstance(type_result.stdout, bytes)
                else ""
            )
            if object_type and object_type != "blob":
                _fail(f"frozen source path is not a blob: {path} ({object_type})")
            _fail(f"frozen source object type is unreadable: {path}")

        blob_result = self._run_git(["cat-file", "blob", object_spec])
        if blob_result.returncode != 0 or blob_result.stderr != b"":
            _fail(f"frozen source blob is unreadable: {path}")
        if not isinstance(blob_result.stdout, bytes):
            _fail(f"frozen source blob output is malformed: {path}")
        return blob_result.stdout

    def read(self, path: str, expected_sha256: str | None = None) -> bytes:
        normalized = _normalized_path(path, "frozen source path")
        admitted = self._expected_hashes.get(normalized)
        if admitted is None:
            _fail(f"frozen source path is not admitted: {normalized}")
        if expected_sha256 is not None and expected_sha256 != admitted:
            _fail(f"frozen source hash authority changed for {normalized}")
        if normalized in self._cache:
            return self._cache[normalized]

        try:
            raw = (
                self._blob_reader(normalized)
                if self._blob_reader is not None
                else self._read_git_blob(normalized)
            )
        except CapabilityDispositionError:
            raise
        except Exception as exc:  # injected readers must fail through the bounded validator
            _fail(f"frozen source blob is unreadable: {normalized}: {exc}")
        if not isinstance(raw, bytes):
            _fail(f"frozen source blob output is malformed: {normalized}")
        actual = sha256_bytes(raw)
        if actual != admitted:
            _fail(
                f"frozen source hash mismatch for {normalized}: "
                f"expected {admitted}, got {actual}"
            )
        self._cache[normalized] = raw
        return raw


def _is_init(path: str) -> bool:
    return path == "__init__.py" or path.endswith("/__init__.py")


def _validate_int_list(value: Any, where: str) -> list[int]:
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        _fail(f"{where} must be a list of integers")
    if value != sorted(set(value)):
        _fail(f"{where} must be sorted and unique")
    return value


def _validate_arc_list(value: Any, where: str) -> list[tuple[int, int]]:
    if not isinstance(value, list):
        _fail(f"{where} must be a list")
    arcs: list[tuple[int, int]] = []
    for index, arc in enumerate(value):
        if (
            not isinstance(arc, list)
            or len(arc) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) for item in arc)
        ):
            _fail(f"{where}[{index}] must be a two-integer array")
        arcs.append((arc[0], arc[1]))
    if arcs != sorted(set(arcs)):
        _fail(f"{where} must be sorted and unique")
    return arcs


def _source_files_from_report(
    report: Mapping[str, Any],
    repo_root: Path,
    *,
    source_reader: FrozenGitSource | None = None,
) -> dict[str, str]:
    subject = report.get("subject")
    if not isinstance(subject, dict):
        _fail("journey report subject is missing")
    identity = subject.get("source_identity")
    if not isinstance(identity, dict):
        _fail("journey report source identity is missing")
    if identity.get("commit") != FROZEN_SOURCE_COMMIT:
        _fail("journey report source commit is not the sealed M007-07 commit")
    relevant = identity.get("relevant")
    if not isinstance(relevant, dict) or not isinstance(relevant.get("files"), list):
        _fail("journey report relevant source file list is malformed")
    if relevant.get("tree_sha256") != FROZEN_SOURCE_TREE_SHA256:
        _fail("journey report relevant source tree digest changed")
    input_roots = report.get("inputs", {}).get("owned_source_roots")
    if input_roots != SOURCE_ROOTS:
        _fail("journey report owned source roots changed")
    relevant_hashes = report.get("inputs", {}).get("relevant_file_sha256")
    if not isinstance(relevant_hashes, dict):
        _fail("journey report relevant file hashes are missing")

    paths: dict[str, str] = {}
    config_rows: list[tuple[str, str]] = []
    for row in relevant["files"]:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            _fail("journey report relevant file row is malformed")
        path = _normalized_path(row.get("path"), "journey report source path")
        digest = row.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            _fail(f"journey report source hash is malformed for {path}")
        if path == ".coveragerc":
            config_rows.append((path, digest))
        if path.endswith(".py") and any(_under(path, root) for root in SOURCE_ROOTS):
            if path in paths:
                _fail(f"duplicate source path in sealed universe: {path}")
            paths[path] = digest
    if sorted(paths) != list(paths):
        _fail("sealed source universe is not canonically sorted")
    if len(paths) != 96:
        _fail(f"sealed source universe must contain 96 Python paths, got {len(paths)}")
    if any(relevant_hashes.get(path) != digest for path, digest in paths.items()):
        _fail("journey report source hash map does not match its source universe")

    if config_rows != [(".coveragerc", FROZEN_COVERAGE_CONFIG_SHA256)]:
        _fail("sealed .coveragerc identity does not match the M007-07 configuration")
    if relevant_hashes.get(".coveragerc") != FROZEN_COVERAGE_CONFIG_SHA256:
        _fail("journey report .coveragerc hash map does not match its source identity")

    reader = source_reader or FrozenGitSource(repo_root)
    if reader.repo_root != Path(repo_root).resolve():
        _fail("frozen source resolver repository root does not match validation root")
    historical_hashes = {".coveragerc": FROZEN_COVERAGE_CONFIG_SHA256, **paths}
    reader.bind(historical_hashes)
    for path, digest in historical_hashes.items():
        reader.read(path, digest)
    return paths


def _validate_report_contexts(
    report: Mapping[str, Any], source_paths: Mapping[str, str]
) -> tuple[set[str], dict[str, list[dict[str, Any]]]]:
    contexts = report.get("contexts")
    if not isinstance(contexts, dict):
        _fail("journey report contexts are missing")
    expected = contexts.get("expected_logical_contexts")
    observed = contexts.get("observed_logical_contexts")
    if not isinstance(expected, list) or not isinstance(observed, list):
        _fail("journey report logical context lists are malformed")
    if expected != sorted(set(expected)) or observed != sorted(set(observed)):
        _fail("journey report logical contexts are not sorted and unique")
    if expected != observed:
        _fail("expected and observed logical contexts differ")
    if any(not isinstance(item, str) for item in expected):
        _fail("logical context IDs must be strings")
    admitted = {item for item in expected if item.startswith(JOURNEY_PREFIX)}
    if len(admitted) != 22:
        _fail(f"sealed journey role selector must admit 22 contexts, got {len(admitted)}")
    if any(
        item.startswith("m007/bootstrap/") or item.startswith("m007/support/")
        for item in admitted
    ):
        _fail("bootstrap/support context was admitted as a journey")

    file_rows = report.get("files")
    if not isinstance(file_rows, list):
        _fail("journey report files are missing")
    by_path: dict[str, list[dict[str, Any]]] = {}
    for row in file_rows:
        if not isinstance(row, dict) or set(row) != {"path", "contexts"}:
            _fail("journey report file row is malformed")
        path = _normalized_path(row.get("path"), "journey report file path")
        if path in by_path:
            _fail(f"duplicate journey report file path: {path}")
        if path not in source_paths:
            _fail(f"journey report file is outside the sealed source universe: {path}")
        rows = row.get("contexts")
        if not isinstance(rows, list):
            _fail(f"journey report contexts are malformed for {path}")
        seen: set[str] = set()
        normalized_rows: list[dict[str, Any]] = []
        for context in rows:
            if not isinstance(context, dict):
                _fail(f"journey context row is not an object for {path}")
            if set(context) != {"executed_arcs", "executed_lines", "logical_context_id", "measurement_context"}:
                _fail(f"journey context row has an unexpected shape for {path}")
            logical = context.get("logical_context_id")
            if not isinstance(logical, str) or logical not in expected:
                _fail(f"unknown logical context for {path}: {logical!r}")
            if logical in seen:
                _fail(f"duplicate logical context for {path}: {logical}")
            seen.add(logical)
            _validate_int_list(context.get("executed_lines"), f"{path}:{logical}.lines")
            _validate_arc_list(context.get("executed_arcs"), f"{path}:{logical}.arcs")
            normalized_rows.append(context)
        by_path[path] = normalized_rows
    return admitted, by_path


def load_sealed_report(
    repo_root: Path = ROOT,
    *,
    source_reader: FrozenGitSource | None = None,
) -> dict[str, Any]:
    path = repo_root / REPORT_REL
    report = load_canonical_json(path)
    if not isinstance(report, dict) or report.get("schema") != "m007_cli_journey_coverage_v1":
        _fail("sealed M007-07 report has the wrong schema")
    if report.get("result") != "pass":
        _fail("sealed M007-07 report is not a pass result")
    integrity = report.get("integrity")
    if not isinstance(integrity, dict):
        _fail("sealed M007-07 report integrity is missing")
    recorded = integrity.get("report_sha256")
    if recorded != FROZEN_REPORT_SHA256:
        _fail("sealed M007-07 report digest changed")
    projection = _copy(report)
    projection["integrity"].pop("report_sha256", None)
    if sha256_bytes(canonical_json_bytes(projection)) != recorded:
        _fail("sealed M007-07 report digest does not verify")
    if report.get("subject", {}).get("coverage_version") != "7.15.2":
        _fail("sealed M007-07 Coverage.py version changed")
    manifest = report.get("inputs", {}).get("manifest")
    if manifest != {"path": MANIFEST_REL, "sha256": FROZEN_MANIFEST_SHA256}:
        _fail("sealed M007-07 manifest identity changed")
    manifest_path = repo_root / MANIFEST_REL
    try:
        manifest_digest = sha256_file(manifest_path)
    except OSError as exc:
        _fail(f"sealed M007-07 manifest is missing or unreadable: {exc}")
    if manifest_digest != FROZEN_MANIFEST_SHA256:
        _fail("sealed M007-07 manifest bytes changed")
    reader = source_reader or FrozenGitSource(repo_root)
    source_paths = _source_files_from_report(report, repo_root, source_reader=reader)
    admitted, files = _validate_report_contexts(report, source_paths)
    return {
        "report": report,
        "report_path": path,
        "source_paths": source_paths,
        "source_reader": reader,
        "admitted_contexts": admitted,
        "files": files,
    }


def _source_analysis_inputs() -> dict[str, Any]:
    return {
        "source_identity": _copy(FROZEN_SOURCE_IDENTITY),
        "coverage_analysis": _copy(FROZEN_COVERAGE_ANALYSIS),
        "source_analysis_runtime": _copy(FROZEN_RUNTIME),
    }


def _possible_regions(
    path: str,
    repo_root: Path,
    *,
    source_paths: Mapping[str, str] | None = None,
    source_reader: FrozenGitSource | None = None,
) -> tuple[list[int], list[list[int]]]:
    normalized = _normalized_path(path, "sealed source path")
    if source_paths is not None and normalized not in source_paths:
        _fail(f"source-analysis path is outside the sealed source universe: {normalized}")
    if _is_init(normalized):
        return [], []
    reader = source_reader
    if reader is None:
        reader = FrozenGitSource(repo_root)
        if source_paths is None:
            _fail(f"no sealed source hash is available for {normalized}")
        reader.bind(source_paths)
    expected_sha256 = source_paths.get(normalized) if source_paths is not None else None
    if expected_sha256 is None:
        expected_sha256 = reader.expected_sha256(normalized)
    try:
        source = reader.read(normalized, expected_sha256).decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(f"frozen source is not valid UTF-8 for {normalized}: {exc}")
    parser = PythonParser(text=source, filename=path)
    try:
        parser.parse_source()
        statements = sorted(parser.statements)
        arcs = [list(arc) for arc in sorted(parser.arcs())]
    except Exception as exc:  # coverage exposes parser-specific exception types
        _fail(f"Coverage.py cannot analyze sealed source {path}: {exc}")
    return statements, arcs


def _runtime_identity() -> dict[str, Any]:
    executable = Path(sys.executable).resolve()
    try:
        executable_sha = sha256_file(executable)
    except OSError as exc:
        _fail(f"cannot hash the active interpreter: {exc}")
    return {
        "implementation": platform_implementation(),
        "full_version": sys.version,
        "abi": sysconfig.get_config_var("SOABI"),
        "cache_tag": sys.implementation.cache_tag,
        "executable_basename": executable.name,
        "executable_sha256": executable_sha,
        "executable_path_sha256": sha256_bytes(str(executable).encode("utf-8")),
    }


def platform_implementation() -> str:
    return "CPython" if sys.implementation.name == "cpython" else sys.implementation.name


def capture_source_analysis(
    repo_root: Path = ROOT, output: Path | None = None
) -> dict[str, Any]:
    if COVERAGE_VERSION != "7.15.2":
        _fail(f"source-analysis capture requires Coverage.py 7.15.2, got {COVERAGE_VERSION}")
    sealed = load_sealed_report(repo_root)
    runtime = _runtime_identity()
    if runtime != FROZEN_RUNTIME:
        _fail("active interpreter does not match the frozen M007-07 source-analysis runtime")
    files = []
    for path, source_sha256 in sealed["source_paths"].items():
        statements, arcs = _possible_regions(
            path,
            repo_root,
            source_paths=sealed["source_paths"],
            source_reader=sealed["source_reader"],
        )
        files.append(
            {
                "path": path,
                "source_sha256": source_sha256,
                "possible_statements": statements,
                "possible_arcs": arcs,
            }
        )
    artifact = {
        "schema": SOURCE_ANALYSIS_SCHEMA,
        "inputs": _source_analysis_inputs(),
        "files": files,
    }
    destination = output or (repo_root / SOURCE_ANALYSIS_REL)
    write_canonical(destination, artifact)
    return artifact


def validate_source_analysis(
    artifact: Mapping[str, Any],
    repo_root: Path,
    source_paths: Mapping[str, str],
    *,
    source_reader: FrozenGitSource | None = None,
) -> str:
    _exact_keys(artifact, ("schema", "inputs", "files"), "source_analysis")
    if artifact.get("schema") != SOURCE_ANALYSIS_SCHEMA:
        _fail("source-analysis schema is not M007-09")
    if artifact.get("inputs") != _source_analysis_inputs():
        _fail("source-analysis input envelope does not match the sealed proposal")
    rows = artifact.get("files")
    if not isinstance(rows, list):
        _fail("source-analysis files must be a list")
    paths: list[str] = []
    for row in rows:
        _exact_keys(
            row,
            ("path", "source_sha256", "possible_statements", "possible_arcs"),
            "source_analysis.file",
        )
        path = _normalized_path(row.get("path"), "source_analysis.file.path")
        paths.append(path)
        if row.get("source_sha256") != source_paths.get(path):
            _fail(f"source-analysis source hash does not match the sealed source: {path}")
        _validate_int_list(row.get("possible_statements"), f"{path}.possible_statements")
        _validate_arc_list(row.get("possible_arcs"), f"{path}.possible_arcs")
        expected_statements, expected_arcs = _possible_regions(
            path,
            repo_root,
            source_paths=source_paths,
            source_reader=source_reader,
        )
        if row["possible_statements"] != expected_statements:
            _fail(f"source-analysis statement projection changed for {path}")
        if row["possible_arcs"] != expected_arcs:
            _fail(f"source-analysis arc projection changed for {path}")
    if paths != sorted(source_paths) or paths != sorted(set(paths)):
        _fail("source-analysis path set is incomplete, duplicated, or unsorted")
    return sha256_bytes(canonical_file_bytes(dict(artifact)))


def _owner_values(value: Any) -> set[str]:
    values: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"owner", "ledger_owner"} and isinstance(child, str) and child:
                values.add(child)
            values.update(_owner_values(child))
    elif isinstance(value, list):
        for child in value:
            values.update(_owner_values(child))
    return values


def load_m007_08_authority(repo_root: Path = ROOT) -> dict[str, Any]:
    documents: dict[str, Any] = {}
    owners_by_path: dict[str, set[str]] = {}
    for entry in FROZEN_M007_08_MANIFEST:
        path = repo_root / entry["path"]
        actual = sha256_file(path) if path.is_file() else None
        if actual != entry["sha256"]:
            _fail(f"frozen M007-08 input changed or is missing: {entry['path']}")
        document = _load_json(path)
        if not isinstance(document, dict) or document.get("schema") != entry["schema"]:
            _fail(f"frozen M007-08 input schema changed: {entry['id']}")
        documents[entry["id"]] = document
        owners_by_path[entry["path"]] = _owner_values(document)
    registry = documents["sequence_registry"]
    if registry.get("catalog_digest") != "9cf4c8bf139183d10ea51c5b576eb47cef1919a161570d704893b3f7372a0e40":
        _fail("frozen sequence registry catalog digest changed")
    sequence_ids = {row.get("id") for row in registry.get("sequences", []) if isinstance(row, dict)}
    owners = set().union(*owners_by_path.values()) if owners_by_path else set()
    return {
        "documents": documents,
        "entries": _copy(FROZEN_M007_08_MANIFEST),
        "paths": {entry["path"] for entry in FROZEN_M007_08_MANIFEST},
        "digests": {entry["path"]: entry["sha256"] for entry in FROZEN_M007_08_MANIFEST},
        "owners_by_path": owners_by_path,
        "owners_by_digest": {
            entry["sha256"]: owners_by_path[entry["path"]]
            for entry in FROZEN_M007_08_MANIFEST
        },
        "sequence_ids": {value for value in sequence_ids if isinstance(value, str)},
        "owners": owners,
    }


def _parse_ref(ref: Any, where: str) -> tuple[str, str]:
    if not isinstance(ref, str) or ":" not in ref:
        _fail(f"{where} must be a typed reference")
    kind, value = ref.split(":", 1)
    if kind not in {"repo_path", "m007_08_sequence", "m007_08_owner"} or not value:
        _fail(f"{where} has an unsupported reference form")
    return kind, value


def _validate_authority_ref(
    ref: Any,
    *,
    where: str,
    dimension: str,
    repo_root: Path,
    source_paths: Mapping[str, str],
    authority: Mapping[str, Any],
) -> None:
    kind, value = _parse_ref(ref, where)
    if kind == "repo_path":
        path = _normalized_path(value, where + ".path")
        if dimension == "tests":
            if not path.startswith("tests/"):
                _fail(f"{where} test reference must be below tests/")
        elif path not in source_paths and path not in authority["paths"]:
            _fail(f"{where} does not resolve through a sealed authority: {path}")
        if path not in source_paths and not (repo_root / path).exists():
            _fail(f"{where} points to a missing path: {path}")
    elif kind == "m007_08_sequence":
        if "@" not in value:
            _fail(f"{where} sequence reference has no registry digest")
        sequence_id, digest = value.rsplit("@", 1)
        if sequence_id not in authority["sequence_ids"]:
            _fail(f"{where} names an unknown M007-08 sequence")
        if digest != authority["digests"][
            "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/sequence_registry.json"
        ]:
            _fail(f"{where} has the wrong sequence registry digest")
    else:
        if "@" not in value:
            _fail(f"{where} owner reference has no artifact digest")
        owner, digest = value.rsplit("@", 1)
        if owner not in authority["owners"]:
            _fail(f"{where} names an unknown M007-08 owner")
        if digest not in authority["digests"].values():
            _fail(f"{where} has the wrong M007-08 artifact digest")
        if owner not in authority["owners_by_digest"].get(digest, set()):
            _fail(f"{where} owner is not present in the cited M007-08 artifact")


def _normalize_reason_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return re.sub(r"-+", " ", normalized)


NUMBER_WORD = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundred|thousand)"
)
NUMBER = rf"(?:[0-9]+(?:\.[0-9]+)?|{NUMBER_WORD}(?: +{NUMBER_WORD})*)"
UNIT = r"(?:line|lines|branch|branches|statement|statements|arc|arcs)"
RATIO_RE = re.compile(
    rf"(?<!\w){NUMBER}(?:\s*(?:/|:)\s*{NUMBER}| +(?:to|in|of) +{NUMBER}| +out +of +{NUMBER})(?!\w)"
)
COUNT_RE = re.compile(rf"(?<!\w)(?:{NUMBER}\s*{UNIT}|{UNIT}\s*{NUMBER})(?!\w)")
FORBIDDEN_TEXT_RE = re.compile(
    r"\b(?:coverage|covered|unexecuted|un executed|un reached|unreached|"
    r"untested|un tested|not covered|not reached|never executed|"
    r"line count|branch count|statement count|arc count)\b"
)


def validate_non_metric_text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{where} must be a non-empty string")
    normalized = _normalize_reason_text(value)
    if "%" in normalized or re.search(r"\b(?:percent|percentage)\b", normalized):
        _fail(f"{where} cannot authorize a disposition with a percentage")
    if re.search(r"\bper cent\b", normalized):
        _fail(f"{where} cannot authorize a disposition with per-cent language")
    if RATIO_RE.search(normalized):
        _fail(f"{where} cannot authorize a disposition with a ratio")
    if COUNT_RE.search(normalized):
        _fail(f"{where} cannot authorize a disposition with a region count")
    if FORBIDDEN_TEXT_RE.search(normalized):
        _fail(f"{where} contains a forbidden reachability or metric term")
    return value


def _validate_owner(
    owner: Any,
    *,
    where: str,
    group_paths: set[str],
    repo_root: Path,
    source_paths: Mapping[str, str],
    authority: Mapping[str, Any],
) -> None:
    _exact_keys(owner, ("kind", "ref"), where)
    kind = owner.get("kind")
    ref = owner.get("ref")
    if kind == "repo_path":
        path = _normalized_path(ref, where + ".ref")
        if path not in source_paths:
            candidate = repo_root / path
            if not candidate.is_dir() or not any(_under(member, path) for member in group_paths):
                _fail(f"{where}.ref is not a source file/directory containing a member")
        elif not any(member == path or _under(member, path) for member in group_paths):
            _fail(f"{where}.ref does not contain a group member")
    elif kind == "m007_08_owner":
        if not isinstance(ref, str) or ref not in authority["owners"]:
            _fail(f"{where}.ref is not a frozen M007-08 owner")
    else:
        _fail(f"{where}.kind is unsupported")


def _validate_reconcile(
    reconcile: Any,
    *,
    where: str,
    repo_root: Path,
    source_paths: Mapping[str, str],
    authority: Mapping[str, Any],
) -> None:
    _exact_keys(reconcile, DIMENSIONS, where)
    for dimension in DIMENSIONS:
        item = reconcile.get(dimension)
        _exact_keys(item, ("status", "refs", "reason"), f"{where}.{dimension}")
        status = item.get("status")
        refs = item.get("refs")
        if status not in {"present", "not_applicable"}:
            _fail(f"{where}.{dimension} has an unsupported status")
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            _fail(f"{where}.{dimension}.refs must be a list of strings")
        if refs != sorted(set(refs)):
            _fail(f"{where}.{dimension}.refs must be sorted and unique")
        if status == "present":
            if not refs or item.get("reason") != "":
                _fail(f"{where}.{dimension} present requires refs and an empty reason")
            for index, ref in enumerate(refs):
                _validate_authority_ref(
                    ref,
                    where=f"{where}.{dimension}.refs[{index}]",
                    dimension=dimension,
                    repo_root=repo_root,
                    source_paths=source_paths,
                    authority=authority,
                )
        else:
            if refs:
                _fail(f"{where}.{dimension} not_applicable cannot have refs")
            validate_non_metric_text(item.get("reason"), f"{where}.{dimension}.reason")


def _validate_reason(
    reason: Any,
    *,
    disposition: str,
    group_paths: set[str],
    reconcile: Mapping[str, Any],
    source_paths: Mapping[str, str],
    authority: Mapping[str, Any],
    where: str,
) -> None:
    _exact_keys(reason, ("code", "reference", "detail"), where)
    code = reason.get("code")
    if code not in REASON_CODES:
        _fail(f"{where}.code is unsupported")
    detail = validate_non_metric_text(reason.get("detail"), f"{where}.detail")
    del detail
    reference = reason.get("reference")
    if not isinstance(reference, dict):
        _fail(f"{where}.reference must be an object")
    kind = reference.get("kind")
    if kind not in REASON_CODE_KINDS[code]:
        _fail(f"{where}.reference kind is incompatible with reason code")
    if code == "cli_gap" and disposition != "expose":
        _fail("cli_gap is only valid for an expose disposition")
    if code in {"non_cli_entrypoint", "dynamic_path", "platform_path"} and disposition != "retain":
        _fail(f"{code} is only valid for a retain disposition")
    if code == "separate_removal_review" and disposition != "remove":
        _fail("separate_removal_review is only valid for a remove disposition")

    if kind == "source_member":
        _exact_keys(reference, ("kind", "path", "source_sha256"), where + ".reference")
        path = _normalized_path(reference.get("path"), where + ".reference.path")
        if path not in group_paths or reference.get("source_sha256") != source_paths.get(path):
            _fail(f"{where}.reference source member does not resolve to this group")
    elif kind == "reconciliation_ref":
        _exact_keys(reference, ("kind", "dimension", "ref"), where + ".reference")
        dimension = reference.get("dimension")
        ref = reference.get("ref")
        if dimension not in DIMENSIONS:
            _fail(f"{where}.reference dimension is unsupported")
        if reconcile[dimension].get("status") != "present" or ref not in reconcile[dimension].get("refs", []):
            _fail(f"{where}.reference must target a present same-group reconciliation ref")
        if REASON_CODE_DIMENSIONS.get(code) != dimension:
            _fail(f"{where}.reference dimension is incompatible with reason code")
    elif kind == "m007_08_sequence":
        _exact_keys(reference, ("kind", "sequence_id", "registry_sha256"), where + ".reference")
        _validate_authority_ref(
            "m007_08_sequence:" + str(reference.get("sequence_id")) + "@" + str(reference.get("registry_sha256")),
            where=where + ".reference",
            dimension="non_cli_entrypoints",
            repo_root=ROOT,
            source_paths=source_paths,
            authority=authority,
        )
    elif kind == "m007_08_owner":
        _exact_keys(reference, ("kind", "value", "artifact_path", "artifact_sha256"), where + ".reference")
        path = reference.get("artifact_path")
        if (
            not isinstance(path, str)
            or path not in authority["paths"]
            or authority["digests"].get(path) != reference.get("artifact_sha256")
        ):
            _fail(f"{where}.reference M007-08 artifact is not frozen")
        if reference.get("value") not in authority["owners"]:
            _fail(f"{where}.reference owner value is not frozen")
        if reference.get("value") not in authority["owners_by_path"].get(path, set()):
            _fail(f"{where}.reference owner is not present in the cited M007-08 artifact")


def validate_grouping(
    grouping: Mapping[str, Any],
    *,
    repo_root: Path,
    source_paths: Mapping[str, str],
    candidate_paths: set[str],
    authority: Mapping[str, Any],
) -> None:
    _exact_keys(grouping, ("schema", "groups"), "grouping")
    if grouping.get("schema") != GROUPING_SCHEMA:
        _fail("grouping schema is not M007-09")
    groups = grouping.get("groups")
    if not isinstance(groups, list):
        _fail("grouping.groups must be a list")
    ids: list[str] = []
    assigned: list[str] = []
    for index, group in enumerate(groups):
        where = f"grouping.groups[{index}]"
        _exact_keys(group, ("id", "name", "member_paths", "reconcile", "owner", "disposition", "reason"), where)
        group_id = group.get("id")
        name = group.get("name")
        if not isinstance(group_id, str) or not group_id.strip() or not isinstance(name, str) or not name.strip():
            _fail(f"{where} id/name must be non-empty strings")
        ids.append(unicoded_key(group_id))
        member_paths = group.get("member_paths")
        if not isinstance(member_paths, list) or not member_paths:
            _fail(f"{where}.member_paths must be non-empty")
        normalized_paths = [_normalized_path(path, f"{where}.member_paths") for path in member_paths]
        if normalized_paths != sorted(normalized_paths):
            _fail(f"{where}.member_paths must be sorted")
        if len(normalized_paths) != len(set(normalized_paths)):
            _fail(f"{where}.member_paths contains duplicates")
        for path in normalized_paths:
            if path not in source_paths:
                _fail(f"{where}.member_paths contains a path outside the sealed source universe")
        assigned.extend(normalized_paths)
        _validate_reconcile(
            group.get("reconcile"),
            where=where + ".reconcile",
            repo_root=repo_root,
            source_paths=source_paths,
            authority=authority,
        )
        _validate_owner(
            group.get("owner"),
            where=where + ".owner",
            group_paths=set(normalized_paths),
            repo_root=repo_root,
            source_paths=source_paths,
            authority=authority,
        )
        disposition = group.get("disposition")
        if disposition not in DISPOSITIONS:
            _fail(f"{where}.disposition is unsupported")
        _validate_reason(
            group.get("reason"),
            disposition=disposition,
            group_paths=set(normalized_paths),
            reconcile=group["reconcile"],
            source_paths=source_paths,
            authority=authority,
            where=where + ".reason",
        )
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        _fail("group IDs must be unique and sorted under NFKC/case-folding")
    if len(assigned) != len(set(assigned)):
        _fail("a source member is assigned to more than one capability group")
    if set(assigned) != candidate_paths:
        missing = sorted(candidate_paths - set(assigned))
        extra = sorted(set(assigned) - candidate_paths)
        _fail(f"grouping candidate parity failed; missing={missing[:4]} extra={extra[:4]}")


def unicoded_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _derive_candidates(
    sealed: Mapping[str, Any], artifact: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    artifact_by_path = {row["path"]: row for row in artifact["files"]}
    candidates: dict[str, dict[str, Any]] = {}
    for path, source_sha256 in sealed["source_paths"].items():
        possible = artifact_by_path[path]
        executed_lines: set[int] = set()
        executed_arcs: set[tuple[int, int]] = set()
        for context in sealed["files"].get(path, []):
            if context["logical_context_id"] in sealed["admitted_contexts"]:
                executed_lines.update(context["executed_lines"])
                executed_arcs.update(tuple(arc) for arc in context["executed_arcs"])
        missing_lines = sorted(set(possible["possible_statements"]) - executed_lines)
        missing_arcs = sorted(
            set(tuple(arc) for arc in possible["possible_arcs"]) - executed_arcs
        )
        if path not in sealed["files"] or missing_lines or missing_arcs:
            candidates[path] = {
                "path": path,
                "source_sha256": source_sha256,
                "unreached_statements": missing_lines,
                "unreached_arcs": [list(arc) for arc in missing_arcs],
            }
    return candidates


def _record_inputs(
    *,
    source_analysis_sha256: str,
    grouping_sha256: str,
) -> dict[str, Any]:
    return {
        "journey_coverage": {
            "report_path": REPORT_REL,
            "report_sha256": FROZEN_REPORT_SHA256,
            "manifest_path": MANIFEST_REL,
            "manifest_sha256": FROZEN_MANIFEST_SHA256,
            "role_selector": {
                "admit_logical_context_prefix": JOURNEY_PREFIX,
                "admitted_context_count": 22,
                "excluded_prefixes": ["m007/bootstrap/", "m007/support/"],
            },
            "source_identity": _copy(FROZEN_SOURCE_IDENTITY),
            "coverage_analysis": _copy(FROZEN_COVERAGE_ANALYSIS),
            "source_analysis_runtime": _copy(FROZEN_RUNTIME),
        },
        "source_analysis": {
            "schema": SOURCE_ANALYSIS_SCHEMA,
            "path": SOURCE_ANALYSIS_REL,
            "sha256": source_analysis_sha256,
        },
        "m007_08": {"input_manifest": _copy(FROZEN_M007_08_MANIFEST)},
        "grouping_input": {
            "schema": GROUPING_SCHEMA,
            "path": GROUPING_REL,
            "sha256": grouping_sha256,
        },
    }


def _groups_from_overlay(
    grouping: Mapping[str, Any], candidates: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    groups = []
    for overlay in grouping["groups"]:
        members = [
            _copy(candidates[path]) for path in overlay["member_paths"]
        ]
        groups.append(
            {
                "id": overlay["id"],
                "name": overlay["name"],
                "members": members,
                "reconcile": _copy(overlay["reconcile"]),
                "owner": _copy(overlay["owner"]),
                "disposition": overlay["disposition"],
                "reason": _copy(overlay["reason"]),
            }
        )
    return groups


def assemble_record(
    *,
    sealed: Mapping[str, Any],
    artifact: Mapping[str, Any],
    grouping: Mapping[str, Any],
    source_analysis_sha256: str,
    grouping_sha256: str,
) -> dict[str, Any]:
    candidates = _derive_candidates(sealed, artifact)
    validate_grouping(
        grouping,
        repo_root=sealed["report_path"].parents[5],
        source_paths=sealed["source_paths"],
        candidate_paths=set(candidates),
        authority=load_m007_08_authority(sealed["report_path"].parents[5]),
    )
    assigned = sorted(
        path for group in grouping["groups"] for path in group["member_paths"]
    )
    record = {
        "schema": RECORD_SCHEMA,
        "integrity": {
            "canonical_json": _copy(CANONICAL_JSON_DECLARATION),
            "digest_projection_omits": ["integrity.record_sha256"],
            "record_sha256": "",
        },
        "inputs": _record_inputs(
            source_analysis_sha256=source_analysis_sha256,
            grouping_sha256=grouping_sha256,
        ),
        "residuals": {
            "candidate_member_paths": sorted(candidates),
            "assigned_member_paths": assigned,
            "unassigned_member_paths": sorted(set(candidates) - set(assigned)),
            "unresolved_region_refs": [],
        },
        "groups": _groups_from_overlay(grouping, candidates),
    }
    record["integrity"]["record_sha256"] = record_digest(record)
    return record


def record_digest(record: Mapping[str, Any]) -> str:
    projection = _copy(record)
    integrity = projection.get("integrity")
    if not isinstance(integrity, dict):
        _fail("record integrity is missing")
    integrity.pop("record_sha256", None)
    return sha256_bytes(canonical_json_bytes(projection))


def validate_record(
    record: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
) -> None:
    _exact_keys(record, ("schema", "integrity", "inputs", "residuals", "groups"), "record")
    if record.get("schema") != RECORD_SCHEMA:
        _fail("record schema is not M007-09")
    integrity = record.get("integrity")
    _exact_keys(integrity, ("canonical_json", "digest_projection_omits", "record_sha256"), "record.integrity")
    if integrity.get("canonical_json") != CANONICAL_JSON_DECLARATION:
        _fail("record canonical JSON declaration changed")
    if integrity.get("digest_projection_omits") != ["integrity.record_sha256"]:
        _fail("record digest projection declaration changed")
    if not isinstance(integrity.get("record_sha256"), str) or integrity["record_sha256"] != record_digest(record):
        _fail("record digest does not verify")
    if record.get("inputs") != expected.get("inputs"):
        _fail("record input envelope does not match derived sealed inputs")
    if record.get("residuals") != expected.get("residuals"):
        _fail("record residuals do not match derived membership")
    if record.get("groups") != expected.get("groups"):
        _fail("record groups do not match the validated grouping overlay")


def _make_pass_report(record: Mapping[str, Any], sealed: Mapping[str, Any]) -> dict[str, Any]:
    statement_count = sum(
        len(member["unreached_statements"])
        for group in record["groups"]
        for member in group["members"]
    )
    arc_count = sum(
        len(member["unreached_arcs"])
        for group in record["groups"]
        for member in group["members"]
    )
    dispositions = Counter(group["disposition"] for group in record["groups"])
    return {
        "schema": REPORT_SCHEMA,
        "result": "pass",
        "record": {
            "path": RECORD_REL,
            "sha256": record["integrity"]["record_sha256"],
        },
        "inputs": record["inputs"],
        "membership": {
            "source_member_count": len(sealed["source_paths"]),
            "journey_report_file_count": len(sealed["files"]),
            "admitted_context_count": len(sealed["admitted_contexts"]),
            "candidate_member_count": len(record["residuals"]["candidate_member_paths"]),
            "unreached_statement_count": statement_count,
            "unreached_arc_count": arc_count,
        },
        "groups": [
            {
                "id": group["id"],
                "name": group["name"],
                "member_count": len(group["members"]),
                "disposition": group["disposition"],
                "owner": group["owner"],
                "reason_code": group["reason"]["code"],
            }
            for group in record["groups"]
        ],
        "residuals": record["residuals"],
        "non_claims": {
            "dead_code": False,
            "numeric_coverage_gate": False,
            "product_change": False,
            "live_recapture": False,
        },
        "integrity": {"record_sha256": record["integrity"]["record_sha256"]},
    }


def _make_residuals(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": RESIDUALS_SCHEMA,
        "record_sha256": record["integrity"]["record_sha256"],
        "residuals": _copy(record["residuals"]),
        "disposition_candidates": [
            {
                "id": group["id"],
                "disposition": group["disposition"],
                "owner": group["owner"],
                "reason_code": group["reason"]["code"],
            }
            for group in record["groups"]
        ],
        "non_claims": {
            "remove_is_not_deletion": True,
            "expose_is_not_implemented": True,
            "dead_code": False,
        },
    }


def render_rollup(record: Mapping[str, Any], sealed: Mapping[str, Any]) -> str:
    lines = [
        "# M007-09 capability disposition",
        "",
        "Result: `pass`",
        "",
        f"Record: `{RECORD_REL}` (`{record['integrity']['record_sha256']}`)",
        "",
        "This rollup is a derived human view. The record and validators are the",
        "authority. Unreached does not mean dead, and a disposition does not",
        "implement an expose or remove candidate.",
        "",
        "## Membership",
        "",
        f"- Sealed source members: {len(sealed['source_paths'])}",
        f"- Admitted journey contexts: {len(sealed['admitted_contexts'])}",
        f"- Candidate members: {len(record['residuals']['candidate_member_paths'])}",
        f"- Assigned members: {len(record['residuals']['assigned_member_paths'])}",
        "- Unassigned members: 0",
        "- Unresolved region references: 0",
        "",
        "## Capability groups",
        "",
        "| ID | Members | Disposition | Owner | Reason code |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for group in record["groups"]:
        owner = f"{group['owner']['kind']}:{group['owner']['ref']}"
        lines.append(
            f"| `{group['id']}` | {len(group['members'])} | "
            f"`{group['disposition']}` | `{owner}` | `{group['reason']['code']}` |"
        )
    lines.extend(
        [
            "",
            "## Dispositions",
            "",
            "These labels are candidate dispositions for later review, not actions",
            "performed by M007-09. Definitions follow the [accepted M007-09",
            "proposal](../../proposals/capability-disposition.md).",
            "",
            "| Disposition | Meaning in this unit |",
            "| --- | --- |",
        ]
    )
    lines.extend(
        f"| `{disposition}` | {meaning} |"
        for disposition, meaning in DISPOSITION_DEFINITIONS
    )
    lines.extend(
        [
            "",
            "## Non-claims",
            "",
            "- The record does not claim that unreached code is dead.",
            "- Coverage percentages are not authorization for any disposition.",
            "- `expose`, `retain`, and `remove` are candidates; no product change is",
            "  performed by this review unit.",
            "",
        ]
    )
    return "\n".join(lines)


def _attr(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _disposition_definitions_markup(proposal_href: str) -> str:
    rows = "".join(
        f"<dt><code>{_attr(disposition)}</code></dt>"
        f"<dd>{_attr(meaning)}</dd>"
        for disposition, meaning in DISPOSITION_DEFINITIONS
    )
    return (
        '<details class="disposition-definitions">'
        '<summary>What expose, retain, and remove mean</summary>'
        '<p>These are candidate dispositions for later review, not actions '
        'performed by M007-09. '
        f'<a href="{_attr(proposal_href)}">Open the accepted M007-09 proposal</a> '
        'for the governing contract.</p>'
        f"<dl>{rows}</dl>"
        "</details>"
    )


def _dashboard_argv_tokens(argv: Any) -> tuple[str, ...]:
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
        return ()
    tokens = list(argv)
    if tokens and PurePosixPath(tokens[0]).name == "automa":
        tokens = tokens[1:]
    return tuple(tokens)


def _dashboard_leaf_rows(authority: Mapping[str, Any]) -> list[tuple[tuple[str, ...], str, str]]:
    inventory = authority["documents"]["leaf_inventory"]
    rows = [
        (
            tuple(row["tokens"]),
            row["leaf_id"],
            row["kind"],
        )
        for row in inventory["leaves"]
    ]
    return sorted(rows, key=lambda row: (-len(row[0]), row[1]))


def _dashboard_leaf_for_argv(
    argv: Any,
    leaf_rows: Iterable[tuple[tuple[str, ...], str, str]],
) -> tuple[str, str] | None:
    tokens = _dashboard_argv_tokens(argv)
    for prefix, leaf_id, kind in leaf_rows:
        if tokens[: len(prefix)] == prefix:
            return leaf_id, kind
    return None


def _dashboard_sequence_rows(
    authority: Mapping[str, Any],
) -> list[dict[str, Any]]:
    leaf_rows = _dashboard_leaf_rows(authority)
    sequence_rows: list[dict[str, Any]] = []
    status_by_disposition = {
        "passed": "covered",
        "ready": "ready",
        "deferred": "not_covered",
        "blocked": "blocked",
    }
    sequences = authority["documents"]["sequence_registry"]["sequences"]
    for sequence in sorted(sequences, key=lambda row: row["id"]):
        sequence_leaf_ids = set()
        for command in sequence["commands"]:
            match = _dashboard_leaf_for_argv(["./cli/automa", *command], leaf_rows)
            if match is not None:
                sequence_leaf_ids.add(match[0])
        disposition = sequence["disposition"]
        sequence_rows.append(
            {
                "id": sequence["id"],
                "operator_outcome": sequence["operator_outcome"],
                "operator_question": sequence["operator_question"],
                "primary_confirmation": sequence["primary_confirmation"],
                "disposition": disposition,
                "status": status_by_disposition.get(disposition, disposition),
                "coverage": sequence["coverage"]["value"],
                "coverage_reason": sequence["coverage"].get("reason"),
                "execution": sequence["execution"],
                "safety_class": sequence["safety_class"],
                "completeness": sequence["completeness"],
                "prerequisites": sequence["prerequisites"],
                "owner": sequence.get("owner"),
                "unlock": sequence.get("unlock"),
                "command_count": len(sequence["commands"]),
                "leaf_ids": sorted(sequence_leaf_ids),
            }
        )
    return sequence_rows


def _dashboard_coverage_overview(
    sequence_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    all_sequence_rows = list(sequence_rows)
    sequences = {sequence["id"]: sequence for sequence in all_sequence_rows}
    expected_ids = [
        sequence_id
        for coverage_class in DASHBOARD_COVERAGE_CLASSES
        for sequence_id in coverage_class["sequence_ids"]
    ]
    if len(sequences) != len(all_sequence_rows) or len(set(expected_ids)) != len(expected_ids):
        raise CapabilityDispositionError("dashboard coverage sequence IDs are not unique")
    if set(sequences) != set(expected_ids):
        raise CapabilityDispositionError(
            "dashboard coverage classes do not account for every registered sequence"
        )

    classes: list[dict[str, Any]] = []
    for coverage_class in DASHBOARD_COVERAGE_CLASSES:
        class_rows = [
            sequences[sequence_id] for sequence_id in coverage_class["sequence_ids"]
        ]
        statuses = [row["status"] for row in class_rows]
        if all(status == "covered" for status in statuses):
            status = "covered"
        elif any(status == "blocked" for status in statuses) and not any(
            candidate == "covered" for candidate in statuses
        ):
            status = "blocked"
        elif any(status == "covered" for status in statuses):
            status = "partial"
        elif any(status == "ready" for status in statuses):
            status = "ready"
        else:
            status = "not_covered"
        classes.append(
            {
                "id": coverage_class["id"],
                "name": coverage_class["name"],
                "summary": coverage_class["summary"],
                "status": status,
                "sequence_ids": list(coverage_class["sequence_ids"]),
                "sequences": class_rows,
                "next_steps": [
                    {
                        "sequence_id": row["id"],
                        "owner": row["owner"],
                        "unlock": row["unlock"],
                    }
                    for row in class_rows
                    if row["status"] != "covered"
                ],
            }
        )
    return {
        "classes": classes,
        "covered_class_count": sum(item["status"] == "covered" for item in classes),
        "open_class_count": sum(item["status"] in {"not_covered", "partial", "ready"} for item in classes),
        "blocked_class_count": sum(item["status"] == "blocked" for item in classes),
    }


def _dashboard_command_leaf_status(statuses: Iterable[str]) -> str:
    values = set(statuses)
    if "covered" in values:
        return "covered"
    if "ready" in values or "not_covered" in values:
        return "planned"
    if "blocked" in values:
        return "blocked"
    return "uncovered"


def _dashboard_command_node_status(statuses: Iterable[str]) -> str:
    values = set(statuses)
    if not values:
        return "uncovered"
    if len(values) == 1:
        return next(iter(values))
    return "partial"


def _dashboard_command_tree(
    authority: Mapping[str, Any],
    sequence_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    leaf_rows = _dashboard_leaf_rows(authority)
    sequence_by_leaf_id: dict[str, list[tuple[str, str]]] = {}
    for sequence in sequence_rows:
        for leaf_id in sequence["leaf_ids"]:
            sequence_by_leaf_id.setdefault(leaf_id, []).append(
                (sequence["id"], sequence["status"])
            )

    def make_node(token: str, path: list[str]) -> dict[str, Any]:
        return {
            "token": token,
            "path": path,
            "children": {},
            "direct_leaf_ids": set(),
            "direct_kinds": {},
            "direct_sequence_ids": set(),
            "direct_statuses": set(),
        }

    root = make_node("automa", [])
    for tokens, leaf_id, kind in leaf_rows:
        node = root
        path: list[str] = []
        for token in tokens:
            path.append(token)
            children = node["children"]
            if token not in children:
                children[token] = make_node(token, list(path))
            node = children[token]
        node["direct_leaf_ids"].add(leaf_id)
        node["direct_kinds"][leaf_id] = kind
        for sequence_id, status in sequence_by_leaf_id.get(leaf_id, []):
            node["direct_sequence_ids"].add(sequence_id)
            node["direct_statuses"].add(status)

    def materialize(node: Mapping[str, Any]) -> dict[str, Any]:
        children = [
            materialize(node["children"][token])
            for token in sorted(node["children"])
        ]
        leaf_statuses: dict[str, str] = {
            leaf_id: _dashboard_command_leaf_status(node["direct_statuses"])
            for leaf_id in node["direct_leaf_ids"]
        }
        sequence_ids = set(node["direct_sequence_ids"])
        for child in children:
            leaf_statuses.update(child.pop("_leaf_statuses"))
            sequence_ids.update(child["sequence_ids"])
        path = list(node["path"])
        command = " ".join(["automa", *path])
        direct_leaf_ids = sorted(node["direct_leaf_ids"])
        direct_leaf_id = direct_leaf_ids[0] if len(direct_leaf_ids) == 1 else None
        result = {
            "token": node["token"],
            "path": path,
            "command": command,
            "status": _dashboard_command_node_status(leaf_statuses.values()),
            "leaf_id": direct_leaf_id,
            "kind": node["direct_kinds"].get(direct_leaf_id),
            "leaf_ids": sorted(leaf_statuses),
            "sequence_ids": sorted(sequence_ids),
            "children": children,
            "_leaf_statuses": leaf_statuses,
        }
        return result

    tree = materialize(root)
    tree.pop("_leaf_statuses")
    return tree


def _dashboard_command_tree_paths(node: Mapping[str, Any]) -> list[str]:
    paths = [node["command"]]
    for child in node["children"]:
        paths.extend(_dashboard_command_tree_paths(child))
    return paths


def _dashboard_projection(
    record: Mapping[str, Any],
    sealed: Mapping[str, Any],
    authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if authority is None:
        authority = load_m007_08_authority()
    candidate_paths = set(record["residuals"]["candidate_member_paths"])
    source_paths = set(sealed["source_paths"])
    report_paths = set(sealed["files"])
    source_status = {
        "fully_reached": len(source_paths - candidate_paths),
        "candidate_partial": len(candidate_paths & report_paths),
        "candidate_absent": len(candidate_paths - report_paths),
    }
    disposition_counts = Counter(
        group["disposition"]
        for group in record["groups"]
        for _member in group["members"]
    )
    groups: list[dict[str, Any]] = []
    total_statements = 0
    total_arcs = 0
    for group in record["groups"]:
        members: list[dict[str, Any]] = []
        statement_count = 0
        arc_count = 0
        for member in group["members"]:
            statements = list(member["unreached_statements"])
            arcs = [list(arc) for arc in member["unreached_arcs"]]
            statement_count += len(statements)
            arc_count += len(arcs)
            members.append(
                {
                    "path": member["path"],
                    "source_sha256": member["source_sha256"],
                    "unreached_statements": statements,
                    "unreached_arcs": arcs,
                }
            )
        total_statements += statement_count
        total_arcs += arc_count
        groups.append(
            {
                "id": group["id"],
                "name": group["name"],
                "member_count": len(members),
                "statement_count": statement_count,
                "arc_count": arc_count,
                "disposition": group["disposition"],
                "owner": _copy(group["owner"]),
                "reason": _copy(group["reason"]),
                "reconcile": _copy(group["reconcile"]),
                "members": members,
            }
        )
    sequence_rows = _dashboard_sequence_rows(authority)
    return {
        "schema": DASHBOARD_SCHEMA,
        "record_sha256": record["integrity"]["record_sha256"],
        "membership": {
            "source_members": len(source_paths),
            "journey_contexts": len(sealed["admitted_contexts"]),
            "journey_report_files": len(sealed["files"]),
            "candidate_members": len(candidate_paths),
            "assigned_members": len(record["residuals"]["assigned_member_paths"]),
            "unassigned_members": len(record["residuals"]["unassigned_member_paths"]),
            "unresolved_region_refs": len(record["residuals"]["unresolved_region_refs"]),
            "unreached_statements": total_statements,
            "unreached_arcs": total_arcs,
        },
        "source_status": source_status,
        "dispositions": {
            disposition: disposition_counts.get(disposition, 0)
            for disposition in ("expose", "retain", "remove")
        },
        "coverage_overview": _dashboard_coverage_overview(sequence_rows),
        "command_tree": _dashboard_command_tree(authority, sequence_rows),
        "groups": groups,
    }


def _dashboard_script_json(value: Mapping[str, Any]) -> str:
    """Return canonical JSON safe to place inside an application/json script."""

    return (
        canonical_json_bytes(value)
        .decode("utf-8")
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _dashboard_percentage(value: int, total: int) -> str:
    if total == 0 or value == 0:
        return "0"
    return f"{100 * value / total:.3f}".rstrip("0").rstrip(".")


def _dashboard_detail_markup(group: Mapping[str, Any]) -> str:
    dimensions = []
    for dimension, item in group["reconcile"].items():
        refs = item["refs"]
        reference_markup = (
            "<ul>"
            + "".join(f"<li><code>{_attr(ref)}</code></li>" for ref in refs)
            + "</ul>"
            if refs
            else "<span class=\"muted\">No typed references.</span>"
        )
        dimensions.append(
            "<li>"
            f"<strong>{_attr(dimension)}</strong>: "
            f"<code>{_attr(item['status'])}</code> {reference_markup}"
            "</li>"
        )
    members = []
    for member in group["members"]:
        statements = member["unreached_statements"]
        arcs = member["unreached_arcs"]
        statement_text = ", ".join(str(value) for value in statements) or "None"
        arc_text = ", ".join(f"{arc[0]} → {arc[1]}" for arc in arcs) or "None"
        members.append(
            "<details class=\"member\" "
            f"data-member-path=\"{_attr(member['path'])}\">"
            "<summary>"
            f"<code>{_attr(member['path'])}</code>"
            f"<span class=\"member-counts\">{len(statements)} statements · "
            f"{len(arcs)} arcs</span>"
            "</summary>"
            "<div class=\"member-detail\">"
            f"<p class=\"muted\">Source SHA-256: <code>{_attr(member['source_sha256'])}</code></p>"
            f"<p><strong>Statement lines:</strong> <code>{_attr(statement_text)}</code></p>"
            f"<p><strong>Branch arcs:</strong> <code>{_attr(arc_text)}</code></p>"
            "</div></details>"
        )
    return (
        f"<h2>{_attr(group['name'])}</h2>"
        f"<p class=\"detail-lede\"><span class=\"disposition-pill disposition-"
        f"{_attr(group['disposition'])}\">{_attr(group['disposition'])}</span> "
        f"{group['member_count']} candidate members, {group['statement_count']} "
        f"statement entries, and {group['arc_count']} arc entries.</p>"
        "<dl class=\"detail-facts\">"
        f"<div><dt>Owner</dt><dd><code>{_attr(group['owner']['kind'])}:"
        f"{_attr(group['owner']['ref'])}</code></dd></div>"
        f"<div><dt>Reason</dt><dd><code>{_attr(group['reason']['code'])}</code> "
        f"{_attr(group['reason']['detail'])}</dd></div>"
        "</dl>"
        "<details class=\"reconciliation\"><summary>Reconciliation evidence</summary>"
        f"<ul>{''.join(dimensions)}</ul></details>"
        f"<details class=\"source-members\"><summary>Members ({group['member_count']})</summary>"
        f"<div class=\"members\">{''.join(members)}</div></details>"
    )


def _dashboard_coverage_status_label(status: str) -> str:
    return {
        "covered": "covered",
        "partial": "partially covered",
        "ready": "ready to run",
        "not_covered": "not yet covered",
        "blocked": "blocked",
    }.get(status, status.replace("_", " "))


def _dashboard_coverage_detail_markup(coverage_class: Mapping[str, Any]) -> str:
    sequence_markup = []
    for sequence in coverage_class["sequences"]:
        status_label = _dashboard_coverage_status_label(sequence["status"])
        if sequence["status"] == "covered":
            next_markup = (
                '<p class="next-step next-step-covered">Exact sequence evidence is present.</p>'
            )
        else:
            owner = sequence["owner"] or "No owner recorded"
            unlock = sequence["unlock"] or sequence["prerequisites"]
            next_markup = (
                '<div class="next-step">'
                f'<strong>Next unlock</strong><p>{_attr(unlock)}</p>'
                f'<p class="muted">Owner: <code>{_attr(owner)}</code></p>'
                "</div>"
        )
        sequence_markup.append(
            '<details class="coverage-sequence">'
            "<summary>"
            f'<code>{_attr(sequence["id"])}</code>'
            f'<span class="coverage-sequence-name">{_attr(sequence["operator_outcome"])}</span>'
            f'<span class="status-pill coverage-status-{_attr(sequence["status"])}">{_attr(status_label)}</span>'
            "</summary>"
            '<div class="coverage-sequence-detail">'
            f'<p><strong>Operator question:</strong> {_attr(sequence["operator_question"])}</p>'
            f'<p><strong>Confirmation:</strong> {_attr(sequence["primary_confirmation"])}</p>'
            f'<p class="muted"><strong>Evidence:</strong> {_attr(sequence["coverage_reason"] or sequence["coverage"])}.</p>'
            f'<dl class="detail-facts"><div><dt>Prerequisites</dt><dd>{_attr(sequence["prerequisites"])}</dd></div>'
            f'<div><dt>CLI leaves</dt><dd><code>{_attr(", ".join(sequence["leaf_ids"]) or "None")}</code></dd></div>'
            f'<div><dt>Execution</dt><dd><code>{_attr(sequence["execution"])}</code> · {_attr(sequence["safety_class"])}</dd></div></dl>'
            f"{next_markup}</div></details>"
        )
    return (
        f'<h2>{_attr(coverage_class["name"])}</h2>'
        f'<p class="detail-lede"><span class="status-pill coverage-status-{_attr(coverage_class["status"])}">'
        f'{_attr(_dashboard_coverage_status_label(coverage_class["status"]))}</span> '
        f'{_attr(coverage_class["summary"])}</p>'
        f'<div class="coverage-sequence-list">{"".join(sequence_markup)}</div>'
    )


def _dashboard_command_status_label(status: str) -> str:
    return {
        "covered": "covered by measured sequence",
        "partial": "mixed coverage",
        "planned": "registered, not measured",
        "blocked": "blocked sequence",
        "uncovered": "not in a registered sequence",
    }.get(status, status.replace("_", " "))


def _dashboard_command_tree_markup(node: Mapping[str, Any]) -> str:
    status = node["status"]
    status_label = _dashboard_command_status_label(status)
    children = node["children"]
    if children:
        child_markup = "".join(
            _dashboard_command_tree_markup(child) for child in children
        )
        control = (
            '<button type="button" class="command-node command-node-toggle '
            f'command-status-{_attr(status)}" aria-expanded="false" '
            f'data-command-path="{_attr(node["command"])}" '
            f'aria-label="{_attr(node["command"])}: {_attr(status_label)}; '
            f'{len(children)} subcommands">'
            '<span class="command-chevron" aria-hidden="true">▸</span>'
            f'<code class="command-word">{_attr(node["token"])}</code>'
            f'<span class="command-state">{_attr(status_label)}</span>'
            "</button>"
            f'<ul class="command-children" hidden>{child_markup}</ul>'
        )
    else:
        control = (
            '<button type="button" class="command-node command-node-leaf '
            f'command-status-{_attr(status)}" data-command-path="{_attr(node["command"])}" '
            f'aria-label="{_attr(node["command"])}: {_attr(status_label)}">'
            '<span class="command-chevron command-chevron-spacer" aria-hidden="true">·</span>'
            f'<code class="command-word">{_attr(node["token"])}</code>'
            f'<span class="command-state">{_attr(status_label)}</span>'
            "</button>"
        )
    return (
        f'<li class="command-tree-node" data-command-path="{_attr(node["command"])}" '
        f'data-command-status="{_attr(status)}">{control}</li>'
    )


def _dashboard_command_leaf_nodes(
    node: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    leaves = []
    if node["leaf_id"] is not None:
        leaves.append(node)
    for child in node["children"]:
        leaves.extend(_dashboard_command_leaf_nodes(child))
    return leaves


def _dashboard_cli_command(command: str) -> str:
    return "./cli/" + command


def _dashboard_command_detail_markup(
    node: Mapping[str, Any],
    sequence_details_by_id: Mapping[str, Mapping[str, Any]],
) -> str:
    status = node["status"]
    status_label = _dashboard_command_status_label(status)
    leaf_nodes = _dashboard_command_leaf_nodes(node)
    if node["leaf_id"] is not None:
        inventory_summary = f'<code>{_attr(node["leaf_id"])}</code>'
    else:
        inventory_summary = f'{len(node["leaf_ids"])} inventoried leaf commands'
    sequence_rows = []
    for sequence_id in node["sequence_ids"]:
        detail = sequence_details_by_id.get(sequence_id)
        if detail is None:
            continue
        sequence = detail["sequence"]
        sequence_status = _dashboard_coverage_status_label(sequence["status"])
        if sequence["status"] == "covered":
            next_step = "Exact sequence evidence is present."
        else:
            owner = sequence["owner"] or "No owner recorded"
            unlock = sequence["unlock"] or sequence["prerequisites"]
            next_step = (
                f'Owner: <code>{_attr(owner)}</code> · '
                f'Next unlock: {_attr(unlock)}'
            )
        sequence_rows.append(
            '<li class="command-sequence-row">'
            f'<div><code>{_attr(sequence["id"])}</code> '
            f'<span class="status-pill coverage-status-{_attr(sequence["status"])}">'
            f'{_attr(sequence_status)}</span></div>'
            f'<p><strong>{_attr(sequence["operator_outcome"])}</strong></p>'
            f'<p class="muted">{_attr(detail["coverage_class_name"])} · '
            f'{_attr(sequence["operator_question"])}</p>'
            f'<p class="muted">{next_step}</p>'
            '</li>'
        )
    if sequence_rows:
        sequence_markup = (
            '<details class="detail-disclosure">'
            f'<summary>Sequences touching this subtree ({len(sequence_rows)})</summary>'
            f'<ul class="command-sequences">{"".join(sequence_rows)}</ul></details>'
        )
    else:
        sequence_markup = '<p class="muted">No registered sequence reaches this branch.</p>'

    uncovered = [
        leaf["command"] for leaf in leaf_nodes if leaf["status"] == "uncovered"
    ]
    if uncovered:
        preview = uncovered[:6]
        gap_markup = (
            f'<p class="command-gap"><strong>Uncovered leaves:</strong> '
            f'<code>{_attr(", ".join(preview))}</code></p>'
        )
        remaining = uncovered[len(preview):]
        if remaining:
            gap_markup += (
                '<details class="inline-disclosure">'
                f'<summary>+{len(remaining)} more uncovered leaves</summary>'
                f'<p class="command-gap"><code>{_attr(", ".join(remaining))}</code></p>'
                '</details>'
            )
    else:
        gap_markup = '<p class="command-gap command-gap-covered">No uncovered leaves in this subtree.</p>'
    return (
        f'<p class="detail-lede"><span class="command-status-pill command-status-{_attr(status)}">'
        f'{_attr(status_label)}</span></p>'
        '<dl class="detail-facts">'
        f'<div><dt>Inventory</dt><dd>{inventory_summary}</dd></div>'
        f'<div><dt>Sequences</dt><dd><code>{_attr(", ".join(node["sequence_ids"]) or "None")}</code></dd></div>'
        '</dl>'
        f'{sequence_markup}{gap_markup}'
    )


def render_dashboard_html(
    record: Mapping[str, Any],
    sealed: Mapping[str, Any],
    authority: Mapping[str, Any] | None = None,
) -> str:
    projection = _dashboard_projection(record, sealed, authority)
    embedded = _dashboard_script_json(projection)
    membership = projection["membership"]
    source_status = projection["source_status"]
    dispositions = projection["dispositions"]
    coverage_classes = projection["coverage_overview"]["classes"]
    command_tree = projection["command_tree"]
    sequence_details_by_id = {
        sequence["id"]: {
            "sequence": sequence,
            "coverage_class_name": coverage_class["name"],
        }
        for coverage_class in coverage_classes
        for sequence in coverage_class["sequences"]
    }
    first_coverage_class = coverage_classes[0]
    first_group = projection["groups"][0]

    coverage_rows = []
    for index, coverage_class in enumerate(coverage_classes):
        sequence_cells = "".join(
            f'<span class="coverage-cell coverage-status-{_attr(sequence["status"])}" '
            f'role="img" aria-label="{_attr(sequence["id"])}: '
            f'{_attr(_dashboard_coverage_status_label(sequence["status"]))}">'
            f'<code>{_attr(sequence["id"])}</code></span>'
            for sequence in coverage_class["sequences"]
        )
        coverage_rows.append(
            f'<button type="button" class="coverage-class-row" '
            f'data-coverage-class-id="{_attr(coverage_class["id"])}" '
            f'aria-pressed="{str(index == 0).lower()}" '
            f'aria-label="{_attr(coverage_class["name"])}: '
            f'{_attr(_dashboard_coverage_status_label(coverage_class["status"]))}">'
            f'<span class="coverage-class-name"><strong>{_attr(coverage_class["name"])}</strong>'
            f'<small>{_attr(coverage_class["summary"])}</small></span>'
            f'<span class="coverage-sequence-cells">{sequence_cells}</span>'
            f'<span class="coverage-state coverage-status-{_attr(coverage_class["status"])}">'
            f'{_attr(_dashboard_coverage_status_label(coverage_class["status"]))}</span>'
            "</button>"
        )

    covered_classes = [
        item["name"] for item in coverage_classes if item["status"] == "covered"
    ]
    open_classes = [
        item["name"]
        for item in coverage_classes
        if item["status"] in {"not_covered", "partial", "ready"}
    ]
    blocked_classes = [
        item["name"] for item in coverage_classes if item["status"] == "blocked"
    ]
    bottom_line_parts = []
    if covered_classes:
        bottom_line_parts.append(f'<strong>Covered:</strong> {_attr(", ".join(covered_classes))}.')
    if open_classes:
        bottom_line_parts.append(
            f'<strong>Not yet covered:</strong> {_attr(", ".join(open_classes))}.'
        )
    if blocked_classes:
        bottom_line_parts.append(
            f'<strong>Blocked:</strong> {_attr(", ".join(blocked_classes))}.'
        )
    coverage_bottom_line = " ".join(bottom_line_parts)

    disposition_total = sum(dispositions.values())
    disposition_segments = "".join(
        f'<span class="segment disposition-{_attr(disposition)}" '
        f'style="width:{_dashboard_percentage(count, disposition_total)}%" '
        f'aria-label="{_attr(disposition)}: {count} members"></span>'
        for disposition, count in dispositions.items()
    )
    disposition_legend = "".join(
        f'<li><span class="swatch disposition-{_attr(disposition)}"></span>'
        f'<strong>{count}</strong> {_attr(disposition)}</li>'
        for disposition, count in dispositions.items()
    )
    source_total = sum(source_status.values())
    source_segments = "".join(
        f'<span class="segment status-{_attr(status)}" '
        f'style="width:{_dashboard_percentage(count, source_total)}%" '
        f'aria-label="{_attr(status)}: {count} files"></span>'
        for status, count in source_status.items()
    )
    source_legend_labels = {
        "fully_reached": "fully journey-reached",
        "candidate_partial": "candidate paths present in report",
        "candidate_absent": "absent from journey report",
    }
    source_legend = "".join(
        f'<li><span class="swatch status-{_attr(status)}"></span>'
        f'<strong>{count}</strong> {_attr(source_legend_labels[status])}</li>'
        for status, count in source_status.items()
    )
    group_rows = []
    for index, group in enumerate(projection["groups"]):
        group_rows.append(
            f'<button type="button" class="group-row" '
            f'data-group-id="{_attr(group["id"])}" aria-pressed="{str(index == 0).lower()}" '
            f'aria-label="{_attr(group["name"])}: {group["member_count"]} members">'
            f'<span class="group-name"><strong>{_attr(group["name"])}</strong>'
            f'<small>{_attr(group["reason"]["code"])}</small></span>'
            f'<span class="group-disposition disposition-pill disposition-{_attr(group["disposition"])}">'
            f'{_attr(group["disposition"])}</span>'
            f'<span class="group-value">{group["member_count"]} members</span>'
            "</button>"
        )

    style = """
:root {
  color-scheme: light;
  --bg: #f6f7f9;
  --surface: #ffffff;
  --surface-alt: #eef1f5;
  --text: #17202a;
  --muted: #5e6a78;
  --border: #d7dde5;
  --focus: #1e6bb8;
  --covered: #4b8a68;
  --partial: #356f8f;
  --not-covered: #b4873f;
  --blocked: #8d3f55;
  --expose: #b4512b;
  --retain: #356f8f;
  --remove: #8d3f55;
  --fully: #4b8a68;
  --partial-file: #356f8f;
  --absent: #a9b2bd;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --bg: #101419;
    --surface: #171d24;
    --surface-alt: #232b35;
    --text: #edf1f5;
    --muted: #aab5c1;
    --border: #3a4653;
    --focus: #6bb5f0;
    --covered: #78c39a;
    --partial: #73b5d5;
    --not-covered: #e1bd73;
    --blocked: #e28aa2;
    --expose: #e48a61;
    --retain: #73b5d5;
    --remove: #e28aa2;
    --fully: #78c39a;
    --partial-file: #73b5d5;
    --absent: #697583;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 15px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
a { color: var(--focus); }
code, .group-value, .member-counts { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.dashboard { max-width: 1160px; margin: 0 auto; padding: 32px clamp(16px, 4vw, 48px) 64px; }
.eyebrow { color: var(--muted); font-size: .78rem; letter-spacing: .08em; text-transform: uppercase; }
h1, h2, h3 { line-height: 1.2; }
h1 { margin: .25rem 0 .5rem; font-size: clamp(1.8rem, 4vw, 2.6rem); }
h2 { margin: 0 0 .45rem; font-size: 1.15rem; }
h3 { margin: 1.3rem 0 .65rem; font-size: .98rem; }
.lede { max-width: 900px; color: var(--muted); margin: 0; }
.notice { margin: 18px 0 26px; padding: 12px 14px; border-left: 4px solid var(--partial); background: var(--surface); color: var(--muted); }
.coverage-bottom-line { margin: 22px 0 14px; padding: 14px 16px; background: var(--surface); border-left: 4px solid var(--covered); }
.dashboard-toc { display: flex; flex-wrap: wrap; gap: 7px 14px; align-items: baseline; margin: 18px 0 10px; color: var(--muted); font-size: .86rem; }
.dashboard-toc strong { color: var(--text); }
.dashboard-explainer, .section-explainer, .source-explorer-disclosure { margin: 14px 0; }
.dashboard-explainer > summary, .section-explainer > summary, .source-explorer-disclosure > summary { cursor: pointer; color: var(--focus); font-size: .84rem; }
.dashboard-explainer > summary { font-weight: 600; }
.section-explainer .caption, .source-explorer-disclosure .caption { margin-top: 10px; }
.disposition-definitions { margin: 14px 0; }
.disposition-definitions > summary { cursor: pointer; color: var(--focus); font-size: .84rem; }
.disposition-definitions > p { margin: 10px 0; color: var(--muted); font-size: .84rem; }
.disposition-definitions dl { display: grid; grid-template-columns: minmax(82px, .2fr) 1fr; gap: 7px 12px; margin: 0; font-size: .84rem; }
.disposition-definitions dt { font-weight: 600; }
.disposition-definitions dd { margin: 0; color: var(--muted); }
.panel { min-width: 0; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 18px; }
.panel-header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.muted, .caption { color: var(--muted); }
.caption { font-size: .86rem; margin: 0 0 14px; }
.command-explorer-panel { margin-top: 18px; }
.command-selection { margin: 16px 0; padding: 10px 12px; background: var(--surface-alt); border: 1px solid var(--border); border-radius: 6px; overflow-wrap: anywhere; }
.command-selection code { font-size: 1.04em; }
.command-explorer-layout { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(280px, .95fr); gap: 18px; align-items: start; }
.command-detail { min-width: 0; border-left: 1px solid var(--border); padding-left: 18px; }
.command-detail h3 { margin-top: 0; overflow-wrap: anywhere; }
.command-detail h4 { margin: 20px 0 8px; font-size: .9rem; }
.command-tree, .command-children { list-style: none; margin: 0; padding: 0; }
.command-tree { margin-top: 12px; }
.command-children { margin: 3px 0 3px 17px; padding-left: 15px; border-left: 1px solid var(--border); }
.command-tree-node { margin: 2px 0; }
.command-node { display: flex; align-items: center; gap: 8px; width: fit-content; max-width: 100%; padding: 4px 7px; color: var(--text); text-align: left; background: transparent; border: 0; border-radius: 5px; }
.command-node-toggle, .command-node-leaf { cursor: pointer; }
.command-node:hover { background: var(--surface-alt); }
.command-node[aria-current="true"] { background: var(--surface-alt); outline: 2px solid var(--focus); outline-offset: -2px; }
.command-node:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.command-chevron { flex: 0 0 15px; color: var(--muted); font-size: .95rem; }
.command-chevron-spacer { text-align: center; }
.command-word { font-size: .95rem; }
.command-state { color: var(--muted); font-size: .76rem; overflow-wrap: anywhere; }
.command-status-covered .command-word { color: var(--covered); }
.command-status-partial .command-word { color: var(--partial); }
.command-status-planned .command-word { color: var(--not-covered); }
.command-status-blocked .command-word { color: var(--blocked); }
.command-status-uncovered .command-word { color: var(--muted); }
.command-legend { display: flex; flex-wrap: wrap; gap: 7px 16px; margin: 12px 0 0; padding: 0; list-style: none; color: var(--muted); font-size: .82rem; }
.command-legend li { display: flex; align-items: center; gap: 6px; }
.command-legend .swatch { border: 1px solid; }
.command-legend .swatch.command-status-covered { background: var(--covered); }
.command-legend .swatch.command-status-partial { background: var(--partial); }
.command-legend .swatch.command-status-planned { background: var(--not-covered); }
.command-legend .swatch.command-status-blocked { background: var(--blocked); }
.command-legend .swatch.command-status-uncovered { background: var(--muted); }
.command-status-pill { display: inline-block; padding: 4px 8px; border: 1px solid; border-radius: 999px; font-size: .78rem; font-weight: 600; }
.command-status-pill.command-status-covered { color: var(--covered); border-color: var(--covered); }
.command-status-pill.command-status-partial { color: var(--partial); border-color: var(--partial); }
.command-status-pill.command-status-planned { color: var(--not-covered); border-color: var(--not-covered); }
.command-status-pill.command-status-blocked { color: var(--blocked); border-color: var(--blocked); }
.command-status-pill.command-status-uncovered { color: var(--muted); border-color: var(--muted); }
.command-sequences { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
.command-sequence-row { border-top: 1px solid var(--border); padding: 9px 0 4px; }
.command-sequence-row p { margin: 5px 0; overflow-wrap: anywhere; }
.detail-disclosure, .source-members { margin-top: 16px; border-top: 1px solid var(--border); }
.detail-disclosure > summary, .source-members > summary { cursor: pointer; padding: 9px 0; color: var(--focus); font-weight: 600; }
.inline-disclosure { margin-top: 6px; }
.inline-disclosure > summary { cursor: pointer; color: var(--focus); font-size: .82rem; }
.command-gap { margin: 14px 0 0; padding: 9px 10px; background: var(--surface-alt); font-size: .82rem; overflow-wrap: anywhere; }
.command-gap-covered { color: var(--covered); }
.coverage-map { display: grid; gap: 5px; }
.coverage-class-row { display: grid; grid-template-columns: minmax(210px, 1.15fr) minmax(180px, 1fr) minmax(125px, .55fr); gap: 12px; align-items: center; width: 100%; padding: 12px 10px; color: var(--text); text-align: left; background: transparent; border: 0; border-radius: 7px; cursor: pointer; }
.coverage-class-row:hover, .coverage-class-row[aria-pressed="true"] { background: var(--surface-alt); }
.coverage-class-row[aria-pressed="true"] { outline: 2px solid var(--focus); outline-offset: -2px; }
.coverage-class-name { min-width: 0; }
.coverage-class-name strong, .coverage-class-name small { display: block; overflow-wrap: anywhere; }
.coverage-class-name small { color: var(--muted); font-size: .8rem; }
.coverage-sequence-cells { display: flex; flex-wrap: wrap; gap: 5px; }
.coverage-cell { display: inline-flex; align-items: center; justify-content: center; min-width: 52px; padding: 6px 8px; border: 1px solid var(--border); border-radius: 5px; }
.coverage-cell code { font-size: .78rem; }
.coverage-state, .status-pill { display: inline-block; width: fit-content; padding: 4px 8px; border: 1px solid; border-radius: 999px; font-size: .78rem; font-weight: 600; }
.coverage-state { justify-self: start; }
.coverage-status-covered { color: var(--covered); border-color: var(--covered); }
.coverage-status-partial { color: var(--partial); border-color: var(--partial); }
.coverage-status-not_covered, .coverage-status-ready { color: var(--not-covered); border-color: var(--not-covered); }
.coverage-status-blocked { color: var(--blocked); border-color: var(--blocked); }
.coverage-cell.coverage-status-covered { background: var(--surface-alt); }
.coverage-cell.coverage-status-partial { background: var(--surface-alt); }
.coverage-cell.coverage-status-not_covered, .coverage-cell.coverage-status-ready { background: var(--surface-alt); }
.coverage-cell.coverage-status-blocked { background: var(--surface-alt); }
.coverage-explorer-layout { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(300px, .95fr); gap: 18px; align-items: start; }
.coverage-explorer-list { min-width: 0; }
.coverage-legend { display: flex; flex-wrap: wrap; gap: 7px 16px; margin: 12px 0 0; padding: 0; list-style: none; color: var(--muted); font-size: .82rem; }
.coverage-legend li { display: flex; align-items: center; gap: 6px; }
.coverage-legend .swatch { border: 1px solid; }
.coverage-detail { min-width: 0; border-left: 1px solid var(--border); padding-left: 18px; }
.coverage-sequence-list { display: grid; gap: 6px; }
.coverage-sequence { border-top: 1px solid var(--border); }
.coverage-sequence summary { display: grid; grid-template-columns: 52px minmax(0, 1fr) auto; gap: 8px; align-items: center; cursor: pointer; padding: 10px 0; }
.dashboard-explainer > summary:focus-visible, .section-explainer > summary:focus-visible, .source-explorer-disclosure > summary:focus-visible, .disposition-definitions > summary:focus-visible, .detail-disclosure > summary:focus-visible, .inline-disclosure > summary:focus-visible, .coverage-sequence summary:focus-visible, .group-row:focus-visible, .coverage-class-row:focus-visible, .member summary:focus-visible, .reconciliation summary:focus-visible, .source-members > summary:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.coverage-sequence-name { min-width: 0; overflow-wrap: anywhere; }
.coverage-sequence-detail { padding: 2px 0 12px 60px; font-size: .86rem; }
.coverage-sequence-detail p { margin: 7px 0; }
.next-step { margin-top: 12px; padding: 10px 12px; background: var(--surface-alt); }
.next-step p { margin: 3px 0; }
.next-step-covered { color: var(--covered); }
.source-explorer-layout { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(300px, .7fr); gap: 18px; align-items: start; margin-top: 14px; }
.source-explorer-list, .source-detail { min-width: 0; }
.source-detail { border-left: 1px solid var(--border); padding-left: 18px; }
.group-chart { display: grid; gap: 5px; }
.group-row { display: grid; grid-template-columns: minmax(165px, 1.3fr) auto 100px; gap: 10px; align-items: center; width: 100%; padding: 9px 7px; color: var(--text); text-align: left; background: transparent; border: 0; border-radius: 6px; cursor: pointer; }
.group-row:hover, .group-row[aria-pressed="true"] { background: var(--surface-alt); }
.group-row[aria-pressed="true"] { outline: 2px solid var(--focus); outline-offset: -2px; }
.group-name { min-width: 0; overflow-wrap: anywhere; }
.group-name strong, .group-name small { display: block; overflow-wrap: anywhere; }
.group-name small { color: var(--muted); font-size: .76rem; }
.group-value { text-align: right; font-weight: 600; }
.group-disposition { justify-self: start; }
.disposition-expose { color: var(--expose); border-color: var(--expose); }
.disposition-retain { color: var(--retain); border-color: var(--retain); }
.disposition-remove { color: var(--remove); border-color: var(--remove); }
.chart-foot { margin: 14px 0 0; color: var(--muted); font-size: .8rem; }
.composition { margin-top: 24px; }
.stacked-bar { display: flex; width: 100%; height: 18px; overflow: hidden; background: var(--surface-alt); border-radius: 4px; }
.segment { display: block; min-width: 0; }
.legend { display: flex; flex-wrap: wrap; gap: 7px 16px; margin: 10px 0 0; padding: 0; list-style: none; color: var(--muted); font-size: .82rem; }
.legend li { display: flex; align-items: center; gap: 6px; }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; }
.swatch.disposition-expose, .segment.disposition-expose { background: var(--expose); }
.swatch.disposition-retain, .segment.disposition-retain { background: var(--retain); }
.swatch.disposition-remove, .segment.disposition-remove { background: var(--remove); }
.status-fully_reached, .swatch.status-fully_reached, .segment.status-fully_reached { background: var(--fully); }
.status-candidate_partial, .swatch.status-candidate_partial, .segment.status-candidate_partial { background: var(--partial-file); }
.status-candidate_absent, .swatch.status-candidate_absent, .segment.status-candidate_absent { background: var(--absent); }
.detail-lede { margin: 0 0 16px; color: var(--muted); }
.detail-facts { display: grid; gap: 8px; margin: 0; }
.detail-facts div { display: grid; grid-template-columns: 92px 1fr; gap: 10px; }
.detail-facts dt { color: var(--muted); }
.detail-facts dd { margin: 0; overflow-wrap: anywhere; }
.reconciliation { margin: 16px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.reconciliation summary, .member summary { cursor: pointer; padding: 9px 0; }
.reconciliation ul { margin: 0 0 10px; padding-left: 20px; }
.reconciliation li { margin: 5px 0; }
.reconciliation li ul { margin: 3px 0; }
.members { display: grid; gap: 6px; }
.member { border: 1px solid var(--border); border-radius: 6px; padding: 0 10px; }
.member summary { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.member-counts { color: var(--muted); font-size: .76rem; }
.member-detail { border-top: 1px solid var(--border); padding: 8px 0 4px; overflow-wrap: anywhere; }
.member-detail p { margin: 7px 0; }
.member-detail code { white-space: pre-wrap; word-break: break-word; }
.residual-note { margin: 16px 0 0; padding: 10px 12px; background: var(--surface-alt); color: var(--muted); font-size: .82rem; }
@media (max-width: 1040px) { .command-explorer-layout, .coverage-explorer-layout, .source-explorer-layout { grid-template-columns: 1fr; } .command-detail, .coverage-detail, .source-detail { border-left: 0; border-top: 1px solid var(--border); padding: 18px 0 0; } }
@media (max-width: 720px) { .coverage-class-row { grid-template-columns: 1fr; gap: 8px; } .coverage-state { justify-self: start; } }
@media (max-width: 560px) { .dashboard { padding-top: 22px; } .coverage-sequence summary { grid-template-columns: 42px minmax(0, 1fr); } .coverage-sequence summary .status-pill { grid-column: 2; } .group-row { grid-template-columns: 1fr auto; } .group-name { grid-column: 1 / -1; } .group-value { grid-column: 2; grid-row: 2; } .detail-facts div { grid-template-columns: 1fr; gap: 2px; } .disposition-definitions dl { grid-template-columns: 1fr; gap: 2px; } .disposition-definitions dd { margin-bottom: 7px; } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; } }
"""
    script = r"""
(function () {
  const data = JSON.parse(document.getElementById("dashboard-data").textContent);
  const coverageDetail = document.getElementById("coverage-detail");
  const coverageButtons = Array.from(document.querySelectorAll("button.coverage-class-row"));
  const groupDetail = document.getElementById("group-detail");
  const groupButtons = Array.from(document.querySelectorAll("button.group-row"));
  const commandExplorer = document.getElementById("command-explorer");
  const commandSelection = document.getElementById("command-selection");
  const commandDetail = document.getElementById("command-detail");
  const commandButtons = Array.from(document.querySelectorAll("button.command-node"));
  const commandNodes = new Map();
  const sequencesById = new Map();

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (character) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[character];
    });
  }

  function listMarkup(values, formatter) {
    return values.map(formatter).join(", ");
  }

  function statusLabel(status) {
    return {
      covered: "covered",
      partial: "partially covered",
      ready: "ready to run",
      not_covered: "not yet covered",
      blocked: "blocked"
    }[status] || status.replace(/_/g, " ");
  }

  function commandStatusLabel(status) {
    return {
      covered: "covered by measured sequence",
      partial: "mixed coverage",
      planned: "registered, not measured",
      blocked: "blocked sequence",
      uncovered: "not in a registered sequence"
    }[status] || status.replace(/_/g, " ");
  }

  function indexCommandNodes(node) {
    commandNodes.set(node.command, node);
    node.children.forEach(indexCommandNodes);
  }

  indexCommandNodes(data.command_tree);
  data.coverage_overview.classes.forEach(function (coverageClass) {
    coverageClass.sequences.forEach(function (sequence) {
      sequencesById.set(sequence.id, Object.assign({}, sequence, {
        coverage_class_name: coverageClass.name
      }));
    });
  });

  function coverageDetailMarkup(coverageClass) {
    const sequences = coverageClass.sequences.map(function (sequence) {
      const nextMarkup = sequence.status === "covered"
        ? "<p class=\"next-step next-step-covered\">Exact sequence evidence is present.</p>"
        : "<div class=\"next-step\"><strong>Next unlock</strong><p>" + escapeHtml(sequence.unlock || sequence.prerequisites) + "</p><p class=\"muted\">Owner: <code>" + escapeHtml(sequence.owner || "No owner recorded") + "</code></p></div>";
      return "<details class=\"coverage-sequence\"><summary><code>" + escapeHtml(sequence.id) + "</code><span class=\"coverage-sequence-name\">" + escapeHtml(sequence.operator_outcome) + "</span><span class=\"status-pill coverage-status-" + escapeHtml(sequence.status) + "\">" + escapeHtml(statusLabel(sequence.status)) + "</span></summary><div class=\"coverage-sequence-detail\"><p><strong>Operator question:</strong> " + escapeHtml(sequence.operator_question) + "</p><p><strong>Confirmation:</strong> " + escapeHtml(sequence.primary_confirmation) + "</p><p class=\"muted\"><strong>Evidence:</strong> " + escapeHtml(sequence.coverage_reason || sequence.coverage) + ".</p><dl class=\"detail-facts\"><div><dt>Prerequisites</dt><dd>" + escapeHtml(sequence.prerequisites) + "</dd></div><div><dt>CLI leaves</dt><dd><code>" + escapeHtml(sequence.leaf_ids.join(", ") || "None") + "</code></dd></div><div><dt>Execution</dt><dd><code>" + escapeHtml(sequence.execution) + "</code> · " + escapeHtml(sequence.safety_class) + "</dd></div></dl>" + nextMarkup + "</div></details>";
    }).join("");
    return "<h2>" + escapeHtml(coverageClass.name) + "</h2><p class=\"detail-lede\"><span class=\"status-pill coverage-status-" + escapeHtml(coverageClass.status) + "\">" + escapeHtml(statusLabel(coverageClass.status)) + "</span> " + escapeHtml(coverageClass.summary) + "</p><div class=\"coverage-sequence-list\">" + sequences + "</div>";
  }

  function commandLeafNodes(node) {
    const leaves = node.leaf_id ? [node] : [];
    node.children.forEach(function (child) {
      leaves.push.apply(leaves, commandLeafNodes(child));
    });
    return leaves;
  }

  function commandSelectionMarkup(node) {
    const cliCommand = "./cli/" + node.command;
    return "<span class=\"muted\">CLI command:</span> <code>" + escapeHtml(cliCommand) + "</code>";
  }

  function commandDetailMarkup(node) {
    const sequenceRows = node.sequence_ids.map(function (sequenceId) {
      return sequencesById.get(sequenceId);
    }).filter(Boolean);
    const sequenceMarkup = sequenceRows.length
      ? "<details class=\"detail-disclosure\"><summary>Sequences touching this subtree (" + sequenceRows.length + ")</summary><ul class=\"command-sequences\">" + sequenceRows.map(function (sequence) {
        const nextStep = sequence.status === "covered"
          ? "Exact sequence evidence is present."
          : "Owner: <code>" + escapeHtml(sequence.owner || "No owner recorded") + "</code> · Next unlock: " + escapeHtml(sequence.unlock || sequence.prerequisites);
        return "<li class=\"command-sequence-row\"><div><code>" + escapeHtml(sequence.id) + "</code> <span class=\"status-pill coverage-status-" + escapeHtml(sequence.status) + "\">" + escapeHtml(statusLabel(sequence.status)) + "</span></div><p><strong>" + escapeHtml(sequence.operator_outcome) + "</strong></p><p class=\"muted\">" + escapeHtml(sequence.coverage_class_name) + " · " + escapeHtml(sequence.operator_question) + "</p><p class=\"muted\">" + nextStep + "</p></li>";
      }).join("") + "</ul></details>"
      : "<p class=\"muted\">No registered sequence reaches this branch.</p>";
    const leaves = commandLeafNodes(node);
    const uncovered = leaves.filter(function (leaf) { return leaf.status === "uncovered"; }).map(function (leaf) { return leaf.command; });
    const uncoveredPreview = uncovered.slice(0, 6).join(", ");
    const uncoveredRemaining = uncovered.slice(6).join(", ");
    const uncoveredMoreMarkup = uncovered.length > 6
      ? "<details class=\"inline-disclosure\"><summary>+" + (uncovered.length - 6) + " more uncovered leaves</summary><p class=\"command-gap\"><code>" + escapeHtml(uncoveredRemaining) + "</code></p></details>"
      : "";
    const gapMarkup = uncovered.length
      ? "<p class=\"command-gap\"><strong>Uncovered leaves:</strong> <code>" + escapeHtml(uncoveredPreview) + "</code></p>" + uncoveredMoreMarkup
      : "<p class=\"command-gap command-gap-covered\">No uncovered leaves in this subtree.</p>";
    const inventory = node.leaf_id
      ? "<code>" + escapeHtml(node.leaf_id) + "</code>"
      : node.leaf_ids.length + " inventoried leaf commands";
    return "<p class=\"detail-lede\"><span class=\"command-status-pill command-status-" + escapeHtml(node.status) + "\">" + escapeHtml(commandStatusLabel(node.status)) + "</span></p><dl class=\"detail-facts\"><div><dt>Inventory</dt><dd>" + inventory + "</dd></div><div><dt>Sequences</dt><dd><code>" + escapeHtml(node.sequence_ids.join(", ") || "None") + "</code></dd></div></dl>" + sequenceMarkup + gapMarkup;
  }

  function detailMarkup(group) {
    const dimensions = Object.entries(group.reconcile).map(function (entry) {
      const dimension = entry[0];
      const item = entry[1];
      const refs = item.refs.length
        ? "<ul>" + item.refs.map(function (ref) { return "<li><code>" + escapeHtml(ref) + "</code></li>"; }).join("") + "</ul>"
        : "<span class=\"muted\">No typed references.</span>";
      return "<li><strong>" + escapeHtml(dimension) + "</strong>: <code>" + escapeHtml(item.status) + "</code> " + refs + "</li>";
    }).join("");
    const members = group.members.map(function (member) {
      const statements = listMarkup(member.unreached_statements, function (value) { return escapeHtml(value); });
      const arcs = listMarkup(member.unreached_arcs, function (arc) { return escapeHtml(arc[0] + " → " + arc[1]); });
      return "<details class=\"member\" data-member-path=\"" + escapeHtml(member.path) + "\"><summary><code>" + escapeHtml(member.path) + "</code><span class=\"member-counts\">" + member.unreached_statements.length + " statements · " + member.unreached_arcs.length + " arcs</span></summary><div class=\"member-detail\"><p class=\"muted\">Source SHA-256: <code>" + escapeHtml(member.source_sha256) + "</code></p><p><strong>Statement lines:</strong> <code>" + statements + "</code></p><p><strong>Branch arcs:</strong> <code>" + arcs + "</code></p></div></details>";
    }).join("");
    return "<h2>" + escapeHtml(group.name) + "</h2><p class=\"detail-lede\"><span class=\"status-pill disposition-" + escapeHtml(group.disposition) + "\">" + escapeHtml(group.disposition) + "</span> " + group.member_count + " candidate members, " + group.statement_count + " statement entries, and " + group.arc_count + " arc entries.</p><dl class=\"detail-facts\"><div><dt>Owner</dt><dd><code>" + escapeHtml(group.owner.kind + ":" + group.owner.ref) + "</code></dd></div><div><dt>Reason</dt><dd><code>" + escapeHtml(group.reason.code) + "</code> " + escapeHtml(group.reason.detail) + "</dd></div></dl><details class=\"reconciliation\"><summary>Reconciliation evidence</summary><ul>" + dimensions + "</ul></details><details class=\"source-members\"><summary>Members (" + group.member_count + ")</summary><div class=\"members\">" + members + "</div></details>";
  }

  function selectCoverageClass(classId) {
    const coverageClass = data.coverage_overview.classes.find(function (candidate) { return candidate.id === classId; });
    if (!coverageClass) return;
    coverageButtons.forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.coverageClassId === classId));
    });
    coverageDetail.innerHTML = coverageDetailMarkup(coverageClass);
    coverageDetail.dataset.initialCoverageClassId = classId;
  }

  function selectGroup(groupId) {
    const group = data.groups.find(function (candidate) { return candidate.id === groupId; });
    if (!group) return;
    groupButtons.forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.groupId === groupId));
    });
    groupDetail.innerHTML = detailMarkup(group);
    groupDetail.dataset.initialGroupId = groupId;
  }

  function selectCommand(command) {
    const node = commandNodes.get(command);
    if (!node) return;
    commandButtons.forEach(function (button) {
      if (button.dataset.commandPath === command) {
        button.setAttribute("aria-current", "true");
      } else {
        button.removeAttribute("aria-current");
      }
    });
    commandSelection.innerHTML = commandSelectionMarkup(node);
    commandDetail.innerHTML = commandDetailMarkup(node);
    commandDetail.dataset.initialCommandPath = command;
  }

  commandExplorer.addEventListener("click", function (event) {
    const button = event.target.closest("button.command-node");
    if (!button || !commandExplorer.contains(button)) return;
    const nodeElement = button.parentElement;
    const children = Array.from(nodeElement.children).find(function (child) {
      return child.classList.contains("command-children");
    });
    if (children) {
      const expanded = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", String(!expanded));
      children.hidden = expanded;
      button.querySelector(".command-chevron").textContent = expanded ? "▸" : "▾";
    }
    selectCommand(button.dataset.commandPath);
  });

  coverageButtons.forEach(function (button) {
    button.addEventListener("click", function () { selectCoverageClass(button.dataset.coverageClassId); });
  });
  groupButtons.forEach(function (button) {
    button.addEventListener("click", function () { selectGroup(button.dataset.groupId); });
  });
  selectCommand("automa");
}());
"""
    lines = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f'<meta name="record-sha256" content="{_attr(record["integrity"]["record_sha256"])}">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>M007-09 operator capability coverage</title>",
        f"<style>{style}</style></head><body>",
        '<main class="dashboard" id="capability-dashboard">',
        '<header><div class="eyebrow">M007-09 · derived evidence view</div>',
        "<h1>Operator capability coverage</h1></header>",
        '<details class="dashboard-explainer">',
        '<summary>How to read this dashboard</summary>',
        '<p class="lede">The bottom line is organized around intended operator outcomes: what is covered, what is not yet covered, and what is blocked. Each cell is a registered M007-08 usage sequence; the source capability view is a separate evidence layer below.</p>',
        '<p class="notice">This view does not infer missing product requirements from executed code. “Covered” means the exact registered sequence has passed measured evidence. Related family coverage does not promote a deferred sequence. <a href="../cli-surface-audit/rollup.md">Open the sequence rollup</a> · <a href="../cli-surface-audit/sequence_registry.json">Open the sequence registry</a> · <a href="../cli-journey-coverage/README.md">Open journey coverage evidence</a> · <a href="record.html">Open the complete audit ledger</a> · <a href="record.json">Open record.json</a></p>',
        '</details>',
        '<nav class="dashboard-toc" id="dashboard-toc" aria-label="Dashboard sections">',
        '<strong>Jump to:</strong>',
        '<a href="#command-explorer-heading" data-dashboard-toc="#command-explorer-heading">CLI command tree</a>',
        '<a href="#coverage-map-heading" data-dashboard-toc="#coverage-map-heading">Operator outcome coverage</a>',
        '<a href="#source-capability-heading" data-dashboard-toc="#source-capability-heading">Source capability and disposition</a>',
        '</nav>',
        f'<p class="coverage-bottom-line"><strong>Bottom line:</strong> {coverage_bottom_line}</p>',
        '<section class="panel command-explorer-panel" aria-labelledby="command-explorer-heading">',
        '<div class="panel-header"><h2 id="command-explorer-heading">Explore the CLI command tree</h2><span class="muted">Open branches or inspect terminal commands</span></div>',
        '<details class="section-explainer"><summary>About the command tree</summary><p class="caption">The tree follows the CLI hierarchy. A leaf is covered only when a measured sequence reaches that exact command; parent words summarize the mix below them.</p></details>',
        f'<div class="command-selection" id="command-selection" aria-live="polite"><span class="muted">CLI command:</span> <code>{_attr(_dashboard_cli_command(command_tree["command"]))}</code></div>',
        '<div class="command-explorer-layout">',
        f'<div class="command-explorer" id="command-explorer" aria-label="Recursive CLI command coverage"><ul class="command-tree">{_dashboard_command_tree_markup(command_tree)}</ul></div>',
        f'<aside class="command-detail" id="command-detail" data-initial-command-path="{_attr(command_tree["command"])}" aria-live="polite">{_dashboard_command_detail_markup(command_tree, sequence_details_by_id)}</aside>',
        '</div>',
        '<ul class="command-legend" aria-label="CLI command coverage legend">',
        '<li><span class="swatch command-status-covered"></span>Covered by measured sequence</li>',
        '<li><span class="swatch command-status-partial"></span>Mixed coverage</li>',
        '<li><span class="swatch command-status-planned"></span>Registered, not measured</li>',
        '<li><span class="swatch command-status-blocked"></span>Blocked sequence</li>',
        '<li><span class="swatch command-status-uncovered"></span>Not in a registered sequence</li>',
        '</ul>',
        '</section>',
        '<section class="panel" aria-labelledby="coverage-map-heading">',
        '<div class="panel-header"><h2 id="coverage-map-heading">Coverage by intended operator outcome</h2><span class="muted">Select a class for the next unlock</span></div>',
        '<details class="section-explainer"><summary>About operator outcome coverage</summary><p class="caption">The class names are a presentation grouping of the ten registered operator outcomes. They are not a new product scope or a claim that all source code belongs to an operator journey.</p></details>',
        '<div class="coverage-explorer-layout">',
        '<div class="coverage-explorer-list">',
        f'<div class="coverage-map" id="coverage-map" role="list">{"".join(coverage_rows)}</div>',
        '<ul class="coverage-legend" aria-label="Coverage status legend">',
        '<li><span class="swatch coverage-status-covered"></span>Covered</li>',
        '<li><span class="swatch coverage-status-partial"></span>Partially covered</li>',
        '<li><span class="swatch coverage-status-not_covered"></span>Not yet covered</li>',
        '<li><span class="swatch coverage-status-blocked"></span>Blocked</li>',
        '</ul>',
        '</div>',
        f'<aside class="coverage-detail" id="coverage-detail" data-initial-coverage-class-id="{_attr(first_coverage_class["id"])}" aria-live="polite">{_dashboard_coverage_detail_markup(first_coverage_class)}</aside>',
        '</div>',
        '</section>',
        '<section class="panel source-capability-panel" aria-labelledby="source-capability-heading">',
        '<div class="panel-header"><h2 id="source-capability-heading">Source capability and disposition</h2><span class="muted">Select a source group for evidence</span></div>',
        '<details class="source-explorer-disclosure">',
        '<summary>Open source capability groups</summary>',
        '<p class="caption">This is the M007-09 source-side explanation for capabilities outside the declared journeys. It identifies candidates and ownership; it does not decide which work should be prioritized.</p>',
        _disposition_definitions_markup("../../proposals/capability-disposition.md"),
        '<div class="source-explorer-layout">',
        '<div class="source-explorer-list">',
        f'<div class="group-chart" id="group-chart" role="list">{"".join(group_rows)}</div>',
        f'<p class="chart-foot">Residuals: {membership["unassigned_members"]} unassigned members · {membership["unresolved_region_refs"]} unresolved region references.</p>',
        '<div class="composition"><h3>Disposition mix</h3><p class="caption">Member allocation across later-review candidates.</p>',
        f'<div class="stacked-bar" id="disposition-chart" role="img" aria-label="Disposition mix: {dispositions["expose"]} expose, {dispositions["retain"]} retain, {dispositions["remove"]} remove">{disposition_segments}</div><ul class="legend">{disposition_legend}</ul></div>',
        '<div class="composition"><h3>Source-status composition</h3><p class="caption">How the sealed source members relate to the journey report.</p>',
        f'<div class="stacked-bar" id="source-status-chart" role="img" aria-label="Source status: {source_status["fully_reached"]} fully journey-reached, {source_status["candidate_partial"]} candidate paths present in the report, {source_status["candidate_absent"]} absent from the journey report">{source_segments}</div><ul class="legend">{source_legend}</ul></div>',
        '</div>',
        f'<aside class="source-detail" id="group-detail" data-initial-group-id="{_attr(first_group["id"])}" aria-live="polite">{_dashboard_detail_markup(first_group)}</aside>',
        '</div>',
        '</details>',
        '</section>',
        '<p class="residual-note">The coverage denominator is the frozen M007-08 sequence registry. The source capability view is bound to the sealed M007-07 report, historical source-analysis runtime, and M007-08 input manifest. Later source changes require a refreshed disposition review.</p>',
        f'<script type="application/json" id="dashboard-data">{embedded}</script>',
        f"<script>{script}</script>",
        '</main></body></html>',
    ]
    return "\n".join(lines) + "\n"


def render_html(record: Mapping[str, Any]) -> str:
    embedded = html.escape(canonical_json_bytes(record).decode("utf-8"))
    lines = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f'<meta name="record-sha256" content="{_attr(record["integrity"]["record_sha256"])}">',
        "<title>M007-09 capability disposition</title>",
        "<style>body{font:14px system-ui,sans-serif;margin:2rem}"
        "table{border-collapse:collapse}th,td{border:1px solid #bbb;padding:.35rem;vertical-align:top}"
        "code,pre{font-family:ui-monospace,monospace}pre{white-space:pre-wrap}"
        ".candidate{color:#555}</style></head><body>",
        "<h1>M007-09 capability disposition</h1>",
        f"<p>Record digest: <code>{_attr(record['integrity']['record_sha256'])}</code></p>",
        "<p class=\"candidate\">Unreached does not mean dead; dispositions are later-review candidates.</p>",
        _disposition_definitions_markup("../../proposals/capability-disposition.md"),
    ]
    for group in record["groups"]:
        lines.extend(
            [
                f'<section data-group-id="{_attr(group["id"])}">',
                f"<h2>{html.escape(group['name'])}</h2>",
                f"<p>Disposition: <code>{_attr(group['disposition'])}</code>; "
                f"owner: <code>{_attr(group['owner']['kind'])}:{_attr(group['owner']['ref'])}</code>; "
                f"reason: <code>{_attr(group['reason']['code'])}</code> "
                f"{html.escape(group['reason']['detail'])}</p>",
                "<table><thead><tr><th>Member</th><th>Source SHA</th>"
                "<th>Unreached statements</th><th>Unreached arcs</th></tr></thead><tbody>",
            ]
        )
        for member in group["members"]:
            lines.append(
                f'<tr data-group-id="{_attr(group["id"])}" '
                f'data-member-path="{_attr(member["path"])}" '
                f'data-source-sha256="{_attr(member["source_sha256"])}" '
                f'data-statements="{_attr(json.dumps(member["unreached_statements"], separators=(",", ":")))}" '
                f'data-arcs="{_attr(json.dumps(member["unreached_arcs"], separators=(",", ":")))}">'
                f"<td><code>{_attr(member['path'])}</code></td>"
                f"<td><code>{_attr(member['source_sha256'])}</code></td>"
                f"<td>{len(member['unreached_statements'])}</td>"
                f"<td>{len(member['unreached_arcs'])}</td></tr>"
            )
        lines.extend(["</tbody></table>", "</section>"])
    lines.extend(
        [
            '<h2>Canonical record data</h2>',
            '<pre id="record-json">' + embedded + "</pre>",
            "</body></html>",
        ]
    )
    return "\n".join(lines) + "\n"


class _EvidenceHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.record_chunks: list[str] = []
        self.in_record = False
        self.groups: list[str] = []
        self.members: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "pre" and values.get("id") == "record-json":
            self.in_record = True
        if tag == "section" and values.get("data-group-id") is not None:
            self.groups.append(str(values["data-group-id"]))
        if tag == "tr" and values.get("data-member-path") is not None:
            try:
                statements = json.loads(values.get("data-statements", ""))
                arcs = json.loads(values.get("data-arcs", ""))
            except json.JSONDecodeError as exc:
                _fail(f"HTML member region attributes are not JSON: {exc}")
            self.members.append(
                {
                    "group_id": values.get("data-group-id"),
                    "path": values.get("data-member-path"),
                    "source_sha256": values.get("data-source-sha256"),
                    "unreached_statements": statements,
                    "unreached_arcs": arcs,
                }
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre" and self.in_record:
            self.in_record = False

    def handle_data(self, data: str) -> None:
        if self.in_record:
            self.record_chunks.append(data)


def validate_html(path: Path, record: Mapping[str, Any]) -> None:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        _fail(f"cannot read derived HTML: {exc}")
    parser = _EvidenceHTMLParser()
    parser.feed(source)
    if not parser.record_chunks:
        _fail("derived HTML does not expose the canonical record")
    try:
        embedded = json.loads("".join(parser.record_chunks))
    except json.JSONDecodeError as exc:
        _fail(f"derived HTML canonical record is invalid JSON: {exc}")
    if embedded != record:
        _fail("derived HTML canonical record does not match record.json")
    expected_groups = [group["id"] for group in record["groups"]]
    if parser.groups != expected_groups:
        _fail("derived HTML group projection is incomplete or reordered")
    expected_members = []
    for group in record["groups"]:
        for member in group["members"]:
            expected_members.append(
                {
                    "group_id": group["id"],
                    "path": member["path"],
                    "source_sha256": member["source_sha256"],
                    "unreached_statements": member["unreached_statements"],
                    "unreached_arcs": member["unreached_arcs"],
                }
            )
    if parser.members != expected_members:
        _fail("derived HTML member projection is incomplete or changed")


class _DashboardHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.record_sha256: str | None = None
        self.data_chunks: list[str] = []
        self.in_data = False
        self.group_ids: list[str] = []
        self.coverage_class_ids: list[str] = []
        self.command_paths: list[str] = []
        self.toc_targets: list[str] = []
        self.element_ids: set[str] = set()
        self.initial_group_id: str | None = None
        self.initial_coverage_class_id: str | None = None
        self.initial_command_path: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        element_id = values.get("id")
        if element_id:
            self.element_ids.add(element_id)
        if tag == "meta" and values.get("name") == "record-sha256":
            self.record_sha256 = values.get("content")
        if tag == "script" and values.get("id") == "dashboard-data":
            self.in_data = True
        if tag == "button" and "group-row" in (values.get("class") or "").split():
            group_id = values.get("data-group-id")
            if group_id is not None:
                self.group_ids.append(group_id)
        if tag == "button" and "coverage-class-row" in (values.get("class") or "").split():
            coverage_class_id = values.get("data-coverage-class-id")
            if coverage_class_id is not None:
                self.coverage_class_ids.append(coverage_class_id)
        command_path = values.get("data-command-path")
        if tag == "li" and command_path is not None:
            self.command_paths.append(command_path)
        toc_target = values.get("data-dashboard-toc")
        if toc_target is not None:
            self.toc_targets.append(toc_target)
        if element_id == "group-detail":
            self.initial_group_id = values.get("data-initial-group-id")
        if element_id == "coverage-detail":
            self.initial_coverage_class_id = values.get("data-initial-coverage-class-id")
        if element_id == "command-detail":
            self.initial_command_path = values.get("data-initial-command-path")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.in_data:
            self.in_data = False

    def handle_data(self, data: str) -> None:
        if self.in_data:
            self.data_chunks.append(data)


def validate_dashboard_html(
    path: Path,
    record: Mapping[str, Any],
    sealed: Mapping[str, Any],
    authority: Mapping[str, Any] | None = None,
) -> None:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        _fail(f"cannot read derived dashboard HTML: {exc}")
    parser = _DashboardHTMLParser()
    parser.feed(source)
    if parser.record_sha256 != record["integrity"]["record_sha256"]:
        _fail("dashboard record digest metadata does not match record.json")
    if not parser.data_chunks:
        _fail("dashboard does not expose its canonical data projection")
    try:
        projection = json.loads("".join(parser.data_chunks))
    except json.JSONDecodeError as exc:
        _fail(f"dashboard data projection is invalid JSON: {exc}")
    expected_projection = _dashboard_projection(record, sealed, authority)
    if projection != expected_projection:
        _fail("dashboard data projection does not match the record")
    expected_groups = [group["id"] for group in record["groups"]]
    expected_coverage_classes = [
        coverage_class["id"]
        for coverage_class in expected_projection["coverage_overview"]["classes"]
    ]
    if parser.group_ids != expected_groups:
        _fail("dashboard group chart is incomplete or reordered")
    if parser.coverage_class_ids != expected_coverage_classes:
        _fail("dashboard coverage map is incomplete or reordered")
    expected_toc_targets = [
        "#command-explorer-heading",
        "#coverage-map-heading",
        "#source-capability-heading",
    ]
    if parser.toc_targets != expected_toc_targets:
        _fail("dashboard table of contents is incomplete or reordered")
    if parser.command_paths != _dashboard_command_tree_paths(
        expected_projection["command_tree"]
    ):
        _fail("dashboard command tree is incomplete or reordered")
    if parser.initial_group_id != expected_groups[0]:
        _fail("dashboard initial group selection is not canonical")
    if parser.initial_coverage_class_id != expected_coverage_classes[0]:
        _fail("dashboard initial coverage selection is not canonical")
    if parser.initial_command_path != expected_projection["command_tree"]["command"]:
        _fail("dashboard initial command selection is not canonical")
    required_ids = {
        "capability-dashboard",
        "dashboard-toc",
        "command-explorer-heading",
        "coverage-map",
        "coverage-map-heading",
        "coverage-detail",
        "command-explorer",
        "command-selection",
        "command-detail",
        "source-capability-heading",
        "group-chart",
        "disposition-chart",
        "source-status-chart",
        "group-detail",
    }
    if not required_ids <= parser.element_ids:
        _fail("dashboard is missing a required visual projection")


def _build_context(
    repo_root: Path,
    *,
    source_reader: FrozenGitSource | None = None,
) -> dict[str, Any]:
    sealed = load_sealed_report(repo_root, source_reader=source_reader)
    source_analysis_path = repo_root / SOURCE_ANALYSIS_REL
    artifact = load_canonical_json(source_analysis_path)
    source_analysis_sha256 = validate_source_analysis(
        artifact,
        repo_root,
        sealed["source_paths"],
        source_reader=sealed["source_reader"],
    )
    authority = load_m007_08_authority(repo_root)
    grouping_path = repo_root / GROUPING_REL
    grouping = load_canonical_json(grouping_path)
    candidates = _derive_candidates(sealed, artifact)
    validate_grouping(
        grouping,
        repo_root=repo_root,
        source_paths=sealed["source_paths"],
        candidate_paths=set(candidates),
        authority=authority,
    )
    grouping_sha256 = sha256_file(grouping_path)
    return {
        "sealed": sealed,
        "artifact": artifact,
        "authority": authority,
        "grouping": grouping,
        "source_analysis_sha256": source_analysis_sha256,
        "grouping_sha256": grouping_sha256,
    }


def build_evidence(repo_root: Path = ROOT) -> dict[str, Any]:
    context = _build_context(repo_root)
    record = assemble_record(
        sealed=context["sealed"],
        artifact=context["artifact"],
        grouping=context["grouping"],
        source_analysis_sha256=context["source_analysis_sha256"],
        grouping_sha256=context["grouping_sha256"],
    )
    expected = _copy(record)
    validate_record(record, expected=expected)
    record_path = repo_root / RECORD_REL
    report_path = repo_root / PASS_REPORT_REL
    residuals_path = repo_root / RESIDUALS_REL
    rollup_path = repo_root / ROLLUP_REL
    html_path = repo_root / HTML_REL
    dashboard_path = repo_root / DASHBOARD_REL
    write_canonical(record_path, record)
    pass_report = _make_pass_report(record, context["sealed"])
    residuals = _make_residuals(record)
    write_canonical(report_path, pass_report)
    write_canonical(residuals_path, residuals)
    rollup_path.parent.mkdir(parents=True, exist_ok=True)
    rollup_path.write_text(render_rollup(record, context["sealed"]), encoding="utf-8")
    html_path.write_text(render_html(record), encoding="utf-8")
    dashboard_path.write_text(
        render_dashboard_html(record, context["sealed"], context["authority"]),
        encoding="utf-8",
    )
    validate_html(html_path, record)
    return {
        "record": record,
        "report": pass_report,
        "residuals": residuals,
        "record_path": record_path,
        "report_path": report_path,
        "html_path": html_path,
        "dashboard_path": dashboard_path,
    }


def validate_evidence(repo_root: Path = ROOT) -> dict[str, Any]:
    context = _build_context(repo_root)
    expected = assemble_record(
        sealed=context["sealed"],
        artifact=context["artifact"],
        grouping=context["grouping"],
        source_analysis_sha256=context["source_analysis_sha256"],
        grouping_sha256=context["grouping_sha256"],
    )
    record_path = repo_root / RECORD_REL
    record = load_canonical_json(record_path)
    validate_record(record, expected=expected)
    if record != expected:
        _fail("committed record differs from the deterministic derivation")
    report = load_canonical_json(repo_root / PASS_REPORT_REL)
    if report != _make_pass_report(record, context["sealed"]):
        _fail("pass report differs from the deterministic derivation")
    residuals = load_canonical_json(repo_root / RESIDUALS_REL)
    if residuals != _make_residuals(record):
        _fail("residual rollup differs from the deterministic derivation")
    rollup_path = repo_root / ROLLUP_REL
    if not rollup_path.is_file() or rollup_path.read_text(encoding="utf-8") != render_rollup(record, context["sealed"]):
        _fail("rollup differs from the deterministic derivation")
    validate_html(repo_root / HTML_REL, record)
    return {
        "result": "pass",
        "record_sha256": record["integrity"]["record_sha256"],
        "candidate_member_count": len(record["residuals"]["candidate_member_paths"]),
        "group_count": len(record["groups"]),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate M007-09 capability disposition evidence")
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture-source-analysis")
    capture.add_argument("--output", type=Path, default=None)
    sub.add_parser("build")
    sub.add_parser("validate")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "capture-source-analysis":
            artifact = capture_source_analysis(ROOT, args.output)
            path = args.output or (ROOT / SOURCE_ANALYSIS_REL)
            print(json.dumps({"result": "pass", "path": str(path), "sha256": sha256_file(path)}, sort_keys=True))
        elif args.command == "build":
            result = build_evidence(ROOT)
            print(json.dumps({"result": "pass", "record_sha256": result["record"]["integrity"]["record_sha256"]}, sort_keys=True))
        else:
            print(json.dumps(validate_evidence(ROOT), sort_keys=True))
        return 0
    except CapabilityDispositionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
