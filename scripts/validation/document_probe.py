"""Run one tree's documents on the registered cases, for the quality check's acceptance comparison.

The check runs this file with ``python -P`` in each side of a change, with that
side as the working directory and ``PYTHONPATH``, so it imports only the
standard library and the side's own modules.

``cases`` prints the cases ``tests/deterministic/autonomy/test_documents.py``
registers. ``judge`` reads cases on standard input and prints, for each,
whether this side's ``from_dict`` accepted it and what ``to_dict`` wrote back,
or which error it raised.
"""

from __future__ import annotations

import importlib
import json
import sys

REGISTRY = "tests.deterministic.autonomy.test_documents"


def cases() -> list[dict[str, object]]:
    registry = importlib.import_module(REGISTRY)
    return [{"document": name, "label": label, "payload": payload} for name, label, payload in registry.cases()]


def judge(case: dict[str, object]) -> dict[str, str]:
    module, _, name = str(case["document"]).rpartition(".")
    try:
        document = getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError):
        return {"outcome": "absent"}
    try:
        written = document.from_dict(case["payload"]).to_dict()
    except Exception as exc:  # Which error a document raises is part of what the check compares.
        return {"outcome": "rejected", "error": type(exc).__name__}
    return {"outcome": "accepted", "written": json.dumps(written, sort_keys=True, default=repr)}


def main(mode: str) -> None:
    # Anything a module prints while importing goes to stderr, so standard output stays JSON.
    output, sys.stdout = sys.stdout, sys.stderr
    if mode == "cases":
        json.dump(cases(), output)
    elif mode == "judge":
        json.dump([judge(case) for case in json.load(sys.stdin)], output)
    else:
        raise SystemExit("usage: document_probe.py cases|judge")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) == 2 else "")
