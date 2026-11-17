"""Every document Automa writes and reads back follows ``autonomy.fields``.

A class with both ``from_dict`` and ``to_dict`` registers samples in
``DOCUMENTS``. Each sample round-trips. Each mutation of a sample (a dropped
key, an added key, null, a value of another type, an empty or padded string, a
non-finite number) either raises ``FieldError`` naming the mutated field or a
sibling its rule depends on, or is accepted and written back unchanged.

The quality check reads ``cases()`` on both sides of a change and reports
which inputs each document now accepts, rejects, or writes back differently.
"""

from __future__ import annotations

import ast
import importlib
import json
import math
import unittest
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from autonomy.decision_cycle.cycle import CYCLE_RECORD_KEYS
from autonomy.fields import ROOT, FieldError
from autonomy.runtime.frame_loop import FramePublication
from autonomy.runtime.report import HostReport, ReportApplication

REPOSITORY = Path(__file__).resolve().parents[3]
MISSING = object()


@dataclass(frozen=True)
class Document:
    samples: Callable[[], list[object]]
    opaque: tuple[str, ...] = ()  # JSON the document carries without reading; mutated only as a whole


def _application() -> ReportApplication:
    return ReportApplication(applied=True, mode="autonomy", reason="applied", steering=0.25, throttle=-0.5)


def _report(**fields: object) -> HostReport:
    return HostReport(**{
        "host_name": "fixture", "run_id": "run-1", "generation_id": "decision-1", "frame_id": "frame-1",
        "frame_index": 3, "timestamp_ms": 1000, "published_at_ms": 1010,
        "cycle": dict.fromkeys(CYCLE_RECORD_KEYS), "application": _application(),
        "values": {"source_id": "fixture:local", "stale_after_ms": 1000, "activation": {"steps": {}}},
        **fields,
    })


DOCUMENTS = {
    "autonomy.runtime.report.ReportApplication": Document(lambda: [_application()]),
    "autonomy.runtime.report.HostReport": Document(
        lambda: [_report(), _report(status="error", error="cycle raised")], opaque=("values",),
    ),
    "autonomy.runtime.frame_loop.FramePublication": Document(lambda: [
        FramePublication(health="healthy", read_at_ms=1100, result_age_ms=90, stale_after_ms=1000, report=_report()),
        FramePublication(health="warming", read_at_ms=1100, result_age_ms=None, stale_after_ms=1000, report=None),
    ], opaque=("report.values",)),
}
# Documents that predate the field rules. Remove a name when its class follows autonomy.fields and registers above.
PENDING = frozenset({
    "autonomy.decision_cycle.memory.beliefs.Belief",
    "autonomy.decision_cycle.memory.beliefs.Check",
    "autonomy.decision_cycle.memory.beliefs.Trial",
    "autonomy.decision_cycle.memory.evidence.MemoryOrigin",
    "autonomy.decision_cycle.memory.evidence.RetainedEvidence",
    "autonomy.decision_cycle.memory.interface.MemoryPluginReport",
    "autonomy.decision_cycle.memory.interface.MemoryReport",
    "autonomy.decision_cycle.observation.values.Observation",
    "autonomy.decision_cycle.perception.evidence.values.PerceivedThing",
    "autonomy.decision_cycle.perception.evidence.values.PerceptionSignal",
    "autonomy.decision_cycle.perception.evidence.values.ViewLocation",
    "autonomy.decision_cycle.perception.interface.PerceptionPluginRun",
    "autonomy.decision_cycle.perception.interface.PerceptionText",
    "autonomy.decision_cycle.proposal.values.ActionProposal",
    "autonomy.decision_cycle.proposal.values.ProposedVehicleCommand",
    "autonomy.decision_cycle.proposal.values.SourceRef",
    "autonomy.runtime.execution.ControlApplication",
    "autonomy.runtime.session.RunConfiguration",
    "cli.automa_cli.hosts.runs.models.RunState",
    "cli.automa_cli.hosts.runs.models.WorkerProcess",
    "implementations.decision_cycle.memory.shared.evidence_ledger.ledger.EvidenceLedger",
    "implementations.decision_cycle.memory.shared.evidence_ledger.ledger.LedgerBounds",
    "implementations.decision_cycle.perception.shared.image.stored.StoredImage",
})


class DocumentTests(unittest.TestCase):
    def test_every_document_class_is_registered_or_pending(self) -> None:
        found = set(_document_classes())
        self.assertEqual(sorted(found - DOCUMENTS.keys() - PENDING), [],
                         "Register these in DOCUMENTS so their from_dict and to_dict follow autonomy.fields.")
        self.assertEqual(sorted(PENDING - found), [], "Remove these from PENDING; they no longer exist.")
        self.assertEqual(sorted(PENDING & DOCUMENTS.keys()), [], "Remove registered documents from PENDING.")

    def test_samples_round_trip(self) -> None:
        for name, document in DOCUMENTS.items():
            parse = _class(name).from_dict
            for sample in document.samples():
                written = sample.to_dict()
                with self.subTest(document=name, sample=written):
                    self.assertEqual(parse(deepcopy(written)), sample)
                    self.assertTrue(_same(parse(deepcopy(written)).to_dict(), written))

    def test_each_mutation_is_rejected_by_field_or_written_back_unchanged(self) -> None:
        for name, document in DOCUMENTS.items():
            parse = _class(name).from_dict
            for sample in document.samples():
                for path, original, replacement, mutated in _mutations(sample.to_dict(), document.opaque):
                    with self.subTest(document=name, field=path, value=replacement):
                        self.assertIsNone(_judge(parse, path, original, replacement, mutated))


def cases() -> Iterator[tuple[str, str, dict[str, object]]]:
    """Each registered document's samples and their mutations, as (class name, label, payload)."""

    for name, document in DOCUMENTS.items():
        for sample in document.samples():
            written = sample.to_dict()
            yield name, "sample as written", written
            for path, original, replacement, mutated in _mutations(written, document.opaque):
                yield name, _label(path, original, replacement), mutated


def _label(path: str, original: object, replacement: object) -> str:
    if replacement is MISSING:
        return f"{path} missing"
    if original is MISSING:
        return f"{path} added"
    return f"{path} = {json.dumps(replacement)}"


def _judge(parse: Callable[[object], object], path: str, original: object, replacement: object,
           mutated: dict[str, object]) -> str | None:
    """Why ``parse`` mishandled this mutation, or None when it followed the rules."""

    try:
        parsed = parse(mutated)
    except FieldError as error:
        if error.field_name in (path, ROOT) or _parent(error.field_name) == _parent(path):
            return None
        return f"FieldError names {error.field_name!r}, not the field or a sibling"
    except Exception as error:  # Any other exception breaks the rule that parsers raise FieldError.
        return f"raised {type(error).__name__}: {error}; parsers raise FieldError"
    return _accepted(parsed, path, original, replacement, mutated)


def _accepted(parsed: object, path: str, original: object, replacement: object,
              mutated: dict[str, object]) -> str | None:
    """Why accepting this mutation broke a rule, or None when the document writes it back unchanged."""

    if replacement is MISSING:
        return "accepted a missing key; every key is required and None is written as null"
    if original is MISSING:
        return "accepted a key the document does not write"
    if isinstance(replacement, float) and not math.isfinite(replacement):
        return "accepted a non-finite number"
    if None not in (original, replacement) and _kind(original) != _kind(replacement):
        return f"accepted {replacement!r} ({_kind(replacement)}) where the document writes {_kind(original)}"
    written = _get(parsed.to_dict(), path)
    if written is MISSING:
        return f"accepted {replacement!r} and left the key out when writing"
    return None if _same(parsed.to_dict(), mutated) else f"accepted {replacement!r} and wrote back {written!r}"


def _mutations(document: dict[str, object], opaque: tuple[str, ...]) -> Iterator[tuple[str, object, object, dict[str, object]]]:
    for path, value in _fields(document, ROOT, opaque):
        yield path, value, MISSING, _replaced(document, path, MISSING)
        for replacement in _replacements(value):
            yield path, value, replacement, _replaced(document, path, replacement)
    for path in _objects(document, ROOT, opaque):
        added = "unexpected" if path == ROOT else f"{path}.unexpected"
        yield added, MISSING, 0, _replaced(document, added, 0)


def _replacements(value: object) -> list[object]:
    if value is None:
        return ["", 0, {}]
    return {
        "bool": [None, 1, "true"],
        "number": [None, True, -1, 1.5, math.nan, math.inf, "1"],
        "string": [None, "", f" {value} ", 0],
        "object": [None, [], "x"],
        "array": [None, {}, "x"],
    }[_kind(value)]


def _kind(value: object) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    return "array" if isinstance(value, (list, tuple)) else type(value).__name__


def _fields(value: dict[str, object], path: str, opaque: tuple[str, ...]) -> Iterator[tuple[str, object]]:
    for key, item in value.items():
        child = key if path == ROOT else f"{path}.{key}"
        yield child, item
        if isinstance(item, dict) and child not in opaque:
            yield from _fields(item, child, opaque)


def _objects(value: dict[str, object], path: str, opaque: tuple[str, ...]) -> Iterator[str]:
    yield path
    for key, item in value.items():
        child = key if path == ROOT else f"{path}.{key}"
        if isinstance(item, dict) and child not in opaque:
            yield from _objects(item, child, opaque)


def _replaced(document: dict[str, object], path: str, replacement: object) -> dict[str, object]:
    copied = deepcopy(document)
    *parents, key = path.split(".")
    target = copied
    for parent in parents:
        target = target[parent]
    if replacement is MISSING:
        del target[key]
    else:
        target[key] = deepcopy(replacement)
    return copied


def _get(document: object, path: str) -> object:
    for key in path.split("."):
        if not isinstance(document, dict) or key not in document:
            return MISSING
        document = document[key]
    return document


def _same(left: object, right: object) -> bool:
    """Equal as JSON: a bool is never a number, and an int equals the float it names."""

    if _kind(left) != _kind(right):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(map(_same, left, right))
    return left == right


def _parent(path: str) -> str:
    return path.rpartition(".")[0]


def _class(name: str) -> type:
    module, _, attribute = name.rpartition(".")
    return getattr(importlib.import_module(module), attribute)


def _document_classes() -> Iterator[str]:
    """Production classes that define both ``from_dict`` and ``to_dict``."""

    for root in ("autonomy", "cli", "implementations"):
        for path in sorted((REPOSITORY / root).rglob("*.py")):
            module = path.relative_to(REPOSITORY).with_suffix("").as_posix().replace("/", ".")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ClassDef) and {"from_dict", "to_dict"} <= {
                        item.name for item in node.body if isinstance(item, ast.FunctionDef)}:
                    yield f"{module}.{node.name}"


if __name__ == "__main__":
    unittest.main()
