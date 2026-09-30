"""Strict decoding of gate authority in decision stream frames."""

from __future__ import annotations

import copy
import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.snapshots.values import (
    MemoryBounds,
    MemoryProvenance,
    MemorySnapshot,
    RetainedEvidence,
)
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from cli.automa_cli.decision import (
    DecisionSurfaceError,
    ENGINE_ID,
    LIVE_ENGINE_ID,
    _require_runner_plan_alignment,
    _require_stream_frame_envelope,
    build_decision_stream_frame,
)
from implementations.runtime.engines.config import default_engine_config
from implementations.runtime.engines.mode_gated_action import ModeGatedActionEngine


def _left_obstruction() -> MemorySnapshot:
    return MemorySnapshot(
        memory_id="memory-1",
        epoch_id="epoch-1",
        health="healthy",
        bounds=MemoryBounds(max_records=16, max_age_ms=10_000),
        created_at_ms=1000,
        records=(
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
        ),
        implementation_id="bounded_evidence",
    )


def _applied_frame() -> dict:
    action = ModeGatedActionEngine().act(
        DecisionFrameContext(frame_id="frame-1", frame_index=1, timestamp_ms=1000, mode="local"),
        None,
        Observation(observation_id="obs-1", created_at_ms=1000, sensor_snapshot={}, summary=("t",)),
        _left_obstruction(),
    )
    assert action.authority.proposed_applied
    return build_decision_stream_frame(
        action,
        vehicle_id="chase-sim-chaser",
        run_id="run-1",
        worker_pid=1,
        activation_engine_id=LIVE_ENGINE_ID,
        activation_activated_at_ms=1,
        published_at_ms=1000,
        engine_id=LIVE_ENGINE_ID,
    )


class GateAuthorityDecodeTests(unittest.TestCase):
    def test_mode_gate_applied_frame_is_accepted(self) -> None:
        frame = _applied_frame()

        cycle = _require_stream_frame_envelope(frame)
        _require_runner_plan_alignment(
            cycle, default_engine_config(), engine_id=LIVE_ENGINE_ID
        )

        self.assertTrue(cycle.authority.proposed_applied)
        self.assertEqual(cycle.authority.gate_id, "mode")
        self.assertTrue(frame["authority_summary"]["proposed_applied"])
        self.assertEqual(frame["authority_summary"]["gate_id"], "mode")

    def test_mode_gate_frame_is_rejected_for_the_hold_engine(self) -> None:
        cycle = _require_stream_frame_envelope(_applied_frame())

        with self.assertRaises(DecisionSurfaceError) as ctx:
            _require_runner_plan_alignment(cycle, default_engine_config(), engine_id=ENGINE_ID)
        self.assertIn("'hold' gate", str(ctx.exception))

    def test_hold_gate_cannot_claim_an_applied_command(self) -> None:
        frame = copy.deepcopy(_applied_frame())
        frame["cycle"]["authority"]["gate_id"] = "hold"

        with self.assertRaises(DecisionSurfaceError) as ctx:
            _require_stream_frame_envelope(frame)
        self.assertIn("must be false for the hold gate", str(ctx.exception))

    def test_applied_output_must_equal_the_proposed_command(self) -> None:
        frame = copy.deepcopy(_applied_frame())
        frame["cycle"]["authority"]["authorized_output"]["steering"] = 0.1

        with self.assertRaises(DecisionSurfaceError) as ctx:
            _require_stream_frame_envelope(frame)
        self.assertIn("proposed_applied requires authorized output equal to proposed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
