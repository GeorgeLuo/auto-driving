"""Strict decoding of action authority in a published cycle."""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.observation.publication import OBSERVATION_KEY
from autonomy.decision_cycle.memory.evidence import MemoryOrigin, RetainedEvidence
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from cli.automa_cli.hosts.publications.decision import (
    DecisionSurfaceError,
    _require_exact_cycle_export,
    _require_runner_plan_alignment,
)
from cli.automa_cli.hosts.publications.decision_records import DecisionRunners
from autonomy.decision_cycle.action.publication import MODE_KEY
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from tests.shared.cli.decision.decision_surfaces_fixtures import packaged_decision_steps


def _left_obstruction() -> tuple[RetainedEvidence, ...]:
    return (
        RetainedEvidence(
            record_id="thing:1:boundary",
            kind="floor_boundary",
            label="floor boundary",
            confidence=0.8,
            origin=MemoryOrigin(
                observation_id="obs-1",
                observed_id="boundary",
                coordinate_frame="image",
                observed_at_ms=1000,
                updated_at_ms=1000,
                source_plugin_id="floor_plane",
                frame_id="frame-1",
            ),
            location=ViewLocation(
                frame="image", zone="left", bbox_xyxy_norm=(0.0, 0.4, 0.2, 0.8)
            ),
            properties={},
        ),
    )


def _applied_cycle() -> dict:
    records = DecisionRunners.from_payloads(packaged_decision_steps("mode")).run(
        frame_id="frame-1",
        frame_index=1,
        timestamp_ms=1000,
        shared_memory={
            MODE_KEY: "local",
            OBSERVATION_KEY: Observation(
                observation_id="obs-1", created_at_ms=1000, sensor_frame={}, summary=("t",)
            ),
            EVIDENCE_KEY: _left_obstruction(),
        },
    )
    assert records.authority.proposed_applied
    return records.to_dict()


class ActionAuthorityDecodeTests(unittest.TestCase):
    def test_authority_rejects_a_non_string_drive_mode_gate(self) -> None:
        payload = _applied_cycle()
        payload["action"]["authority"]["drive_mode_gate"] = 7
        with self.assertRaisesRegex(DecisionSurfaceError, "drive_mode_gate"):
            _require_exact_cycle_export(payload)

    def test_mode_action_applied_cycle_is_accepted(self) -> None:
        cycle = _require_exact_cycle_export(_applied_cycle())
        _require_runner_plan_alignment(cycle, packaged_decision_steps("mode"))

        self.assertTrue(cycle.authority.proposed_applied)
        self.assertEqual(cycle.authority.gate_id, "mode")

    def test_the_plan_is_accepted_as_the_staged_plugin_chose_it(self) -> None:
        # The plan plugin read shared memory that acceptance cannot see, so it
        # is not run again here.
        payload = _applied_cycle()
        payload["plan"]["metadata"] = {"observation_id": "obs-1"}
        cycle = _require_exact_cycle_export(payload)
        _require_runner_plan_alignment(cycle, packaged_decision_steps("mode"))
        self.assertEqual(cycle.plan.metadata, {"observation_id": "obs-1"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
