from __future__ import annotations

import copy
import unittest

from qca.factors.verification import (
    VERIFICATION_SCHEMA,
    analyze_verification,
    attach_verification,
)


class VerificationFactorTests(unittest.TestCase):
    def test_static_factors_report_assertion_candidates_and_lifecycle_sites(self) -> None:
        factors = analyze_verification(
            {
                "tests/test_example.py": """
def test_candidates(value):
    assert True
    assert value == value
    self.assertEqual(value, value)
    self.assertTrue(value)
""",
                "cli/runner.py": """
class Runner:
    def start(self):
        self.reset()

    def reset(self):
        self.cleanup()

    def cleanup(self):
        self.stop()

    def stop(self):
        return None

runner = Runner()
runner.start()
runner.reset()
""",
                "README.md": "assert True\n",
            }
        )

        test_metrics = factors["test_effectiveness"]["metrics"]
        self.assertEqual(factors["test_effectiveness"]["status"], "measured")
        self.assertEqual(test_metrics["assertion_count"], 4)
        self.assertEqual(test_metrics["literal_assertion_candidates"], 1)
        self.assertEqual(test_metrics["tautological_assertion_candidates"], 2)
        self.assertEqual(test_metrics["candidate_assertion_count"], 3)
        self.assertEqual(len(factors["test_effectiveness"]["findings"]), 3)
        self.assertTrue(all(item["message"] for item in factors["test_effectiveness"]["findings"]))
        self.assertTrue(
            any("candidate" in limitation.lower() for limitation in factors["test_effectiveness"]["limitations"])
        )

        lifecycle_metrics = factors["lifecycle"]["metrics"]
        lifecycle_details = factors["lifecycle"]["details"]
        self.assertEqual(lifecycle_metrics["start_definition_count"], 1)
        self.assertEqual(lifecycle_metrics["start_call_count"], 1)
        self.assertEqual(lifecycle_metrics["reset_definition_count"], 1)
        self.assertEqual(lifecycle_metrics["reset_call_count"], 2)
        self.assertEqual(lifecycle_metrics["cleanup_definition_count"], 1)
        self.assertEqual(lifecycle_metrics["stop_definition_count"], 1)
        self.assertEqual(lifecycle_details["by_kind"]["start"]["definitions"], 1)
        self.assertTrue(all(item["message"] for item in factors["lifecycle"]["findings"]))
        self.assertTrue(
            any("symmetry" in limitation.lower() for limitation in factors["lifecycle"]["limitations"])
        )

    def test_dynamic_factors_are_explicitly_unmeasured_without_evidence(self) -> None:
        factors = analyze_verification({})
        for name in ("end_to_end", "ui_behavior"):
            self.assertEqual(factors[name]["status"], "not_measured")
            self.assertEqual(factors[name]["metrics"], {})
            self.assertEqual(factors[name]["findings"], [])
            self.assertTrue(factors[name]["limitations"])

    def test_candidate_site_details_are_complete_and_counts_are_retained(self) -> None:
        source = "\n".join("assert True" for _ in range(80)) + "\n"
        factors = analyze_verification({"tests/test_many.py": source})
        metrics = factors["test_effectiveness"]["metrics"]
        self.assertEqual(metrics["literal_assertion_candidates"], 80)
        self.assertEqual(len(factors["test_effectiveness"]["findings"]), 80)
        self.assertEqual(factors["test_effectiveness"]["details"]["candidate_site_limit"], 64)

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
                    "commands": ["python -m pytest tests/cli/test_workbench.py"],
                    "results": [{"returncode": 0, "tests": 30}],
                    "expected": {"phase": "completed"},
                    "actual": {"phase": "completed"},
                },
                "ui_behavior": {
                    "status": "failed",
                    "commands": ["python workbench_probe.py"],
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

    def test_attach_treats_omitted_records_as_not_measured(self) -> None:
        attached = attach_verification(
            analyze_verification({}),
            {
                "schema": VERIFICATION_SCHEMA,
                "base_sha": "base-ref",
                "head_sha": "head-ref",
                "factors": {},
            },
            "base-ref",
            "head-ref",
        )
        for name in ("test_effectiveness", "end_to_end", "ui_behavior", "lifecycle"):
            self.assertEqual(attached[name]["verification"]["status"], "not_measured")
        self.assertEqual(attached["end_to_end"]["status"], "not_measured")


if __name__ == "__main__":
    unittest.main()
