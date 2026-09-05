"""Small, observations-only verification factors for QCA.

The factor layer is deliberately separate from :mod:`qca.analyzer`.  A tree
scan can say where tests and lifecycle-shaped code exist, but it cannot say
that a test is effective or that a lifecycle is symmetric.  Runtime claims
are therefore accepted only through an explicit, caller-supplied evidence
record.

The runtime evidence record uses ``qca/verification/v1``::

    {
      "schema": "qca/verification/v1",
      "base_sha": "<base revision>",
      "head_sha": "<head revision>",
      "provenance": {"runner": "..."},
      "factors": {
        "test_effectiveness": {"status": "not_measured", "reason": "..."},
        "end_to_end": {
          "status": "passed",
          "commands": ["python -m ..."],
          "results": [{"returncode": 0, "stdout": "..."}],
          "expected": {"phase": "completed"},
          "actual": {"phase": "completed"}
        },
        "ui_behavior": {"status": "not_measured", "reason": "..."},
        "lifecycle": {"status": "not_measured", "reason": "..."}
      }
    }

All four factor names are recognized.  Omitted factor records are treated as
``not_measured`` by :func:`attach_verification`; a ``passed`` or ``failed``
record must contain non-empty command/result material or a non-empty
expected/actual pair.  A lone ``{"passed": true}`` is not evidence.  The
provenance object is copied exactly as supplied by the caller.  This module
does not execute commands, inspect Git, or attest that provenance is genuine.

The static scan has similarly narrow semantics:

* test assertions are counted only in test-like files or test-named callables;
* literal, same-operand, string-expected, and formatted-literal assertions are
  reported as *candidates*, not bad tests;
* private production imports and helper calls from tests are candidates, while
  same-module test helpers and numeric expected values are not;
* lifecycle metrics count recognized definitions and calls, with a bounded
  list of sites; and
* no static metric proves lifecycle start/stop/reset/cleanup symmetry.
"""

from __future__ import annotations

import ast
import copy
import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any


VERIFICATION_SCHEMA = "qca/verification/v1"
VERIFICATION_FACTORS = (
    "test_effectiveness",
    "end_to_end",
    "ui_behavior",
    "lifecycle",
)
STATIC_FACTORS = ("test_effectiveness", "lifecycle")
DYNAMIC_FACTORS = ("end_to_end", "ui_behavior")

# Site lists are evidence for inspection, not a complete program inventory.
# Counts remain exact (for successfully parsed Python inputs) while the lists
# stay bounded so a large repository cannot produce an unwieldy report.
MAX_CANDIDATE_SITES = 64
MAX_LIFECYCLE_SITES = 128

_PYTHON_SUFFIXES = {".py", ".pyi"}
_TEST_FILE_RE = re.compile(r"(?:^|/)(?:tests?|spec)(?:/|$)|(?:^|/)(?:test_[^/]+|[^/]+_test)\.py$")
_LIFECYCLE_ALIASES: dict[str, set[str]] = {
    "start": {"start", "begin", "launch"},
    "stop": {"stop", "shutdown", "halt", "terminate", "cancel"},
    "reset": {"reset", "restart", "clear"},
    "cleanup": {"cleanup", "clean_up", "teardown", "close", "dispose", "release"},
}
_LIFECYCLE_BY_NAME = {
    alias: operation for operation, aliases in _LIFECYCLE_ALIASES.items() for alias in aliases
}
_LIFECYCLE_SUFFIXES = ("_locked", "_action", "_now", "_async")
_ASSERT_EQUAL_METHODS = {
    "assertEqual",
    "assertAlmostEqual",
    "assertSequenceEqual",
    "assertListEqual",
    "assertTupleEqual",
    "assertSetEqual",
    "assertDictEqual",
    "assertCountEqual",
    "assertIs",
}
_ASSERT_LITERAL_METHODS = {
    "assertTrue",
    "assertFalse",
    "assertIsNone",
    "assertIsNotNone",
}
_STRING_EXPECTED_METHODS = {
    "assertEqual",
    "assertNotEqual",
    "assertMultiLineEqual",
    "assertIn",
    "assertNotIn",
    "assertRegex",
    "assertNotRegex",
}
_FORMATTED_LITERAL_RE = re.compile(r"[|<>\n`]|\d+(?:\.\d+)?%")
_FORMATTED_LITERAL_MIN_LENGTH = 40


def analyze_verification(sources: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Return conservative static verification factors for source contents.

    ``sources`` maps repository-relative paths to text.  Python files are
    parsed with :mod:`ast`; unsupported files are ignored by the test scan but
    are still counted as source inputs.  The return value intentionally has no
    score or overall verdict.
    """

    _validate_sources(sources)
    ordered_sources = sorted(sources.items(), key=lambda item: item[0].replace("\\", "/"))
    parsed: list[tuple[str, str, ast.AST]] = []
    parse_errors: list[dict[str, Any]] = []
    python_file_count = 0
    for path, text in ordered_sources:
        if not _is_python_path(path):
            continue
        python_file_count += 1
        try:
            tree = ast.parse(text, filename=path, type_comments=True)
        except SyntaxError as exc:
            parse_errors.append(
                {
                    "path": path,
                    "line": int(exc.lineno or 0),
                    "column": int(exc.offset or 0),
                    "message": str(exc.msg or "syntax error"),
                }
            )
            continue
        parsed.append((path, text, tree))

    test_factor = _test_effectiveness_factor(
        ordered_sources,
        parsed,
        parse_errors,
        python_file_count,
    )
    lifecycle_factor = _lifecycle_factor(ordered_sources, parsed, parse_errors)
    return {
        "test_effectiveness": test_factor,
        "end_to_end": _not_measured_factor(
            "No subprocess or integration-run evidence was supplied; static source inspection cannot establish end-to-end behavior."
        ),
        "ui_behavior": _not_measured_factor(
            "No actual browser interaction evidence was supplied; loopback/API traces alone do not establish UI behavior."
        ),
        "lifecycle": lifecycle_factor,
    }


def attach_verification(
    factors: Mapping[str, Mapping[str, Any]],
    evidence: dict[str, Any],
    base_sha: str,
    head_sha: str,
) -> dict[str, dict[str, Any]]:
    """Attach validated caller-supplied evidence without rewriting static data.

    ``base_sha`` and ``head_sha`` are the revisions selected by the caller.
    They are required to match the evidence record when the record repeats
    them.  Runtime ``passed`` records promote only the dynamic factors to
    ``verified``; ``failed`` promotes them to ``failed``.  Static factor
    statuses remain the result of :func:`analyze_verification`.  Every attached
    factor receives a ``verification`` object containing the original record,
    revisions, schema, and caller-supplied provenance.

    The function is intentionally non-executing: it does not authenticate
    commands, inspect their output, or claim that a provenance object is
    genuine.  Invalid or empty evidence raises :class:`ValueError` rather than
    silently turning a boolean into a behavior claim.
    """

    _validate_revision(base_sha, "base_sha")
    _validate_revision(head_sha, "head_sha")
    if not isinstance(factors, Mapping):
        raise TypeError("factors must be a mapping of factor names to payloads")
    if not isinstance(evidence, Mapping):
        raise TypeError("evidence must be a mapping")

    schema = evidence.get("schema")
    if schema != VERIFICATION_SCHEMA:
        raise ValueError(f"evidence schema must be {VERIFICATION_SCHEMA}")
    evidence_base = evidence.get("base_sha")
    evidence_head = evidence.get("head_sha")
    _validate_revision(evidence_base, "evidence.base_sha")
    _validate_revision(evidence_head, "evidence.head_sha")
    if evidence_base != base_sha or evidence_head != head_sha:
        raise ValueError("evidence base_sha/head_sha do not match attach_verification revisions")

    records = _extract_factor_records(evidence)
    normalized_records: dict[str, dict[str, Any]] = {}
    for factor_name in VERIFICATION_FACTORS:
        raw_record = records.get(factor_name)
        if raw_record is None:
            raw_record = {
                "status": "not_measured",
                "reason": "No record was supplied for this factor.",
            }
        normalized_records[factor_name] = _validate_factor_record(
            factor_name,
            raw_record,
            base_sha=base_sha,
            head_sha=head_sha,
        )

    # deepcopy keeps the caller's report/evidence independent from subsequent
    # mutation and preserves any analyzer-owned base_metrics/delta fields.
    result: dict[str, dict[str, Any]] = copy.deepcopy(dict(factors))
    provenance = copy.deepcopy(evidence.get("provenance"))
    for factor_name, record in normalized_records.items():
        if factor_name not in result:
            # A partial static report is still attachable, but do not invent a
            # measured static result.  This makes the helper useful to callers
            # that select only a subset while keeping status conservative.
            result[factor_name] = _not_measured_factor(
                "No static factor payload was supplied; only attached evidence is available."
            )
        payload = result[factor_name]
        if not isinstance(payload, dict):
            raise ValueError(f"factor payload for {factor_name!r} must be a mapping")

        attached = {
            "schema": VERIFICATION_SCHEMA,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "status": record["status"],
            "record": copy.deepcopy(record),
            "provenance": provenance,
            # This is a provenance boundary, not an execution attestation.
            "claim_source": "caller_supplied",
        }
        payload["verification"] = attached

        if factor_name in DYNAMIC_FACTORS:
            if record["status"] == "passed":
                payload["status"] = "verified"
            elif record["status"] == "failed":
                payload["status"] = "failed"
            else:
                payload["status"] = "not_measured"

            evidence_findings = record.get("findings")
            if isinstance(evidence_findings, list) and evidence_findings:
                existing = payload.setdefault("findings", [])
                if not isinstance(existing, list):
                    raise ValueError(f"factor findings for {factor_name!r} must be a list")
                existing.extend(copy.deepcopy(evidence_findings))

    return result


def _validate_sources(sources: Mapping[str, str]) -> None:
    if not isinstance(sources, Mapping):
        raise TypeError("sources must be a mapping of paths to source text")
    for path, text in sources.items():
        if not isinstance(path, str) or not path.strip():
            raise TypeError("source paths must be non-empty strings")
        if not isinstance(text, str):
            raise TypeError(f"source text for {path!r} must be a string")


def _validate_revision(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or any(char.isspace() for char in value):
        raise ValueError(f"{label} must be a non-empty revision/ref string without whitespace")


def _is_python_path(path: str) -> bool:
    return PurePosixPath(path.replace("\\", "/")).suffix.lower() in _PYTHON_SUFFIXES


def _is_test_source(path: str) -> bool:
    normalized = path.replace("\\", "/").strip("/")
    return bool(_TEST_FILE_RE.search(normalized))


def _test_effectiveness_factor(
    ordered_sources: list[tuple[str, str]],
    parsed: list[tuple[str, str, ast.AST]],
    parse_errors: list[dict[str, Any]],
    python_file_count: int,
) -> dict[str, Any]:
    test_paths = {path for path, _ in ordered_sources if _is_test_source(path)}
    candidate_sites: list[dict[str, Any]] = []
    assertion_count = 0
    literal_count = 0
    tautological_count = 0
    string_expected_count = 0
    formatted_literal_count = 0
    private_import_count = 0
    private_helper_call_count = 0
    private_import_files: set[str] = set()
    test_case_count = 0
    test_parse_error_count = sum(1 for error in parse_errors if error["path"] in test_paths)

    for path, text, tree in parsed:
        module_is_test = path in test_paths
        parents = _parent_map(tree)
        local_private = _private_names_defined(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                test_case_count += 1
            if module_is_test and isinstance(node, (ast.Import, ast.ImportFrom)):
                imported = _private_imported_names(node)
                if imported:
                    private_import_count += len(imported)
                    private_import_files.add(path)
                    _append_test_candidate(
                        candidate_sites,
                        kind="private_import",
                        path=path,
                        node=node,
                        text=text,
                        reason=(
                            "Private production import in a test; inspect whether the test can use a public entrypoint."
                        ),
                    )
                continue
            if not isinstance(node, (ast.Assert, ast.Call)):
                continue
            if not module_is_test and not _inside_test_callable(node, parents):
                continue
            if isinstance(node, ast.Call) and _is_private_helper_call(node, local_private):
                private_helper_call_count += 1
                _append_test_candidate(
                    candidate_sites,
                    kind="private_helper_call",
                    path=path,
                    node=node,
                    text=text,
                    reason=(
                        "Private production helper called from a test; inspect whether a public entrypoint covers the behavior."
                    ),
                )
            candidate_kind = _assertion_candidate_kind(node)
            if candidate_kind is None:
                candidate_kind = _string_expected_kind(node)
            if isinstance(node, ast.Assert):
                assertion_count += 1
            elif _is_assertion_call(node):
                assertion_count += 1
            if candidate_kind is None:
                continue
            if candidate_kind == "literal_assertion":
                literal_count += 1
            elif candidate_kind == "tautological_assertion":
                tautological_count += 1
            elif candidate_kind == "formatted_literal_assertion":
                formatted_literal_count += 1
                string_expected_count += 1
            else:
                string_expected_count += 1
            _append_test_candidate(
                candidate_sites,
                kind=candidate_kind,
                path=path,
                node=node,
                text=text,
                reason=(
                    "Static candidate only; inspect assertion intent and its input variation before drawing a conclusion."
                ),
            )

    candidate_count = literal_count + tautological_count + string_expected_count
    limitations = [
        "Literal, same-operand, string-expected, and formatted-literal assertions are candidates for review, not a judgment that a test is ineffective.",
        "Private production imports and helper calls are candidates; same-module test helpers and numeric expected values are omitted.",
        "Static inspection does not infer input variation, mocks, explicit state setup, or the behavior under test.",
    ]
    if test_parse_error_count:
        limitations.append(
            f"{test_parse_error_count} test-like Python file(s) could not be parsed and were excluded from assertion counts."
        )
    if candidate_count > MAX_CANDIDATE_SITES:
        limitations.append(
            f"Candidate site details are limited to the first {MAX_CANDIDATE_SITES}; counts remain parser-derived."
        )
    metrics = {
        "source_file_count": len(ordered_sources),
        "python_file_count": python_file_count,
        "test_file_count": len(test_paths),
        "test_case_count": test_case_count,
        "assertion_count": assertion_count,
        "literal_assertion_candidates": literal_count,
        "tautological_assertion_candidates": tautological_count,
        "string_expected_assertion_count": string_expected_count,
        "formatted_literal_assertion_count": formatted_literal_count,
        "private_import_count": private_import_count,
        "private_import_test_file_count": len(private_import_files),
        "private_helper_call_count": private_helper_call_count,
        "candidate_assertion_count": candidate_count,
        "candidate_site_count": len(candidate_sites),
        "parse_error_count": test_parse_error_count,
    }
    return {
        "status": "measured",
        "metrics": metrics,
        "findings": candidate_sites,
        "details": {
            "candidate_site_limit": MAX_CANDIDATE_SITES,
            "candidate_sites_are_complete": True,
        },
        "limitations": limitations,
    }


def _lifecycle_factor(
    ordered_sources: list[tuple[str, str]],
    parsed: list[tuple[str, str, ast.AST]],
    parse_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    by_kind = {
        operation: {"definitions": 0, "calls": 0, "sites": 0}
        for operation in _LIFECYCLE_ALIASES
    }
    sites: list[dict[str, Any]] = []
    for path, text, tree in parsed:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                operation = _lifecycle_operation(node.name)
                if operation is not None:
                    by_kind[operation]["definitions"] += 1
                    _append_lifecycle_site(
                        sites,
                        path,
                        node,
                        operation,
                        "definition",
                        text,
                    )
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                operation = _lifecycle_operation(name or "")
                if operation is not None:
                    by_kind[operation]["calls"] += 1
                    _append_lifecycle_site(
                        sites,
                        path,
                        node,
                        operation,
                        "call",
                        text,
                    )

    for values in by_kind.values():
        values["sites"] = values["definitions"] + values["calls"]
    total_sites = sum(values["sites"] for values in by_kind.values())
    limitations = [
        "Recognized names and call sites are static indicators only; they do not prove start/stop/reset/cleanup effects or symmetry.",
        "No runtime lifecycle sequence, resource ownership, or teardown outcome was measured.",
    ]
    if len(sites) < total_sites:
        limitations.append(
            f"Lifecycle site details are limited to the first {MAX_LIFECYCLE_SITES}; counts remain parser-derived."
        )
    if parse_errors:
        limitations.append(
            f"{len(parse_errors)} Python file(s) could not be parsed and were excluded from lifecycle AST counts."
        )
    metrics = {
        "recognized_site_count": total_sites,
        "effect_site_count": total_sites,
        "definitions_count": sum(values["definitions"] for values in by_kind.values()),
        "calls_count": sum(values["calls"] for values in by_kind.values()),
        "start_definition_count": by_kind["start"]["definitions"],
        "start_call_count": by_kind["start"]["calls"],
        "start_site_count": by_kind["start"]["sites"],
        "stop_definition_count": by_kind["stop"]["definitions"],
        "stop_call_count": by_kind["stop"]["calls"],
        "stop_site_count": by_kind["stop"]["sites"],
        "reset_definition_count": by_kind["reset"]["definitions"],
        "reset_call_count": by_kind["reset"]["calls"],
        "reset_site_count": by_kind["reset"]["sites"],
        "cleanup_definition_count": by_kind["cleanup"]["definitions"],
        "cleanup_call_count": by_kind["cleanup"]["calls"],
        "cleanup_site_count": by_kind["cleanup"]["sites"],
        "site_count": len(sites),
        "parse_error_count": len(parse_errors),
    }
    return {
        "status": "measured",
        "metrics": metrics,
        "findings": sites,
        "details": {
            "by_kind": by_kind,
            "site_limit": MAX_LIFECYCLE_SITES,
            "sites_are_complete": True,
        },
        "limitations": limitations,
    }


def _not_measured_factor(reason: str) -> dict[str, Any]:
    return {
        "status": "not_measured",
        "metrics": {},
        "findings": [],
        "limitations": [reason],
    }


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _inside_test_callable(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> bool:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)) and current.name.startswith("test_"):
            return True
        current = parents.get(current)
    return False


def _is_assertion_call(node: ast.Call) -> bool:
    name = _call_name(node.func)
    return bool(name and (name.startswith("assert") or name in {"fail", "failUnless", "failIf"}))


def _append_test_candidate(
    sites: list[dict[str, Any]],
    *,
    kind: str,
    path: str,
    node: ast.AST,
    text: str,
    reason: str,
) -> None:
    sites.append(
        {
            "kind": kind,
            "path": path,
            "line": int(getattr(node, "lineno", 0) or 0),
            "column": int((getattr(node, "col_offset", 0) or 0) + 1),
            "expression": _source_expression(text, node),
            "reason": reason,
            "message": reason,
        }
    )


def _is_private_identifier(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def _private_names_defined(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if _is_private_identifier(node.name):
                names.add(node.name)
    return names


def _is_test_module_name(module: str | None) -> bool:
    if not module:
        return False
    return any(
        part in {"tests", "test", "testing"} or part.startswith("test_")
        for part in module.split(".")
    )


def _private_imported_names(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name.split(".")[-1] for alias in node.names if _is_private_identifier(alias.name.split(".")[-1]))
    if node.module == "__future__" or (node.level or 0) > 0 or _is_test_module_name(node.module):
        return ()
    return tuple(alias.name for alias in node.names if _is_private_identifier(alias.name))


def _is_private_helper_call(node: ast.Call, local_private: set[str]) -> bool:
    name = _call_name(node.func)
    return bool(name and _is_private_identifier(name) and name not in local_private)


def _constant_text(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, (str, bytes)):
        return None
    if isinstance(node.value, bytes):
        return node.value.decode("utf-8", "replace")
    return node.value


def _is_formatted_literal(text: str) -> bool:
    return len(text) >= _FORMATTED_LITERAL_MIN_LENGTH or bool(_FORMATTED_LITERAL_RE.search(text))


def _string_constants_in_assertion(node: ast.AST) -> list[str]:
    values: list[str] = []
    if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare):
        for part in (node.test.left, *node.test.comparators):
            text = _constant_text(part)
            if text is not None:
                values.append(text)
        return values
    if not isinstance(node, ast.Call):
        return values
    name = _call_name(node.func)
    if name not in _STRING_EXPECTED_METHODS:
        return values
    for arg in node.args[:2]:
        text = _constant_text(arg)
        if text is not None:
            values.append(text)
    return values


def _string_expected_kind(node: ast.AST) -> str | None:
    constants = _string_constants_in_assertion(node)
    if not constants:
        return None
    expected = max(constants, key=len)
    if _is_formatted_literal(expected):
        return "formatted_literal_assertion"
    return "string_expected_assertion"


def _assertion_candidate_kind(node: ast.AST) -> str | None:
    if isinstance(node, ast.Assert):
        if _is_literal_expression(node.test):
            return "literal_assertion"
        if _is_same_operand_compare(node.test):
            return "tautological_assertion"
        return None
    if not isinstance(node, ast.Call):
        return None
    name = _call_name(node.func)
    if name is None:
        return None
    if name in _ASSERT_EQUAL_METHODS and len(node.args) >= 2:
        if _ast_equal(node.args[0], node.args[1]):
            return "tautological_assertion"
        return None
    if name in _ASSERT_LITERAL_METHODS and node.args and _is_literal_expression(node.args[0]):
        return "literal_assertion"
    return None


def _is_literal_expression(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    # ``assert not True`` and simple literal boolean expressions are still
    # static candidates, while arbitrary calls/attributes are not.
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.UAdd, ast.USub)):
        return isinstance(node.operand, ast.Constant)
    return False


def _is_same_operand_compare(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and len(node.comparators) == 1
        and isinstance(node.ops[0], (ast.Eq, ast.Is))
        and _ast_equal(node.left, node.comparators[0])
    )


def _ast_equal(left: ast.AST, right: ast.AST) -> bool:
    return ast.dump(left, annotate_fields=True, include_attributes=False) == ast.dump(
        right,
        annotate_fields=True,
        include_attributes=False,
    )


def _source_expression(text: str, node: ast.AST) -> str:
    expression = ast.get_source_segment(text, node)
    if expression:
        return " ".join(expression.strip().split())
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return type(node).__name__


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _lifecycle_operation(name: str) -> str | None:
    normalized = name.strip().lower().lstrip("_")
    for suffix in _LIFECYCLE_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return _LIFECYCLE_BY_NAME.get(normalized)


def _append_lifecycle_site(
    sites: list[dict[str, Any]],
    path: str,
    node: ast.AST,
    operation: str,
    site_type: str,
    text: str,
) -> None:
    if len(sites) >= MAX_LIFECYCLE_SITES:
        return
    message = (
        "Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately."
    )
    sites.append(
        {
            "kind": "lifecycle_effect_site",
            "operation": operation,
            "site_type": site_type,
            "path": path,
            "line": int(getattr(node, "lineno", 0) or 0),
            "column": int((getattr(node, "col_offset", 0) or 0) + 1),
            "name": _source_expression(text, node),
            "message": message,
        }
    )


def _extract_factor_records(evidence: Mapping[str, Any]) -> dict[str, Any]:
    nested = evidence.get("factors")
    if nested is None:
        raise ValueError("evidence must contain one nested factors mapping")
    if not isinstance(nested, Mapping):
        raise ValueError("evidence.factors must be a mapping")
    unknown = sorted(str(name) for name in nested if name not in VERIFICATION_FACTORS)
    if unknown:
        raise ValueError(f"evidence contains unknown factor(s): {', '.join(unknown)}")
    return dict(nested)


def _validate_factor_record(
    factor_name: str,
    record: Any,
    *,
    base_sha: str,
    head_sha: str,
) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError(f"evidence record for {factor_name!r} must be a mapping")
    normalized = copy.deepcopy(dict(record))
    status = normalized.get("status")
    if not isinstance(status, str) or status not in {"passed", "failed", "not_measured"}:
        raise ValueError(
            f"evidence record for {factor_name!r} must use status passed, failed, or not_measured"
        )
    record_base = normalized.get("base_sha")
    record_head = normalized.get("head_sha")
    if record_base is not None and (
        not isinstance(record_base, str) or record_base != base_sha
    ):
        raise ValueError(
            f"evidence record for {factor_name!r} has a base_sha that does not match the envelope"
        )
    if record_head is not None and (
        not isinstance(record_head, str) or record_head != head_sha
    ):
        raise ValueError(
            f"evidence record for {factor_name!r} has a head_sha that does not match the envelope"
        )
    if status == "not_measured":
        reason = normalized.get("reason", normalized.get("limitation"))
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError(f"not_measured record for {factor_name!r} needs a non-empty reason")
        return normalized

    commands = _nonempty_items(normalized.get("commands"), "commands", factor_name)
    results = _nonempty_items(normalized.get("results"), "results", factor_name, result=True)
    expected_present = "expected" in normalized
    actual_present = "actual" in normalized
    if expected_present != actual_present:
        raise ValueError(f"evidence record for {factor_name!r} must include both expected and actual")
    expected_actual = expected_present and _substantive_value(normalized.get("expected")) and _substantive_value(
        normalized.get("actual")
    )
    if status == "passed" and expected_present and normalized["expected"] != normalized["actual"]:
        raise ValueError(
            f"passed evidence for {factor_name!r} has expected/actual values that differ"
        )
    command_result = bool(commands and results)
    detail = _substantive_value(normalized.get("error")) or _substantive_value(
        normalized.get("details")
    )
    if not (command_result or expected_actual or (status == "failed" and detail)):
        raise ValueError(
            f"{status} evidence for {factor_name!r} needs non-empty commands/results or expected/actual data"
        )
    if "passed" in normalized and isinstance(normalized["passed"], bool) and not (
        command_result or expected_actual
    ):
        raise ValueError(
            f"boolean passed flag alone cannot prove {factor_name!r}; provide command/results or expected/actual data"
        )
    nonzero_returncodes = _nonzero_returncodes(results)
    if status == "passed" and nonzero_returncodes:
        raise ValueError(
            f"passed evidence for {factor_name!r} contains non-zero returncode(s): {nonzero_returncodes}"
        )
    if factor_name == "ui_behavior" and not _has_browser_evidence(normalized):
        raise ValueError(
            "ui_behavior evidence requires actual browser evidence; API/HTTP or runner traces alone are insufficient"
        )
    if commands:
        normalized["commands"] = commands
    if results:
        normalized["results"] = results
    return normalized


def _nonzero_returncodes(results: list[Any]) -> list[int | float]:
    values: list[int | float] = []
    for result in results:
        if not isinstance(result, Mapping):
            continue
        raw = result.get("returncode", result.get("return_code"))
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("structured result returncode must be a number")
        if raw != 0:
            values.append(raw)
    return values


def _has_browser_evidence(record: Mapping[str, Any]) -> bool:
    browser = record.get("browser", record.get("browser_evidence"))
    if isinstance(browser, Mapping):
        return bool(browser) and any(_substantive_value(value) for value in browser.values())
    if isinstance(browser, (list, tuple)):
        return bool(browser) and any(_substantive_value(value) for value in browser)
    return isinstance(browser, str) and bool(browser.strip())


def _nonempty_items(
    value: Any,
    label: str,
    factor_name: str,
    *,
    result: bool = False,
) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    elif isinstance(value, Mapping):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"evidence record for {factor_name!r} has invalid {label}")
    items = list(value)
    if not items:
        return []
    for item in items:
        if result:
            if not _substantive_result(item):
                raise ValueError(f"evidence record for {factor_name!r} has an empty result")
        elif not isinstance(item, str) or not item.strip():
            raise ValueError(f"evidence record for {factor_name!r} has an empty command")
    return items


def _substantive_result(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        if not value:
            return False
        # A result consisting solely of a pass/status boolean is no stronger
        # than the rejected top-level boolean flag.
        if set(value) <= {"passed", "status"}:
            return False
        return any(_substantive_value(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return bool(value) and any(_substantive_result(item) for item in value)
    return not isinstance(value, (bool, type(None)))


def _substantive_value(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value) and any(_substantive_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return bool(value) and any(_substantive_value(item) for item in value)
    return True


__all__ = [
    "DYNAMIC_FACTORS",
    "MAX_CANDIDATE_SITES",
    "MAX_LIFECYCLE_SITES",
    "STATIC_FACTORS",
    "VERIFICATION_FACTORS",
    "VERIFICATION_SCHEMA",
    "analyze_verification",
    "attach_verification",
]
