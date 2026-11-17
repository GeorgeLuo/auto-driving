"""Which inputs a change makes each registered document accept, reject, or write back differently.

The head's ``tests/deterministic/autonomy/test_documents.py`` gives the cases:
each registered document's samples and their mutations. Both sides of the
change parse every case with their own ``from_dict`` and write it back with
``to_dict``, in a separate Python that ``document_probe.py`` runs. A document
whose outcomes differ gets one review question, which asks whether the change
describes them.
"""

from __future__ import annotations

import ast
import itertools
import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from qca.evaluation import Context, Finding, Location, finding, location

PROBE = Path(__file__).with_name("document_probe.py")
REGISTRY = "tests/deterministic/autonomy/test_documents.py"
PRODUCTION = ("autonomy/", "cli/", "implementations/")
TIMEOUT_S = 300
EXAMPLES = 4
COLUMNS = ("Newly rejected", "Newly accepted", "Written differently", "Rejected with another error")


@dataclass
class Shift:
    """One document's cases whose outcome differs between base and head."""

    rejected: list[str] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)
    rewritten: list[str] = field(default_factory=list)
    errors: int = 0

    @property
    def behavior_changed(self) -> bool:
        return bool(self.rejected or self.accepted or self.rewritten)

    def counts(self) -> list[int]:
        return [len(self.rejected), len(self.accepted), len(self.rewritten), self.errors]


def skip_reason(context: Context) -> str | None:
    if context.base is None:
        return "compares the two sides of a change"
    if not (context.head / REGISTRY).is_file():
        return f"the head has no {REGISTRY}"
    if not any(path.startswith(PRODUCTION) and path.endswith(".py") for path in context.paths):
        return "the change has no production Python"
    return None


def acceptance_changes(context: Context) -> tuple[list[Finding], dict[str, Shift]]:
    """One review question per document whose behavior changed, and every document's shift."""

    assert context.base is not None
    cases = _probe(context.head, "head", "cases")
    shifts = compare(cases, _probe(context.base, "base", "judge", cases), _probe(context.head, "head", "judge", cases))
    found = [finding("acceptance", "acceptance-change", _place(context.head, name), question(name, shift),
                     evidence={"introduced": True, **dict(zip(COLUMNS, shift.counts(), strict=True))})
             for name, shift in sorted(shifts.items()) if shift.behavior_changed]
    return found, shifts


def compare(cases: list[dict], base: list[dict], head: list[dict]) -> dict[str, Shift]:
    """The shift of each document with a differing outcome; a document new in the head is left out."""

    shifts: dict[str, Shift] = {}
    for case, before, after in zip(cases, base, head, strict=True):
        if "absent" in (before["outcome"], after["outcome"]):
            continue
        shift = shifts.setdefault(case["document"], Shift())
        if before["outcome"] != after["outcome"]:
            (shift.rejected if after["outcome"] == "rejected" else shift.accepted).append(case["label"])
        elif before["outcome"] == "accepted" and before["written"] != after["written"]:
            shift.rewritten.append(case["label"])
        elif before["outcome"] == "rejected" and before["error"] != after["error"]:
            shift.errors += 1
    return {name: shift for name, shift in shifts.items() if shift.behavior_changed or shift.errors}


def question(name: str, shift: Shift) -> str:
    parts = [f"{verb} {_inputs(len(labels))}{rest}" for verb, rest, labels in (
        ("rejects", " it accepted", shift.rejected),
        ("accepts", " it rejected", shift.accepted),
        ("writes back", " differently", shift.rewritten),
    ) if labels]
    return (f"{name.rpartition('.')[2]} now {', '.join(parts)}, such as "
            f"{', '.join(f'`{label}`' for label in _examples(shift))}. Does the change describe each one? "
            f"The inputs are the samples and mutations in {REGISTRY}.")


def _examples(shift: Shift) -> list[str]:
    """Labels from each kind of change in turn, each field once before any field twice."""

    alternating = itertools.chain.from_iterable(itertools.zip_longest(shift.rejected, shift.accepted, shift.rewritten))
    labels = [label for label in dict.fromkeys(alternating) if label is not None]
    seen: Counter[str] = Counter()
    ranked = []
    for index, label in enumerate(labels):
        field_name = label.split(" ")[0]
        ranked.append((seen[field_name], index, label))
        seen[field_name] += 1
    return [label for _, _, label in sorted(ranked)[:EXAMPLES]]


def _inputs(count: int) -> str:
    return f"{count} input{'' if count == 1 else 's'}"


def _probe(tree: Path, side: str, mode: str, cases: list[dict] | None = None) -> list[dict]:
    completed = subprocess.run(
        [sys.executable, "-P", str(PROBE), mode],
        cwd=tree,
        env={**os.environ, "PYTHONPATH": str(tree), "PYTHONDONTWRITEBYTECODE": "1"},
        input="" if cases is None else json.dumps(cases),
        capture_output=True, text=True, timeout=TIMEOUT_S, check=False,
    )
    if completed.returncode:
        last = (completed.stderr.strip().splitlines() or ["no output"])[-1]
        raise RuntimeError(f"document probe `{mode}` failed in the {side}: {last}")
    return json.loads(completed.stdout)


def _place(head: Path, name: str) -> Location:
    """The class definition in the head, or the registry when the class is not in a module file."""

    module, _, symbol = name.rpartition(".")
    path = module.replace(".", "/") + ".py"
    try:
        parsed = ast.parse((head / path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return location(REGISTRY, 1)
    for node in ast.walk(parsed):
        if isinstance(node, ast.ClassDef) and node.name == symbol:
            return location(path, node.lineno, symbol=symbol)
    return location(REGISTRY, 1)
