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
``not_measured`` by :func:`attach_verification` and receive a default reason.
An explicitly supplied ``not_measured`` record must include a non-empty
``reason`` or ``limitation``.  A ``passed`` or ``failed`` record must contain
non-empty command/result material or a non-empty expected/actual pair.  A lone
``{"passed": true}`` is not evidence.  The
whole evidence envelope, including provenance, must contain only JSON-safe
values: built-in scalar values with finite floats, mappings with string keys,
and list/tuple arrays (tuples are normalized to lists).  This module does not
execute commands, inspect Git, or attest that provenance is genuine.

The static scan has similarly narrow semantics:

* test assertions are counted only in test-like files or test-named callables;
* literal, same-operand, assignment-readback, string-expected, and formatted-literal assertions are
  reported as *candidates*, not bad tests;
* private production imports and helper calls from tests are candidates, while
  same-module test helpers are not; numeric literals alone are not a signal;
* lifecycle metrics count recognized definitions and calls, with a bounded
  list of sites; and
* no static metric proves lifecycle start/stop/reset/cleanup symmetry.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Any

from ..indicators.context import AnalysisContext
from ..indicators import lifecycle as _lifecycle_indicator
from ..indicators import test_effectiveness as _test_effectiveness_indicator

VERIFICATION_SCHEMA = "qca/verification/v1"
VERIFICATION_FACTORS = (
    "test_effectiveness",
    "end_to_end",
    "ui_behavior",
    "lifecycle",
)
STATIC_FACTORS = ("test_effectiveness", "lifecycle")
DYNAMIC_FACTORS = ("end_to_end", "ui_behavior")

# Lifecycle site lists are evidence for inspection, not a complete program
# inventory. Counts remain exact (for successfully parsed Python inputs) while
# the list stays bounded so a large repository cannot produce an unwieldy report.
MAX_LIFECYCLE_SITES = _lifecycle_indicator.MAX_LIFECYCLE_SITES


def analyze_verification(
    sources: dict[str, str],
    *,
    revision: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return conservative static verification factors for source contents.

    sources remains the historic path-to-text API. Static detection is
    delegated to independently callable verification indicators through one
    shared family-specific AnalysisContext; runtime evidence remains attached
    only by attach_verification.
    """

    _validate_sources(sources)
    return analyze_verification_context(
        AnalysisContext.from_sources(sources, revision=revision)
    )


def analyze_verification_context(context: AnalysisContext) -> dict[str, dict[str, Any]]:
    """Measure verification indicators from an already shared context."""

    factors = {
        "test_effectiveness": _test_effectiveness_indicator.analyze(context),
        "end_to_end": _not_measured_factor(
            "No subprocess or integration-run evidence was supplied; static source inspection cannot establish end-to-end behavior."
        ),
        "ui_behavior": _not_measured_factor(
            "No actual browser interaction evidence was supplied; loopback/API traces alone do not establish UI behavior."
        ),
        "lifecycle": _lifecycle_indicator.analyze(context),
    }
    return factors


def _validate_sources(sources: Mapping[str, str]) -> None:
    if not isinstance(sources, Mapping):
        raise TypeError("sources must be a mapping of paths to source text")
    for path, text in sources.items():
        if not isinstance(path, str) or not path.strip():
            raise TypeError("source paths must be non-empty strings")
        if not isinstance(text, str):
            raise TypeError(f"source text for {path!r} must be a string")


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
    factor receives a ``verification`` object containing a validated
    JSON-compatible copy of the record, revisions, schema, and caller-supplied
    provenance.

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
    evidence = _json_compatible_copy(evidence, "evidence")

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


def _validate_revision(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or any(char.isspace() for char in value):
        raise ValueError(f"{label} must be a non-empty revision/ref string without whitespace")


def _not_measured_factor(reason: str) -> dict[str, Any]:
    return {
        "status": "not_measured",
        "metrics": {},
        "findings": [],
        "limitations": [reason],
    }


# Compatibility aliases for private detector helpers used by older callers.
_test_effectiveness_factor = _test_effectiveness_indicator._test_effectiveness_factor
_lifecycle_factor = _lifecycle_indicator._lifecycle_factor
_is_literal_expression = _test_effectiveness_indicator._is_literal_expression
_is_same_operand_compare = _test_effectiveness_indicator._is_same_operand_compare
_assignment_readbacks = _test_effectiveness_indicator.assignment_readbacks
_lifecycle_operation = _lifecycle_indicator.lifecycle_operation
_call_name = _lifecycle_indicator._call_name


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


def _json_compatible_copy(value: Any, path: str, active: set[int] | None = None) -> Any:
    """Return a JSON-safe copy of an evidence value or reject it.

    Evidence is deliberately limited to the built-in JSON scalar types, finite
    floats, mappings with string keys, and list/tuple arrays.  Tuples are
    accepted as the Python spelling of a JSON array and normalized to lists.
    Mapping and sequence containers are copied while traversing so custom
    container implementations cannot remain in the attached report.  The
    active container ids detect cycles while allowing shared sub-values.
    """

    if type(value) in (type(None), bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value

    if isinstance(value, Mapping):
        marker = id(value)
        active = set() if active is None else active
        if marker in active:
            raise ValueError(f"{path} contains a cyclic reference")
        active.add(marker)
        try:
            normalized: dict[str, Any] = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError(f"{path} contains a non-string object key")
                normalized[key] = _json_compatible_copy(item, f"{path}.{key}", active)
            return normalized
        finally:
            active.remove(marker)

    if isinstance(value, (list, tuple)):
        marker = id(value)
        active = set() if active is None else active
        if marker in active:
            raise ValueError(f"{path} contains a cyclic reference")
        active.add(marker)
        try:
            return [
                _json_compatible_copy(item, f"{path}[{index}]", active)
                for index, item in enumerate(value)
            ]
        finally:
            active.remove(marker)

    raise ValueError(f"{path} contains unsupported value type {type(value).__name__}")


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
        reason = normalized.get("reason")
        limitation = normalized.get("limitation")
        has_reason = isinstance(reason, str) and bool(reason.strip())
        has_limitation = isinstance(limitation, str) and bool(limitation.strip())
        if not (has_reason or has_limitation):
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
        return any(_substantive_value(value) for value in browser.values())
    if isinstance(browser, (list, tuple)):
        return any(_substantive_value(value) for value in browser)
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
        return any(_substantive_result(item) for item in value)
    return not isinstance(value, (bool, type(None)))


def _substantive_value(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_substantive_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_substantive_value(item) for item in value)
    return True


__all__ = [
    "DYNAMIC_FACTORS",
    "MAX_LIFECYCLE_SITES",
    "STATIC_FACTORS",
    "VERIFICATION_FACTORS",
    "VERIFICATION_SCHEMA",
    "analyze_verification",
    "analyze_verification_context",
    "attach_verification",
]
