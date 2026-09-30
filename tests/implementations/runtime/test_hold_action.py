"""HoldActionEngine tests."""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.memory.snapshots.values import (
    MemoryBounds,
    MemoryProvenance,
    MemorySnapshot,
    RetainedEvidence,
)
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.action_gate.hold import HOLD_IDLE_REASON
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from autonomy.decision_cycle.context import DecisionFrameContext
from implementations.runtime.engines.hold_action import (
    ADAPTER_ENGINE_SPEC,
    ENGINE_ID,
    HoldActionEngine,
)
from autonomy.runtime.engine import AutonomyControl
from autonomy.runtime.manager import AutonomyManager, EngineLoadError


def _observation() -> Observation:
    return Observation(
        observation_id="obs_001",
        created_at_ms=1000,
        sensor_snapshot={},
        summary=("line",),
    )


def _memory(zone: str = "left") -> MemorySnapshot:
    return MemorySnapshot(
        memory_id="m1",
        epoch_id="e1",
        health="healthy",
        bounds=MemoryBounds(max_records=16, max_age_ms=10_000),
        created_at_ms=1000,
        records=(
            RetainedEvidence(
                record_id="thing:1:a",
                kind="floor_boundary",
                label="floor_boundary",
                confidence=0.8,
                provenance=MemoryProvenance(
                    observation_id="obs_001",
                    evidence_id="ev_001",
                    coordinate_frame="image",
                    observed_at_ms=1000,
                    updated_at_ms=1000,
                    source_plugin_id="src",
                    frame_id="frame_001",
                ),
                location=ViewLocation(
                    frame="image",
                    zone=zone,
                    bbox_xyxy_norm=(0.0, 0.0, 0.2, 0.5),
                ),
                properties={},
            ),
        ),
        implementation_id="bounded_evidence",
    )


def _context(frame_id: str = "frame_001") -> DecisionFrameContext:
    return DecisionFrameContext(
        frame_id=frame_id,
        frame_index=1,
        timestamp_ms=1000,
        mode="autonomy",
    )


class HoldActionTests(unittest.TestCase):
    def test_autonomy_manager_loads_engine(self) -> None:
        manager = AutonomyManager(
            default_engine_spec=ADAPTER_ENGINE_SPEC,
            default_engine_config={
                "enabled_plugins": ["avoid_recent_obstruction"],
                "accepted_kinds": [
                    "floor_boundary",
                    "obstacle",
                    "obstruction_evidence",
                ],
                "retained_max_age_ms": 1000,
                "steer_magnitude": 0.35,
            },
        )
        self.assertIsInstance(manager.engine, HoldActionEngine)
        schema = manager.status()["engine_schema"]
        self.assertEqual(schema["engine_id"], ENGINE_ID)
        self.assertEqual(schema["engine_spec"], ADAPTER_ENGINE_SPEC)
        self.assertEqual(schema["steps"]["action"], "propose_plan_hold")
        self.assertEqual(schema["output"]["movement"], "always idle")
        self.assertEqual(schema["output"]["gate"], "hold")

    def test_bare_composition_is_not_activation_spec(self) -> None:
        with self.assertRaises(EngineLoadError):
            AutonomyManager(
                default_engine_spec="autonomy.decision_cycle.action:ActionComposition",
                default_engine_config={},
            )

    def test_act_strips_evaluator_metadata_from_live_capture(self) -> None:
        observation = Observation(
            observation_id="obs_live",
            created_at_ms=1000,
            sensor_snapshot={
                "readings": {
                    "front_camera": {
                        "metadata": {
                            "content_type": "image/png",
                            "evaluator_reference": {"status": "available"},
                        }
                    }
                }
            },
            summary=("line",),
        )
        action = HoldActionEngine().act(_context(), None, observation, _memory())
        self.assertEqual(action.control.reason, HOLD_IDLE_REASON)
        self.assertEqual(action.status, "ok")
        self.assertNotEqual(action.reason, "decision_data_source_invalid")

    def test_nonzero_proposal_is_held_idle(self) -> None:
        action = HoldActionEngine().act(_context(), None, _observation(), _memory())
        self.assertIsInstance(action.control, AutonomyControl)
        self.assertEqual(action.control.steering, 0.0)
        self.assertEqual(action.control.throttle, 0.0)
        self.assertEqual(action.control.reason, HOLD_IDLE_REASON)
        self.assertEqual(action.frame_id, "frame_001")
        self.assertEqual(action.status, "ok")
        self.assertEqual(action.authority.gate_id, "hold")
        self.assertFalse(action.authority.proposed_applied)
        self.assertIsNotNone(action.authority.proposed)
        assert action.authority.proposed is not None
        self.assertNotEqual(action.authority.proposed.steering, 0.0)
        self.assertEqual(action.authority.drive_mode_gate, "autonomy")

    def test_invalid_frame_identity_is_recorded_and_holds_idle(self) -> None:
        manager = AutonomyManager(default_engine_spec=ADAPTER_ENGINE_SPEC)

        self.assertIsNone(manager.act(_context("bad frame!"), None, _observation(), _memory()))
        self.assertIn("ActionInputError", manager.last_error or "")
        self.assertEqual(manager.error_count, 1)

    def test_invalid_config_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            HoldActionEngine(steer_magnitude=0.0)
        with self.assertRaises(ValueError):
            HoldActionEngine(enabled_plugins=[])

    def test_unavailable_memory_idle_plan(self) -> None:
        action = HoldActionEngine().act(_context(), None, _observation(), None)
        self.assertEqual(action.control.reason, HOLD_IDLE_REASON)
        self.assertEqual(action.status, "ok")
        assert action.plan is not None
        self.assertIn(action.plan.status, {"idle", "selected"})
        # Without memory, avoid_recent_obstruction is missing_input / inactive.
        selected = action.plan.selected_candidate()
        if selected is not None:
            self.assertIn(selected.lifecycle, {"missing_input", "inactive", "incompatible"})
        self.assertFalse(action.authority.proposed_applied)


if __name__ == "__main__":
    unittest.main(verbosity=2)
