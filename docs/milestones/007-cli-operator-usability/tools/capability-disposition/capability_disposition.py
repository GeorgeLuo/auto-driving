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
import sys
import sysconfig
import unicodedata
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

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

SOURCE_ROOTS = ["autonomy", "implementations", "cli/automa_cli"]
JOURNEY_PREFIX = "m007/journey/"
SOURCE_ANALYSIS_SCHEMA = "m007_capability_source_analysis_v1"
GROUPING_SCHEMA = "m007_capability_grouping_v1"
RECORD_SCHEMA = "m007_capability_disposition_v1"
REPORT_SCHEMA = "m007_capability_disposition_report_v1"
RESIDUALS_SCHEMA = "m007_capability_disposition_residuals_v1"

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


def _source_files_from_report(report: Mapping[str, Any], repo_root: Path) -> dict[str, str]:
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
    for row in relevant["files"]:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            _fail("journey report relevant file row is malformed")
        path = _normalized_path(row.get("path"), "journey report source path")
        digest = row.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            _fail(f"journey report source hash is malformed for {path}")
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

    coveragerc = repo_root / ".coveragerc"
    if not coveragerc.is_file() or sha256_file(coveragerc) != FROZEN_COVERAGE_CONFIG_SHA256:
        _fail(".coveragerc does not match the sealed M007-07 configuration")
    for path, digest in paths.items():
        file_path = repo_root / path
        if not file_path.is_file() or sha256_file(file_path) != digest:
            _fail(f"sealed source file changed or is missing: {path}")
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


def load_sealed_report(repo_root: Path = ROOT) -> dict[str, Any]:
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
    source_paths = _source_files_from_report(report, repo_root)
    admitted, files = _validate_report_contexts(report, source_paths)
    return {
        "report": report,
        "report_path": path,
        "source_paths": source_paths,
        "admitted_contexts": admitted,
        "files": files,
    }


def _source_analysis_inputs() -> dict[str, Any]:
    return {
        "source_identity": _copy(FROZEN_SOURCE_IDENTITY),
        "coverage_analysis": _copy(FROZEN_COVERAGE_ANALYSIS),
        "source_analysis_runtime": _copy(FROZEN_RUNTIME),
    }


def _possible_regions(path: str, repo_root: Path) -> tuple[list[int], list[list[int]]]:
    if _is_init(path):
        return [], []
    source = (repo_root / path).read_text(encoding="utf-8")
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
        statements, arcs = _possible_regions(path, repo_root)
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
        expected_statements, expected_arcs = _possible_regions(path, repo_root)
        if row["possible_statements"] != expected_statements:
            _fail(f"source-analysis statement projection changed for {path}")
        if row["possible_arcs"] != expected_arcs:
            _fail(f"source-analysis arc projection changed for {path}")
    if paths != sorted(source_paths) or paths != sorted(set(paths)):
        _fail("source-analysis path set is incomplete, duplicated, or unsorted")
    return sha256_bytes(canonical_file_bytes(dict(artifact)))


def load_m007_08_authority(repo_root: Path = ROOT) -> dict[str, Any]:
    documents: dict[str, Any] = {}
    for entry in FROZEN_M007_08_MANIFEST:
        path = repo_root / entry["path"]
        actual = sha256_file(path) if path.is_file() else None
        if actual != entry["sha256"]:
            _fail(f"frozen M007-08 input changed or is missing: {entry['path']}")
        document = _load_json(path)
        if not isinstance(document, dict) or document.get("schema") != entry["schema"]:
            _fail(f"frozen M007-08 input schema changed: {entry['id']}")
        documents[entry["id"]] = document
    registry = documents["sequence_registry"]
    if registry.get("catalog_digest") != "9cf4c8bf139183d10ea51c5b576eb47cef1919a161570d704893b3f7372a0e40":
        _fail("frozen sequence registry catalog digest changed")
    sequence_ids = {row.get("id") for row in registry.get("sequences", []) if isinstance(row, dict)}
    owners: set[str] = set()
    for row in registry.get("sequences", []):
        if isinstance(row, dict):
            for field in ("owner", "ledger_owner"):
                if isinstance(row.get(field), str) and row[field]:
                    owners.add(row[field])
    audit_report = documents["audit_report"]
    for key in ("owner", "ledger_owner"):
        value = audit_report.get(key)
        if isinstance(value, str) and value:
            owners.add(value)
    return {
        "documents": documents,
        "entries": _copy(FROZEN_M007_08_MANIFEST),
        "paths": {entry["path"] for entry in FROZEN_M007_08_MANIFEST},
        "digests": {entry["path"]: entry["sha256"] for entry in FROZEN_M007_08_MANIFEST},
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
        if not (repo_root / path).exists():
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
    r"\b(?:coverage|covered|unexecuted|un reached|unreached|untested|"
    r"not covered|not reached|never executed|line count|branch count|"
    r"statement count|arc count)\b"
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
        if path not in authority["paths"] or authority["digests"].get(path) != reference.get("artifact_sha256"):
            _fail(f"{where}.reference M007-08 artifact is not frozen")
        if reference.get("value") not in authority["owners"]:
            _fail(f"{where}.reference owner value is not frozen")


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


def _build_context(repo_root: Path) -> dict[str, Any]:
    sealed = load_sealed_report(repo_root)
    source_analysis_path = repo_root / SOURCE_ANALYSIS_REL
    artifact = load_canonical_json(source_analysis_path)
    source_analysis_sha256 = validate_source_analysis(
        artifact, repo_root, sealed["source_paths"]
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
    write_canonical(record_path, record)
    pass_report = _make_pass_report(record, context["sealed"])
    residuals = _make_residuals(record)
    write_canonical(report_path, pass_report)
    write_canonical(residuals_path, residuals)
    rollup_path.parent.mkdir(parents=True, exist_ok=True)
    rollup_path.write_text(render_rollup(record, context["sealed"]), encoding="utf-8")
    html_path.write_text(render_html(record), encoding="utf-8")
    validate_html(html_path, record)
    return {
        "record": record,
        "report": pass_report,
        "residuals": residuals,
        "record_path": record_path,
        "report_path": report_path,
        "html_path": html_path,
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
