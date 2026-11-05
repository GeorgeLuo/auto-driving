"""Completed cycles reuse their serialized records across output consumers."""

from __future__ import annotations

import copy
import json
import pickle
import threading
import unittest
from contextlib import ExitStack
from dataclasses import asdict, replace
from unittest.mock import patch

from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.activation import STEPS
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionCycle, DecisionCycleResult, DecisionSteps
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.plan.values import ActionPlan, PlanContribution
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.proposal.values import (
    ActionProposal,
    ProposedVehicleCommand,
    SourceRef,
)
from autonomy.decision_cycle.records import DecisionRecords
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlApplication
from autonomy.runtime.recording import RunRecording
from autonomy.runtime.report import report_from_host_result
from autonomy.serialization import FrozenJsonObject
from tests.shared.cli.workbench_fixtures import image_source
from tests.shared.integration.plugin_flow_fixtures import frame
from tests.support.action_fixtures import fixed_action_runner, plan_runner, proposal_runner


def _live_parts(root):
    context = replace(frame(root, 1), metadata={"step_activations": {step: None for step in STEPS}})
    observation = Observation(context.frame_id, context.timestamp_ms, {}, metadata={"labels": ["first"]})
    memory = {"schema": "memory_report", "plugins": [{"plugin_id": "fixture", "state": {"count": 1}}]}
    return context, observation, memory


def _finish_like_host(context, observation, memory, *, proposal=None, plan=None):
    """Run, then replace context and application, as ``AutonomyCycleHost.run`` does."""

    result = DecisionCycle(DecisionSteps(
        observation=lambda context, perception: observation,
        memory=lambda context: memory,
        proposal=proposal or proposal_runner({}),
        plan=plan or plan_runner(),
        action=fixed_action_runner(AutonomyControl()),
    )).run(context)
    result = replace(
        result,
        context=replace(result.context, metadata={**dict(result.context.metadata), "recorded": True}),
    )
    return replace(result, application=ControlApplication(
        context.frame_id, "observe_only", False, "observe-only", result.control,
    ))


def _completed_result(root):
    return _finish_like_host(*_live_parts(root))


def _populated_result() -> DecisionCycleResult:
    """A real cycle whose proposal and plan keep nested frozen metadata."""

    context = DecisionFrameContext(
        "frame_001", 1, 1_000, metadata={"step_activations": {step: None for step in STEPS}},
    )
    proposal_metadata = {"labels": ["left"], "nested": {"b": [1, 2]}}
    plan_metadata = {"trace": {"nodes": ["a"], "empty": []}}

    def propose(context):
        return ProposalResult(
            frame_id=context.frame_id,
            status="ok",
            candidates=(ActionProposal(
                plugin_id="lane",
                frame_id=context.frame_id,
                lifecycle="fresh",
                freshness="fresh",
                confidence=0.5,
                reason="clear",
                command=ProposedVehicleCommand(steering=0.1, throttle=0.0),
                assumptions=("image_relative_only",),
                source_refs=(SourceRef(kind="memory_record", id="r1", frame_id=context.frame_id),),
                available=True,
                metadata=proposal_metadata,
            ),),
        )

    def plan(context, proposal):
        candidate = proposal.candidates[0]
        return ActionPlan(
            frame_id=context.frame_id,
            timestamp_ms=context.timestamp_ms,
            status="selected",
            candidates=proposal.candidates,
            selector_id="selector",
            selected_proposal_id=candidate.proposal_id,
            contributions=(PlanContribution(
                proposal_id=candidate.proposal_id,
                plugin_id=candidate.plugin_id,
                weight=1.0,
                role="selected",
            ),),
            command=candidate.command,
            reason=candidate.reason,
            confidence=candidate.confidence,
            metadata=plan_metadata,
        )

    observation = Observation(context.frame_id, context.timestamp_ms, {}, metadata={"labels": ["lane"]})
    memory = {"schema": "memory_report", "plugins": [{"plugin_id": "fixture", "state": {"count": 1}}]}
    result = _finish_like_host(context, observation, memory, proposal=propose, plan=plan)
    assert isinstance(result.proposal.candidates[0].metadata, FrozenJsonObject)
    assert isinstance(result.plan.metadata, FrozenJsonObject)
    return result


class CycleExportTests(unittest.TestCase):
    def test_recording_status_and_report_serialize_each_step_record_once(self):
        with image_source(1) as root:
            context, observation, memory = _live_parts(root)
            recording = RunRecording(root / "runs", host_name="fixture", run_id="run")
            with ExitStack() as stack:
                exports = [stack.enter_context(patch.object(
                    record_type, "to_dict", autospec=True, side_effect=record_type.to_dict,
                )) for record_type in (Observation, ProposalResult, ActionPlan, ActionResult)]

                # The host replaces context and application before any consumer exports.
                result = _finish_like_host(context, observation, memory)
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

    def test_populated_result_copies_pickles_and_keeps_one_export(self):
        result = _populated_result()
        self.assertIsNotNone(DecisionRecords.from_cycle(result))
        exported = result.export_cycle()
        self.assertEqual(exported["proposal"]["candidates"][0]["metadata"]["nested"]["b"], [1, 2])
        self.assertEqual(exported["plan"]["metadata"]["trace"]["empty"], [])

        copied = copy.copy(result)
        cloned = copy.deepcopy(result)
        restored = pickle.loads(pickle.dumps(result))
        plain = asdict(result)
        for duplicate in (copied, cloned, restored):
            self.assertEqual(duplicate.export_cycle(), exported)
            self.assertIsNot(duplicate.export_cycle(), exported)
        self.assertEqual(plain["proposal"]["candidates"][0]["metadata"]["labels"], ("left",))
        self.assertEqual(plain["plan"]["metadata"]["trace"]["empty"], ())
        self.assertIsInstance(plain["_cycle_export"], dict)
        self.assertEqual(plain["_cycle_export"]["plan"]["metadata"]["trace"]["nodes"], ["a"])

        exported["plan"]["metadata"]["trace"]["nodes"].append("mutated")
        self.assertEqual(result.export_cycle()["plan"]["metadata"]["trace"]["nodes"], ["a"])
        self.assertEqual(restored.export_cycle()["plan"]["metadata"]["trace"]["nodes"], ["a"])

    def test_concurrent_exports_share_one_serialization(self):
        result = _populated_result()
        with ExitStack() as stack:
            exports = [stack.enter_context(patch.object(
                record_type, "to_dict", autospec=True, side_effect=record_type.to_dict,
            )) for record_type in (Observation, ProposalResult, ActionPlan, ActionResult)]
            barrier = threading.Barrier(8)
            found: list[dict] = []
            errors: list[BaseException] = []

            def grab() -> None:
                try:
                    barrier.wait()
                    found.append(result.export_cycle())
                except BaseException as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=grab) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(found), 8)
            for item in found[1:]:
                self.assertEqual(item, found[0])
                self.assertIsNot(item, found[0])
            found[0]["memory"]["plugins"][0]["state"]["count"] = 99
            self.assertEqual(result.export_cycle()["memory"]["plugins"][0]["state"]["count"], 1)
            for export in exports:
                export.assert_called_once()


if __name__ == "__main__":
    unittest.main()
