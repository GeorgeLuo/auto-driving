"""Strict decoding of action authority in decision stream frames."""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.memory.evidence import MemoryProvenance, RetainedEvidence
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from cli.automa_cli.decision import (
    _require_runner_plan_alignment,
    _require_stream_frame_envelope,
    build_decision_stream_frame,
)
from cli.automa_cli.decision_records import DecisionRunners
from implementations.decision_cycle.memory.bounded_evidence.ledger import EVIDENCE_KEY
from tests.cli.decision.decision_surfaces_fixtures import packaged_decision_steps, packaged_identity


def _left_obstruction() -> tuple[RetainedEvidence, ...]:
    return (
        RetainedEvidence(
            record_id="thing:1:boundary",
            kind="floor_boundary",
            label="floor boundary",
            confidence=0.8,
            provenance=MemoryProvenance(
                observation_id="obs-1",
                evidence_id="boundary",
                coordinate_frame="image",
                observed_at_ms=1000,
                updated_at_ms=1000,
                source_plugin_id="floor-plane-v0",
                frame_id="frame-1",
            ),
            location=ViewLocation(
                frame="image", zone="left", bbox_xyxy_norm=(0.0, 0.4, 0.2, 0.8)
            ),
            properties={},
        ),
    )


def _applied_frame() -> dict:
    records = DecisionRunners.from_payloads(packaged_decision_steps("mode")).run(
        frame_id="frame-1",
        frame_index=1,
        timestamp_ms=1000,
        mode="local",
        observation=Observation(
            observation_id="obs-1", created_at_ms=1000, sensor_snapshot={}, summary=("t",)
        ),
        shared_memory={EVIDENCE_KEY: _left_obstruction()},
    )
    assert records.authority.proposed_applied
    return build_decision_stream_frame(
        records,
        vehicle_id="chase-sim-chaser",
        run_id="run-1",
        worker_pid=1,
        generation_id=packaged_identity("mode")["generation_id"],
        published_at_ms=1000,
    )


class ActionAuthorityDecodeTests(unittest.TestCase):
    def test_mode_action_applied_frame_is_accepted(self) -> None:
        frame = _applied_frame()

        cycle = _require_stream_frame_envelope(frame)
        _require_runner_plan_alignment(cycle, packaged_decision_steps("mode"))

        self.assertTrue(cycle.authority.proposed_applied)
        self.assertEqual(cycle.authority.gate_id, "mode")
        self.assertTrue(frame["authority_summary"]["proposed_applied"])
        self.assertEqual(frame["authority_summary"]["gate_id"], "mode")


if __name__ == "__main__":
    unittest.main(verbosity=2)
