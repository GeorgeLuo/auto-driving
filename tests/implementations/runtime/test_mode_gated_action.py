"""Happy-path mode-gated action engine tests."""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.memory.snapshots.values import (
    MemoryBounds,
    MemoryProvenance,
    MemorySnapshot,
    RetainedEvidence,
    empty_memory_snapshot,
)
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.cycle import DecisionSteps
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from autonomy.decision_cycle.context import DecisionFrameContext
from implementations.runtime.engines.mode_gated_action import (
    ADAPTER_ENGINE_SPEC,
    ENGINE_ID,
    GATE_ID,
    ModeGatedActionEngine,
)
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.manager import AutonomyManager
from implementations.runtime.donkeycar import AutonomyPilotPart


def _memory(
    zone: str | None = None,
    *,
    frame_id: str = "frame-1",
    updated_at_ms: int = 1000,
) -> MemorySnapshot:
    bounds = MemoryBounds(max_records=16, max_age_ms=10_000)
    if zone is None:
        return empty_memory_snapshot(
            memory_id="memory-1",
            epoch_id="epoch-1",
            created_at_ms=updated_at_ms,
            bounds=bounds,
            implementation_id="bounded_evidence",
        )
    return MemorySnapshot(
        memory_id="memory-1",
        epoch_id="epoch-1",
        health="healthy",
        bounds=bounds,
        created_at_ms=updated_at_ms,
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
                    observed_at_ms=updated_at_ms,
                    updated_at_ms=updated_at_ms,
                    source_plugin_id="floor-plane-v0",
                    frame_id=frame_id,
                ),
                location=ViewLocation(
                    frame="image",
                    zone=zone,
                    bbox_xyxy_norm=(0.0, 0.4, 0.2, 0.8)
                    if zone == "left"
                    else (0.8, 0.4, 1.0, 0.8),
                ),
                properties={},
            ),
        ),
        implementation_id="bounded_evidence",
    )


def _act(*, zone: str | None, mode: str):
    return ModeGatedActionEngine().act(
        DecisionFrameContext(frame_id="frame-1", frame_index=1, timestamp_ms=1000, mode=mode),
        None,
        Observation(
            observation_id="obs-1",
            created_at_ms=1000,
            sensor_snapshot={},
            summary=("test",),
        ),
        _memory(zone),
    )


class ModeGatedActionTests(unittest.TestCase):
    def test_schema_is_explicitly_live_and_mode_gated(self) -> None:
        engine = ModeGatedActionEngine()
        schema = engine.describe_schema()

        self.assertEqual(schema["engine_id"], ENGINE_ID)
        self.assertEqual(schema["engine_spec"], ADAPTER_ENGINE_SPEC)
        self.assertEqual(schema["output"]["live_modes"], ["autonomy", "local"])
        self.assertEqual(schema["output"]["gate"], GATE_ID)

    def test_left_obstruction_drives_forward_and_away(self) -> None:
        action = _act(zone="left", mode="local")
        control = action.control

        self.assertGreater(control.steering, 0.0)
        self.assertAlmostEqual(control.throttle, 0.60)
        self.assertEqual(control.reason, "steer_away_left_obstruction")
        self.assertEqual(action.authority.gate_id, GATE_ID)
        self.assertTrue(action.authority.proposed_applied)
        self.assertTrue(action.authority.proposed_equals_authorized)

    def test_right_obstruction_drives_forward_and_away(self) -> None:
        action = _act(zone="right", mode="local")
        control = action.control

        self.assertLess(control.steering, 0.0)
        self.assertAlmostEqual(control.throttle, 0.60)
        self.assertEqual(control.reason, "steer_away_right_obstruction")
        self.assertTrue(action.authority.proposed_applied)

    def test_clear_path_is_idle(self) -> None:
        action = _act(zone=None, mode="local")
        control = action.control

        self.assertEqual(control.steering, 0.0)
        self.assertEqual(control.throttle, 0.0)
        self.assertEqual(control.reason, "no_lateral_obstruction")
        self.assertFalse(action.authority.proposed_applied)

    def test_manual_mode_is_idle_even_with_obstruction(self) -> None:
        action = _act(zone="left", mode="user")
        control = action.control

        self.assertEqual(control.steering, 0.0)
        self.assertEqual(control.throttle, 0.0)
        self.assertEqual(control.reason, "autonomy-mode-required")
        self.assertIsNotNone(action.authority.proposed)
        self.assertFalse(action.authority.proposed_applied)
        self.assertEqual(action.authority.drive_mode_gate, "user")

    def test_donkey_pilot_part_forwards_live_command_in_local_mode(self) -> None:
        manager = AutonomyManager(
            default_engine_spec=ADAPTER_ENGINE_SPEC,
            default_engine_config={},
        )

        def remember(context, observation):  # noqa: ANN001 - test step
            del observation
            return _memory(
                "left",
                frame_id=context.frame_id,
                updated_at_ms=context.timestamp_ms,
            )

        part = AutonomyPilotPart(
            host=AutonomyCycleHost(
                manager=manager,
                steps=DecisionSteps(remember=remember),
            ),
            min_interval_s=0.0,
        )

        part.run(
            image_array=object(),
            mode="local",
        )
        part.wait_for_cycle()
        steering, throttle, control, _engine, cycle = part.completed_outputs("local")

        self.assertGreater(steering, 0.0)
        self.assertAlmostEqual(throttle, 0.60)
        self.assertEqual(control["reason"], "steer_away_left_obstruction")
        self.assertTrue(cycle["action"]["authority"]["proposed_applied"])
        self.assertEqual(cycle["action"]["authority"]["gate_id"], GATE_ID)


if __name__ == "__main__":
    unittest.main(verbosity=2)
