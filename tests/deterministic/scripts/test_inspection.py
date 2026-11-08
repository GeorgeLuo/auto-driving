from __future__ import annotations

import unittest

from scripts.validation.inspection import inspect_sources, related_candidates, unused_candidates
from scripts.validation.source_index import symbols


class UsageInspectionTests(unittest.TestCase):
    def test_removing_last_production_call_exposes_unchanged_helper_despite_tests(self) -> None:
        before = {
            "pkg/helpers.py": "def restore_checkpoint():\n    return {}\n",
            "pkg/service.py": "from .helpers import restore_checkpoint\ndef run():\n    return restore_checkpoint()\nrun()\n",
            "tests/test_helpers.py": "from pkg.helpers import restore_checkpoint\ndef test_restore():\n    assert restore_checkpoint() == {}\n",
            "pkg/old.py": "def already_unused():\n    return 1\n",
        }
        after = {**before, "pkg/service.py": "def run():\n    return {}\nrun()\n"}
        findings = unused_candidates(before, after)
        self.assertEqual([(item.path, item.symbol, item.reason) for item in findings], [
            ("pkg/helpers.py", "restore_checkpoint", "newly unused"),
        ])
        self.assertEqual(findings[0].test_references, ("tests/test_helpers.py:3",))

    def test_field_writes_are_grouped_and_line_shifts_do_not_make_existing_debt_new(self) -> None:
        source = ("class Host:\n    def __init__(self):\n        self.last_result = None\n"
                  "    def run(self):\n        self.last_result = 1\nHost().run()\n")
        findings = unused_candidates({"pkg/host.py": source}, {"pkg/host.py": "\n\n" + source})
        self.assertEqual([(item.symbol, item.lines, item.reason) for item in findings], [
            ("Host.last_result", (5, 7), "existing in changed file"),
        ])

    def test_no_candidate_after_a_real_caller_is_added_or_definition_removed(self) -> None:
        before = {"pkg/helper.py": "def helper():\n    return 1\n"}
        self.assertEqual(unused_candidates(before, {"pkg/helper.py": ""}), [])
        after = {**before, "pkg/caller.py": "from .helper import helper\nanswer = helper()\n"}
        self.assertEqual(unused_candidates(before, after), [])

    def test_syntax_errors_fail_inspection_instead_of_reporting_an_empty_success(self) -> None:
        with self.assertRaises(SyntaxError):
            inspect_sources({}, {"pkg/broken.py": "def broken(:\n"})


class RelatedImplementationTests(unittest.TestCase):
    def test_upload_siblings_are_ranked_even_when_their_bodies_are_not_clones(self) -> None:
        old = (
            "def upload_plugin(source):\n"
            "    data = read_source(source)\n"
            "    receipt = send(data)\n"
            "    if receipt.ok:\n        return Result(receipt, Recovery('arm'))\n"
            "    return failure(receipt)\n"
            "def change_selection(data):\n    return Result(send(data), Recovery('list'))\n"
        )
        new = ("def upload_collection(manifest):\n    receipt = send(read_manifest(manifest))\n"
               "    if not receipt.ok:\n        return failure(receipt)\n"
               "    return Result(receipt, Recovery('list'))\n")
        matches = related_candidates({"pkg/service.py": old}, {"pkg/service.py": old + new})
        self.assertEqual(matches[0].changed.name, "upload_collection")
        self.assertEqual(matches[0].related.name, "upload_plugin")
        self.assertEqual(matches[0].shared_calls, ("Recovery", "Result", "failure", "send"))
        self.assertEqual(matches[0].relationship, "same module")
        self.assertIn("read_source", matches[0].related.code)

    def test_imports_expand_beyond_sibling_directory_in_either_direction(self) -> None:
        before = {
            "pkg/inner/source.py": "def existing():\n    return Result(send())\n",
            "pkg/outer/service.py": "from ..inner.source import existing\n",
            "unrelated/other.py": "def unrelated():\n    return Result(send())\n",
        }
        after = {**before, "pkg/outer/service.py": before["pkg/outer/service.py"] +
                 "def changed():\n    return Result(send())\n"}
        matches = related_candidates(before, after)
        self.assertEqual([(item.related.name, item.relationship) for item in matches], [
            ("existing", "import neighbor (static resolution)"),
        ])
        reverse = {**before, "pkg/inner/source.py": before["pkg/inner/source.py"] +
                   "def changed():\n    return Result(send())\n",
                   "pkg/outer/service.py": "from ..inner.source import existing\ndef caller():\n    return Result(send())\n"}
        self.assertTrue(any(item.related.name == "caller" for item in related_candidates(before, reverse)))

    def test_builtins_nested_bodies_and_line_moves_do_not_create_comparisons(self) -> None:
        source = ("def outer():\n    def inner():\n        return Result(send())\n"
                  "    return inner()\n"
                  "def other():\n    return len(str(1))\n")
        indexed = {item.name: item for item in symbols("pkg/source.py", source)}
        self.assertEqual(indexed["outer"].calls, frozenset({"inner"}))
        self.assertEqual(indexed["other"].calls, frozenset())
        self.assertEqual(related_candidates({"pkg/source.py": source}, {"pkg/source.py": "\n" + source}), [])

    def test_order_is_stable_and_only_three_matches_are_retained_per_changed_function(self) -> None:
        before = {f"pkg/{name}.py": f"def {name}():\n    return Result(send())\n" for name in "abcd"}
        after = {**before, "pkg/new.py": "def added():\n    return Result(send())\n"}
        matches = related_candidates(before, after)
        self.assertEqual([item.related.name for item in matches], ["a", "b", "c"])
        self.assertEqual(matches, related_candidates(dict(reversed(list(before.items()))),
                                                    dict(reversed(list(after.items())))))


if __name__ == "__main__":
    unittest.main()
