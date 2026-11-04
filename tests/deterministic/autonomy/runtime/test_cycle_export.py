"""Completed cycles reuse their serialized records across output consumers."""

from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.activation import STEPS
from autonomy.decision_cycle.cycle import DecisionCycle, DecisionSteps
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlApplication
from autonomy.runtime.recording import RunRecording
from autonomy.runtime.report import report_from_host_result
from tests.shared.cli.workbench_fixtures import image_source
from tests.shared.integration.plugin_flow_fixtures import frame
from tests.support.action_fixtures import fixed_action_runner, plan_runner, proposal_runner


def _completed_result(root):
    context = replace(frame(root, 1), metadata={"step_activations": {step: None for step in STEPS}})
    observation = Observation(context.frame_id, context.timestamp_ms, {}, metadata={"labels": ["first"]})
    memory = {"schema": "memory_report", "plugins": [{"plugin_id": "fixture", "state": {"count": 1}}]}
    result = DecisionCycle(DecisionSteps(
        observation=lambda context, perception: observation,
        memory=lambda context: memory,
        proposal=proposal_runner({}),
        plan=plan_runner(),
        action=fixed_action_runner(AutonomyControl()),
    )).run(context)
    return replace(result, application=ControlApplication(
        context.frame_id, "observe_only", False, "observe-only", result.control,
    ))


class CycleExportTests(unittest.TestCase):
    def test_recording_status_and_report_serialize_each_step_record_once(self):
        with image_source(1) as root:
            result = _completed_result(root)
            recording = RunRecording(root / "runs", host_name="fixture", run_id="run")
            with ExitStack() as stack:
                exports = [stack.enter_context(patch.object(
                    record_type, "to_dict", autospec=True, side_effect=record_type.to_dict,
                )) for record_type in (Observation, ProposalResult, ActionPlan, ActionResult)]

                # A live host records before the loop publishes status and its report.
                recording.append(result)
                status = json.loads(json.dumps(result.to_dict()))
                report = report_from_host_result(
                    result, host_name="fixture", run_id="run", generation_id="generation",
                ).to_dict()
                recorded = recording.read(run_id="run", after=0)["frames"][0]["frame"]
                for key in report["cycle"]:
                    self.assertEqual(status[key], recorded[key])
                    self.assertEqual(report["cycle"][key], recorded[key])
                for export in exports:
                    export.assert_called_once()

    def test_source_and_consumer_mutations_cannot_change_the_retained_export(self):
        with image_source(1) as root:
            result = _completed_result(root)
            exported = result.export_cycle()
            exported["memory"]["plugins"][0]["state"]["count"] = 10
            exported["observation"]["metadata"]["labels"].append("consumer")
            result.memory["plugins"][0]["state"]["count"] = 20
            result.observation.metadata["labels"].append("producer")

            retained = result.to_dict()
            self.assertEqual(retained["memory"]["plugins"][0]["state"]["count"], 1)
            self.assertEqual(retained["observation"]["metadata"]["labels"], ["first"])

            report = report_from_host_result(
                result, host_name="fixture", run_id="run", generation_id="generation",
            )
            report.cycle["memory"]["plugins"][0]["state"]["count"] = 30
            self.assertEqual(result.export_cycle()["memory"], retained["memory"])

    def test_replacing_a_step_record_starts_a_new_export(self):
        with image_source(1) as root:
            result = _completed_result(root)
            original = result.export_cycle()
            replacement = replace(result, memory={"schema": "memory_report", "plugins": []})

            self.assertEqual(replacement.export_cycle()["memory"], replacement.memory)
            self.assertEqual(result.export_cycle(), original)


if __name__ == "__main__":
    unittest.main()
