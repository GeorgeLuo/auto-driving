from __future__ import annotations
import unittest
from copy import deepcopy
from autonomy.decision_cycle.action.hold import HOLD_IDLE_REASON
from autonomy.decision_cycle.activation import step_activation_from_payload
from cli.automa_cli.decision import (
    DecisionSurfaceError,
    accept_published_report,
    build_decision_stream_frame,
)
from cli.automa_cli.step_activations import decision_generation_id
from tests.cli.decision.decision_surfaces_fixtures import (
    DecisionSurfaceFixture,
    packaged_decision_steps,
    packaged_identity,
    vehicle_report_for_records,
)


def _identity(steps: dict) -> dict:
    activations = {
        step: None if payload is None else step_activation_from_payload(payload, step=step)
        for step, payload in steps.items()
    }
    return {"generation_id": decision_generation_id(activations), "steps": steps}


class DecisionSurfaceTests(DecisionSurfaceFixture, unittest.TestCase):
    def test_build_stream_frame_no_applied_control(self) -> None:
        cycle = self._sample_cycle()
        frame = build_decision_stream_frame(
            cycle,
            vehicle_id="chase-sim-chaser",
            run_id="run-1",
            worker_pid=12345,
            generation_id=packaged_identity()["generation_id"],
            published_at_ms=2000,
        )
        self.assertEqual(frame["schema"], "vehicle_decision_stream_frame_v0")
        self.assertNotIn("applied_control", frame)
        self.assertFalse(frame["authority_summary"]["proposed_applied"])
        self.assertEqual(
            frame["authority_summary"]["authorized_output"]["reason"],
            HOLD_IDLE_REASON,
        )
        self.assertIsNotNone(frame["authority_summary"]["proposed"])
        self.assertNotEqual(frame["authority_summary"]["proposed"]["steering"], 0.0)
        # selected candidate carries source_refs in plan_summary
        plan = frame["plan_summary"]
        self.assertEqual(plan["status"], "selected")
        selected = next(
            c
            for c in plan["candidates"]
            if c["proposal_id"] == plan["selected_proposal_id"]
        )
        self.assertTrue(selected["source_refs"])

    def test_stream_acceptance_production_predicate(self) -> None:
        records = self._sample_cycle()
        identity = packaged_identity()
        report = vehicle_report_for_records(
            records,
            vehicle_id="chase-sim-chaser",
            run_id="run-1",
            generation_id=identity["generation_id"],
            frame_id=records.frame_id,
            frame_index=1,
            timestamp_ms=1000,
            published_at_ms=5000,
            values={"worker_pid": 42},
        )
        state = {"run_id": "run-1", "status": "running", "pid": 42}

        accept_published_report(
            report,
            vehicle_id="chase-sim-chaser",
            activation=identity,
            automation_state=state,
            now_ms=6000,
            is_pid_alive=lambda pid: True,
        )

        # generation mismatch (restaged with a different proposal config)
        restaged = deepcopy(packaged_decision_steps())
        restaged["proposal"]["plugin_configs"]["avoid_recent_obstruction"]["steer_magnitude"] = 0.5
        with self.assertRaises(DecisionSurfaceError) as ctx:
            accept_published_report(
                report,
                vehicle_id="chase-sim-chaser",
                activation=_identity(restaged),
                automation_state=state,
                now_ms=6000,
                is_pid_alive=lambda pid: True,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_stale")
