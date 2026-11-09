from __future__ import annotations

import copy
import json
import unittest

from qca import analyze_sources, report_to_dict

from qca.factors.verification import (
    VERIFICATION_SCHEMA,
    analyze_verification,
    attach_verification,
)


class VerificationFactorTests(unittest.TestCase):
    def test_test_file_count_excludes_non_python_and_keeps_parse_failures(self) -> None:
        factor = analyze_verification({
            "tests/README.md": "# Test guide\n",
            "tests/data.json": "{}\n",
            "tests/support.py": "value = 1\n",
            "tests/broken.py": "def broken(:\n",
            "test_feature.PY": "def test_feature():\n    assert True\n",
            "test_api.PYI": "def test_api() -> None: ...\n",
            "app.py": "value = 1\n",
            "broken.py": "def broken(:\n",
        })["test_effectiveness"]
        self.assertEqual(factor["metrics"]["source_file_count"], 8)
        self.assertEqual(factor["metrics"]["python_file_count"], 6)
        self.assertEqual(factor["metrics"]["test_file_count"], 4)
        self.assertEqual(factor["metrics"]["parse_error_count"], 1)
        self.assertEqual(factor["metrics"]["test_case_count"], 2)

    def test_reports_string_expected_and_private_surface_candidates(self) -> None:
        factors = analyze_verification(
            {
                "pkg/app.py": "def run():\n    return 1\n\ndef _hidden():\n    return 2\n",
                "tests/test_app.py": """
from pkg.app import run
from pkg.app import _hidden

def _local_helper():
    return run()

class Suite:
    def test_behavior(self):
        markdown = "report"
        self.assertEqual(run(), 1)
        self.assertGreater(run(), 0)
        self.assertIn("| production | 8 | 80.0% |", markdown)
        self.assertIn("Production vs tests", markdown)
        self.assertEqual(_hidden(), 2)
        _local_helper()
""",
            }
        )
        metrics = factors["test_effectiveness"]["metrics"]
        kinds = [item["kind"] for item in factors["test_effectiveness"]["findings"]]
        self.assertEqual(metrics["assertion_count"], 5)
        self.assertEqual(metrics["literal_assertion_candidates"], 0)
        self.assertEqual(metrics["string_expected_assertion_count"], 2)
        self.assertEqual(metrics["formatted_literal_assertion_count"], 1)
        self.assertEqual(metrics["private_import_count"], 1)
        self.assertEqual(metrics["private_import_test_file_count"], 1)
        self.assertEqual(metrics["private_helper_call_count"], 1)
        self.assertEqual(kinds.count("formatted_literal_assertion"), 1)
        self.assertEqual(kinds.count("string_expected_assertion"), 1)
        self.assertEqual(kinds.count("private_import"), 1)
        self.assertEqual(kinds.count("private_helper_call"), 1)
        self.assertFalse(
            any(item["path"] == "pkg/app.py" for item in factors["test_effectiveness"]["findings"])
        )
        self.assertTrue(all(item["message"] for item in factors["test_effectiveness"]["findings"]))
        self.assertTrue(
            any("private production" in limitation.lower() for limitation in factors["test_effectiveness"]["limitations"])
        )

    def test_assertion_candidate_metrics_cover_distinct_and_overlapping_kinds(self) -> None:
        factor = analyze_verification({
            "tests/test_examples.py": (
                "def test_examples(self, value, output):\n"
                "    assert True\n"
                "    assert value == value\n"
                "    assigned = 1\n"
                "    assert assigned == 1\n"
                "    self.assertEqual(fetch(), 'ready')\n"
                "    self.assertIn('| report |', output)\n"
            ),
        })["test_effectiveness"]
        metrics = factor["metrics"]
        self.assertEqual(metrics["assertion_count"], 5)
        self.assertEqual(metrics["candidate_assertion_count"], 5)
        self.assertEqual(metrics["literal_assertion_candidates"], 1)
        self.assertEqual(metrics["tautological_assertion_candidates"], 1)
        self.assertEqual(metrics["assignment_readback_candidates"], 1)
        self.assertEqual(metrics["string_expected_assertion_count"], 2)
        self.assertEqual(metrics["formatted_literal_assertion_count"], 1)
        self.assertEqual(len(factor["findings"]), 5)

    def test_consumer_report_locates_assignment_and_readback(self) -> None:
        """Consumer: the serialized report identifies both ends of a readback."""
        cases = [
            ("value = 3", "assert value == 3"),
            ("value: int = -3", "self.assertEqual(-3, value)"),
            ("obj.value = 3", "assert obj.value == 3"),
            ('value = "ready"', 'self.assertEqual(value, "ready")'),
            ("value = None", "self.assertIs(value, None)"),
        ]
        for assignment, assertion in cases:
            with self.subTest(assignment=assignment, assertion=assertion):
                source = f"def test_example(self, obj):\n    {assignment}\n    {assertion}\n"
                payload = report_to_dict(analyze_sources({"tests/test_sample.py": source}))
                factor = payload["factors"]["test_effectiveness"]
                self.assertEqual(factor["metrics"]["assignment_readback_candidates"], 1)
                self.assertEqual(factor["metrics"]["candidate_assertion_count"], 1)
                candidate = next(f for f in factor["findings"] if f["kind"] == "assignment_readback")
                self.assertEqual((candidate["path"], candidate["line"], candidate["column"]), ("tests/test_sample.py", 3, 5))
                self.assertEqual(candidate["assignment"], {
                    "path": "tests/test_sample.py", "line": 2, "column": 5,
                    "expression": assignment,
                })
                self.assertIn(assertion.removeprefix("assert "), candidate["expression"])

    def test_boundary_readback_tracking_stops_at_behavior_or_uncertainty(self) -> None:
        """Boundary: transformations and uncertain state must not look like direct readbacks."""
        cases = [
            'result = api.normalize({"value": "3"})\nassert result["value"] == 3',
            'value = 3\nassert serialize(value) == 3',
            'obj.value = 3\nnormalize(obj)\nassert obj.value == 3',
            'value = 3\nvalue += 1\nassert value == 3',
            'value = 3\nvalue = normalize(value)\nassert value == 3',
            'obj.value = 3\nobj = other\nassert obj.value == 3',
            'obj.value = 3\nother.value = 3\nassert obj.value == 3',
            'value = 3\nif condition:\n    value = 4\nassert value == 3',
            'value = 3\nassert value == 3, mutate()',
            'value = 3\nself.assertEqual(value, mutate())',
            'value = 3\nassert value == 4',
            'value = 3\nassert value != 3',
            'value = 3\nalias = value\nassert alias == 3',
            'obj = Model(value=3)\nassert obj.value == 3',
            'data["value"] = 3\nassert data["value"] == 3',
            'value = 3\ndef nested():\n    assert value == 3',
        ]
        for body in cases:
            with self.subTest(body=body):
                source = "def test_example(self, obj):\n" + "\n".join("    " + line for line in body.splitlines()) + "\n"
                factor = analyze_sources({"tests/test_sample.py": source}).factors["test_effectiveness"]
                self.assertEqual(factor["metrics"]["assignment_readback_candidates"], 0)
                self.assertFalse(any(f["kind"] == "assignment_readback" for f in factor["findings"]))

    def test_boundary_readback_scopes_and_reassignments(self) -> None:
        """Boundary: track the latest local literal without crossing test scopes."""
        source = """def test_first():
    value = 3
    other = 4
    value = 5
    assert value == 5

def test_second():
    assert value == 5

def helper():
    value = 3
    assert value == 3
"""
        factor = analyze_sources({"tests/test_sample.py": source}).factors["test_effectiveness"]
        candidates = [f for f in factor["findings"] if f["kind"] == "assignment_readback"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual((candidates[0]["assignment"]["line"], candidates[0]["line"]), (4, 5))

    def test_attach_promotes_only_dynamic_factors_and_preserves_static_payload(self) -> None:
        factors = analyze_verification(
            {
                "tests/test_example.py": "def test_one():\n    assert value == value\n",
            }
        )
        original = copy.deepcopy(factors)
        evidence = {
            "schema": VERIFICATION_SCHEMA,
            "base_sha": "base-ref",
            "head_sha": "head-ref",
            "provenance": {"runner": "unit-test", "artifact": "capture.json"},
            "factors": {
                "end_to_end": {
                    "status": "passed",
                    "commands": ["python -m pytest tests/deterministic/cli/test_workbench.py"],
                    "results": [{"returncode": 0, "tests": 30}],
                    "expected": {"phase": "completed"},
                    "actual": {"phase": "completed"},
                },
                "ui_behavior": {
                    "status": "failed",
                    "commands": ["python -m unittest tests.deterministic.cli.test_workbench"],
                    "results": [{"returncode": 1, "stderr": "toggle mismatch"}],
                    "browser": {"steps": ["toggle plugin", "inspect frame"]},
                    "expected": {"plugin_runs": ["classical_regions"]},
                    "actual": {"plugin_runs": []},
                },
            },
        }

        attached = attach_verification(factors, evidence, "base-ref", "head-ref")
        self.assertEqual(attached["end_to_end"]["status"], "verified")
        self.assertEqual(attached["ui_behavior"]["status"], "failed")
        self.assertEqual(attached["test_effectiveness"]["status"], original["test_effectiveness"]["status"])
        self.assertEqual(attached["test_effectiveness"]["metrics"], original["test_effectiveness"]["metrics"])
        self.assertEqual(
            attached["end_to_end"]["verification"]["provenance"],
            evidence["provenance"],
        )
        self.assertEqual(attached["end_to_end"]["verification"]["claim_source"], "caller_supplied")
        self.assertEqual(attached["end_to_end"]["verification"]["record"]["status"], "passed")
        evidence["provenance"]["runner"] = "mutated"
        self.assertEqual(
            attached["end_to_end"]["verification"]["provenance"]["runner"],
            "unit-test",
        )

    def test_attach_nested_evidence_is_json_serializable(self) -> None:
        report = analyze_sources({"tests/test_example.py": "def test_one():\n    assert value == value\n"})
        shared = {"source": "same"}
        evidence = {
            "schema": VERIFICATION_SCHEMA,
            "base_sha": "base-ref",
            "head_sha": "head-ref",
            "provenance": {
                "runner": "unit-test",
                "metadata": [{"elapsed": 1.5, "tags": ("nested", "tuple")}],
                "shared_first": shared,
                "shared_second": shared,
            },
            "factors": {
                "end_to_end": {
                    "status": "passed",
                    "commands": ("python -m pytest",),
                    "results": [{"returncode": 0, "details": {"checks": ["one", {"ok": True}]}}],
                    "expected": {"phase": "completed", "checks": ("one",)},
                    "actual": {"phase": "completed", "checks": ("one",)},
                },
            },
        }

        report.factors = attach_verification(report.factors, evidence, "base-ref", "head-ref")
        payload = report_to_dict(report)
        json.dumps(payload, allow_nan=False)
        self.assertEqual(
            payload["factors"]["end_to_end"]["verification"]["record"]["commands"],
            ["python -m pytest"],
        )
        self.assertEqual(
            payload["factors"]["end_to_end"]["verification"]["provenance"]["shared_first"],
            payload["factors"]["end_to_end"]["verification"]["provenance"]["shared_second"],
        )

    def test_attach_rejects_non_json_evidence_values(self) -> None:
        base = analyze_verification({})
        common = {
            "schema": VERIFICATION_SCHEMA,
            "base_sha": "base-ref",
            "head_sha": "head-ref",
            "factors": {
                "end_to_end": {
                    "status": "passed",
                    "commands": ["pytest"],
                    "results": [{"returncode": 0}],
                },
            },
        }

        cases = [
            ("set", lambda: {**common, "provenance": {"values": {"a", "b"}}}),
            ("custom scalar", lambda: {**common, "provenance": {"value": object()}}),
            ("non-finite float", lambda: {**common, "provenance": {"score": float("nan")}}),
            ("non-string key", lambda: {**common, "provenance": {1: "invalid"}}),
        ]
        for label, make_evidence in cases:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    attach_verification(base, make_evidence(), "base-ref", "head-ref")

        cyclic = {"marker": "cycle"}
        cyclic["self"] = cyclic
        with self.assertRaises(ValueError):
            attach_verification(
                base,
                {**common, "provenance": cyclic},
                "base-ref",
                "head-ref",
            )

    def test_lifecycle_site_completeness_reflects_bounded_details(self) -> None:
        source = "\n".join("def start():\n    pass" for _ in range(129)) + "\n"
        lifecycle = analyze_verification({"app.py": source})["lifecycle"]

        self.assertEqual(lifecycle["metrics"]["recognized_site_count"], 129)
        self.assertEqual(len(lifecycle["findings"]), 128)
        self.assertFalse(lifecycle["details"]["sites_are_complete"])
        self.assertTrue(any("first 128" in item for item in lifecycle["limitations"]))

    def test_attach_rejects_mismatched_refs_and_boolean_only_pass(self) -> None:
        base = analyze_verification({})
        common = {
            "schema": VERIFICATION_SCHEMA,
            "base_sha": "base-ref",
            "head_sha": "head-ref",
            "factors": {
                "end_to_end": {"status": "passed", "passed": True},
            },
        }
        with self.assertRaises(ValueError):
            attach_verification(base, common, "other-base", "head-ref")
        with self.assertRaises(ValueError):
            attach_verification(base, common, "base-ref", "head-ref")

    def test_factor_revision_fields_must_match_the_evidence_envelope(self) -> None:
        factors = analyze_verification({})
        record = {"status": "not_measured", "reason": "not run", "base_sha": "base-ref", "head_sha": "head-ref"}
        evidence = {"schema": VERIFICATION_SCHEMA, "base_sha": "base-ref", "head_sha": "head-ref",
                    "factors": {"end_to_end": record}}
        self.assertEqual(attach_verification(factors, evidence, "base-ref", "head-ref")["end_to_end"]["status"], "not_measured")
        for key, bad_value in (("base_sha", "other"), ("head_sha", "other"), ("base_sha", 7), ("head_sha", 7)):
            with self.subTest(key=key, bad_value=bad_value):
                invalid = {**evidence, "factors": {"end_to_end": {**record, key: bad_value}}}
                with self.assertRaisesRegex(ValueError, key):
                    attach_verification(factors, invalid, "base-ref", "head-ref")

    def test_attach_rejects_contradictory_pass_and_api_only_ui_evidence(self) -> None:
        base = analyze_verification({})
        contradictory = {
            "schema": VERIFICATION_SCHEMA,
            "base_sha": "base-ref",
            "head_sha": "head-ref",
            "factors": {
                "end_to_end": {
                    "status": "passed",
                    "commands": ["pytest"],
                    "results": [{"returncode": 1, "stderr": "failure"}],
                },
            },
        }
        with self.assertRaises(ValueError):
            attach_verification(base, contradictory, "base-ref", "head-ref")

        api_only = {
            "schema": VERIFICATION_SCHEMA,
            "base_sha": "base-ref",
            "head_sha": "head-ref",
            "factors": {
                "ui_behavior": {
                    "status": "passed",
                    "commands": ["curl http://127.0.0.1/api/state"],
                    "results": [{"returncode": 0, "phase": "completed"}],
                    "expected": {"phase": "completed"},
                    "actual": {"phase": "completed"},
                },
            },
        }
        with self.assertRaises(ValueError):
            attach_verification(base, api_only, "base-ref", "head-ref")

    def test_attach_requires_reason_for_explicit_not_measured_records(self) -> None:
        """Consumer: explicit unmeasured evidence must explain the uncertainty."""
        base = analyze_verification({})
        envelope = {
            "schema": VERIFICATION_SCHEMA,
            "base_sha": "base-ref",
            "head_sha": "head-ref",
        }
        rejected = [
            {"end_to_end": {"status": "not_measured"}},
            {"end_to_end": {"status": "not_measured", "reason": "  "}},
            {"end_to_end": {"status": "not_measured", "limitation": ""}},
        ]
        for factors in rejected:
            with self.subTest(factors=factors):
                with self.assertRaises(ValueError):
                    attach_verification(
                        base,
                        {**envelope, "factors": factors},
                        "base-ref",
                        "head-ref",
                    )

        attached = attach_verification(
            base,
            {
                **envelope,
                "factors": {
                    "end_to_end": {
                        "status": "not_measured",
                        "limitation": "No subprocess was executed.",
                    },
                    "ui_behavior": {
                        "status": "not_measured",
                        "reason": "No browser interaction was executed.",
                    },
                },
            },
            "base-ref",
            "head-ref",
        )
        end_to_end = attached["end_to_end"]["verification"]
        ui_behavior = attached["ui_behavior"]["verification"]
        self.assertEqual(end_to_end["record"]["status"], "not_measured")
        self.assertEqual(end_to_end["record"]["limitation"], "No subprocess was executed.")
        self.assertEqual(ui_behavior["record"]["reason"], "No browser interaction was executed.")
        json.dumps(end_to_end, allow_nan=False)
        self.assertEqual(
            attached["lifecycle"]["verification"]["record"]["reason"],
            "No record was supplied for this factor.",
        )


if __name__ == "__main__":
    unittest.main()
