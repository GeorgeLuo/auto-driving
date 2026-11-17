from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qca.evaluation import Context
from scripts.validation.acceptance import REGISTRY, Shift, compare, question, skip_reason


def accepted(written: str) -> dict[str, str]:
    return {"outcome": "accepted", "written": written}


def rejected(error: str = "FieldError") -> dict[str, str]:
    return {"outcome": "rejected", "error": error}


class AcceptanceTests(unittest.TestCase):
    def test_each_differing_outcome_is_counted_under_its_document(self) -> None:
        rows = [
            ("a.Doc", "name = \"\"", accepted("{}"), rejected()),
            ("a.Doc", "name missing", rejected("KeyError"), accepted("{}")),
            ("a.Doc", "name = null", accepted('{"name": null}'), accepted("{}")),
            ("a.Doc", "count = true", rejected("ValueError"), rejected("FieldError")),
            ("a.Doc", "sample as written", accepted("{}"), accepted("{}")),
            ("a.Same", "sample as written", accepted("{}"), accepted("{}")),
            ("a.Errors", "x = 1", rejected("ValueError"), rejected("FieldError")),
            ("a.New", "sample as written", {"outcome": "absent"}, accepted("{}")),
        ]
        cases = [{"document": name, "label": label} for name, label, _, _ in rows]
        shifts = compare(cases, [before for *_, before, _ in rows], [after for *_, after in rows])
        self.assertEqual({name: shift.counts() for name, shift in shifts.items()},
                         {"a.Doc": [1, 1, 1, 1], "a.Errors": [0, 0, 0, 1]})
        self.assertEqual(shifts["a.Doc"].rewritten, ["name = null"])
        self.assertFalse(shifts["a.Errors"].behavior_changed)

    def test_the_question_counts_each_kind_and_spreads_its_examples_across_fields(self) -> None:
        shift = Shift(rejected=["ok missing", "ok = null", "ok = 1", "ok = null", "health = \" x \""],
                      rewritten=["error = null"])
        text = question("autonomy.runtime.frame_loop.FramePublication", shift)
        self.assertTrue(text.startswith(
            "FramePublication now rejects 5 inputs it accepted, writes back 1 input differently, such as "
            "`ok missing`, `error = null`, `health = \" x \"`, `ok = null`."), text)
        self.assertIn(REGISTRY, text)

    def test_only_a_change_to_production_python_with_a_registry_is_compared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            head = Path(directory)
            change = Context("change", head, ("cli/a.py",), base=head)
            self.assertIn("no tests/deterministic", skip_reason(change) or "")
            (head / REGISTRY).parent.mkdir(parents=True)
            (head / REGISTRY).write_text("")
            self.assertIsNone(skip_reason(change))
            self.assertIsNotNone(skip_reason(Context("change", head, ("docs/a.md", "tests/a.py"), base=head)))
            self.assertIsNotNone(skip_reason(Context("files", head, ("cli/a.py",))))


if __name__ == "__main__":
    unittest.main()
