"""ShadowProposalsAutonomyEngine adapter tests (M006-05)."""

from __future__ import annotations

import unittest

from autonomy.decision.memory import (
    MemoryBounds,
    MemoryProvenance,
    MemorySnapshot,
    RetainedEvidence,
)
from autonomy.decision.observation import Observation
from autonomy.decision.shadow_authority import AUTHORIZED_IDLE_REASON
from autonomy.perception import ViewLocation
from autonomy.runtime.engine import AutonomyControl, AutonomySnapshot
from autonomy.runtime.manager import AutonomyManager, EngineLoadError
from implementations.decision.shadow_adapter import (
    ADAPTER_ENGINE_SPEC,
    ENTRY_ERROR_REASON,
    ShadowProposalsAutonomyEngine,
)


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


def _snapshot(
    *,
    frame_id: str = "frame_001",
    frame_index: int = 1,
    timestamp_ms: int = 1000,
    memory: MemorySnapshot | None = None,
    observation: Observation | None = None,
) -> AutonomySnapshot:
    return AutonomySnapshot(
        observation=observation if observation is not None else _observation(),
        memory=memory if memory is not None else _memory(),
        cycle={"frame_id": frame_id, "frame_index": frame_index},
        mode="autonomy",
        timestamp_ms=timestamp_ms,
        metadata={},
    )


class ShadowAdapterTests(unittest.TestCase):
    def test_autonomy_manager_loads_adapter(self) -> None:
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
        self.assertIsInstance(manager.engine, ShadowProposalsAutonomyEngine)
        schema = manager.status()["engine_schema"]
        self.assertEqual(schema["engine_id"], "shadow-proposals")
        self.assertEqual(schema["engine_spec"], ADAPTER_ENGINE_SPEC)
        self.assertEqual(schema["stages"]["action"], "shadow_proposals_run_cycle")
        self.assertEqual(schema["output"]["movement"], "always idle")

    def test_bare_shadow_engine_is_not_activation_spec(self) -> None:
        with self.assertRaises(EngineLoadError):
            AutonomyManager(
                default_engine_spec="autonomy.decision.shadow_runner:ShadowProposalsEngine",
                default_engine_config={},
            )

    def test_step_success_idle_control_and_cycle_result(self) -> None:
        engine = ShadowProposalsAutonomyEngine()
        control = engine.step(_snapshot())
        self.assertIsInstance(control, AutonomyControl)
        self.assertEqual(control.steering, 0.0)
        self.assertEqual(control.throttle, 0.0)
        self.assertEqual(control.reason, AUTHORIZED_IDLE_REASON)
        self.assertIsNotNone(engine.last_cycle_result)
        assert engine.last_cycle_result is not None
        self.assertEqual(engine.last_cycle_result.frame_id, "frame_001")
        self.assertEqual(engine.last_cycle_result.status, "ok")
        self.assertFalse(engine.last_cycle_result.authority.proposed_applied)
        self.assertIsNotNone(engine.last_cycle_result.authority.proposed)
        assert engine.last_cycle_result.authority.proposed is not None
        self.assertNotEqual(engine.last_cycle_result.authority.proposed.steering, 0.0)

    def test_step_clears_last_cycle_result_on_entry_failure(self) -> None:
        engine = ShadowProposalsAutonomyEngine()
        engine.step(_snapshot(frame_id="frame_001"))
        self.assertIsNotNone(engine.last_cycle_result)
        control = engine.step(
            AutonomySnapshot(
                observation=_observation(),
                memory=_memory(),
                cycle={"frame_id": "bad frame!", "frame_index": 2},
                timestamp_ms=2000,
            )
        )
        self.assertEqual(control.reason, ENTRY_ERROR_REASON)
        self.assertIsNone(engine.last_cycle_result)

    def test_step_missing_frame_id_is_entry_error(self) -> None:
        engine = ShadowProposalsAutonomyEngine()
        control = engine.step(
            AutonomySnapshot(
                observation=_observation(),
                memory=_memory(),
                cycle={"frame_index": 1},
                timestamp_ms=1000,
            )
        )
        self.assertEqual(control.reason, ENTRY_ERROR_REASON)
        self.assertIsNone(engine.last_cycle_result)

    def test_reset_clears_last_cycle_result(self) -> None:
        engine = ShadowProposalsAutonomyEngine()
        engine.step(_snapshot())
        self.assertIsNotNone(engine.last_cycle_result)
        engine.reset()
        self.assertIsNone(engine.last_cycle_result)

    def test_invalid_config_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            ShadowProposalsAutonomyEngine(steer_magnitude=0.0)
        with self.assertRaises(ValueError):
            ShadowProposalsAutonomyEngine(enabled_plugins=[])

    def test_unavailable_memory_idle_plan(self) -> None:
        engine = ShadowProposalsAutonomyEngine()
        control = engine.step(
            AutonomySnapshot(
                observation=_observation(),
                memory=None,
                cycle={"frame_id": "frame_001", "frame_index": 1},
                timestamp_ms=1000,
            )
        )
        self.assertEqual(control.reason, AUTHORIZED_IDLE_REASON)
        assert engine.last_cycle_result is not None
        self.assertEqual(engine.last_cycle_result.status, "ok")
        assert engine.last_cycle_result.plan is not None
        self.assertIn(
            engine.last_cycle_result.plan.status,
            {"idle", "selected"},
        )
        # Without memory, avoid_recent_obstruction is missing_input / inactive.
        selected = engine.last_cycle_result.plan.selected_candidate()
        if selected is not None:
            self.assertIn(selected.lifecycle, {"missing_input", "inactive", "incompatible"})
        self.assertFalse(engine.last_cycle_result.authority.proposed_applied)


if __name__ == "__main__":
    unittest.main(verbosity=2)
