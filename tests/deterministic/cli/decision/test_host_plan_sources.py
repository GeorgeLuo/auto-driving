"""Uploaded plan sources the CLI reads from the host whose plans it checks."""

from __future__ import annotations

import hashlib
import unittest
import uuid

from autonomy.decision_cycle.plan.runner import PlanRunner
from autonomy.plugins import PluginManagementError, uploaded_source
from cli.automa_cli.decision_records import activations_from_payloads
from cli.automa_cli.host_sources import UploadedSourceError, provide_plan_sources
from cli.automa_cli.paths import ROOT
from tests.shared.cli.decision.decision_surfaces_fixtures import packaged_decision_steps

HIGHEST_CONFIDENCE = ROOT / "implementations" / "decision_cycle" / "plan" / "plugins" / "highest_confidence" / "plugin.py"


def _uploaded_plan() -> tuple[dict, str, bytes]:
    """Decision steps whose plan is an uploaded source this process never saw."""

    source = HIGHEST_CONFIDENCE.read_bytes() + f"# {uuid.uuid4().hex}\n".encode()
    name = f"{hashlib.sha256(source).hexdigest()}.py"
    steps = packaged_decision_steps()
    steps["plan"]["plugin_specs"] = {"highest_confidence": f"{name}:HighestConfidencePlan"}
    return steps, name, source


class HostPlanSourceTests(unittest.TestCase):
    def test_a_missing_plan_source_is_read_verified_and_then_runs_here(self) -> None:
        steps, name, source = _uploaded_plan()
        with self.assertRaisesRegex(PluginManagementError, "not available here"):
            PlanRunner.from_activation(activations_from_payloads(steps)["plan"])
        reads = []
        provide_plan_sources(steps["plan"], lambda requested: reads.append(requested) or source)
        self.assertEqual(reads, [name])
        self.assertEqual(uploaded_source(name).read_bytes(), source)
        PlanRunner.from_activation(activations_from_payloads(steps)["plan"])
        # A source already here is not read again.
        provide_plan_sources(steps["plan"], lambda requested: self.fail(f"read {requested} again"))

    def test_other_content_or_a_failed_read_is_refused_with_the_source_name(self) -> None:
        steps, name, source = _uploaded_plan()

        def unavailable(requested: str) -> bytes:
            raise ValueError("operation not supported")

        for load, message in (
            (lambda requested: source + b"# changed\n", "with other content"),
            (unavailable, "did not serve it: operation not supported"),
        ):
            with self.subTest(message=message):
                with self.assertRaises(UploadedSourceError) as raised:
                    provide_plan_sources(steps["plan"], load)
                self.assertEqual(raised.exception.name, name)
                self.assertIn(message, str(raised.exception))
                with self.assertRaises(PluginManagementError):
                    uploaded_source(name)

    def test_packaged_or_malformed_plans_read_nothing(self) -> None:
        def unexpected(requested: str) -> bytes:
            raise AssertionError(f"read {requested}")

        for plan in (packaged_decision_steps()["plan"], None, {"plugin_specs": None},
                     {"plugin_specs": {"highest_confidence": 7}}, "plan"):
            with self.subTest(plan=plan):
                provide_plan_sources(plan, unexpected)


if __name__ == "__main__":
    unittest.main()
