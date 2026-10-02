"""The packaged proposal, the built-in plan, and the hold and mode actions, run as cycle steps."""

from __future__ import annotations

import unittest
from dataclasses import replace

from autonomy.decision_cycle.action.hold import HOLD_IDLE_REASON
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionCycle, DecisionCycleResult
from autonomy.decision_cycle.memory.evidence import MemoryProvenance, RetainedEvidence
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from implementations.decision_cycle.action.mode.plugin import LIVE_MODES
from implementations.decision_cycle.catalog import packaged_activation, step_plugins
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from implementations.runtime.donkeycar import AutonomyPilotPart


def _records(
    zone: str | None,
    *,
    frame_id: str = "frame-1",
    updated_at_ms: int = 1000,
) -> tuple[RetainedEvidence, ...]:
    if zone is None:
        return ()
    return (
        RetainedEvidence(
            record_id="thing:1:boundary",
            kind="floor_boundary",
            label="floor boundary",
            confidence=0.8,
            provenance=MemoryProvenance(
                observation_id="obs-1",
                observed_id="boundary",
                coordinate_frame="image",
                observed_at_ms=updated_at_ms,
                updated_at_ms=updated_at_ms,
                source_plugin_id="floor_plane",
                frame_id=frame_id,
            ),
            location=ViewLocation(
                frame="image",
                zone=zone,
                bbox_xyxy_norm=(0.0, 0.4, 0.2, 0.8) if zone == "left" else (0.8, 0.4, 1.0, 0.8),
            ),
            properties={},
        ),
    )


def _observation(**overrides) -> Observation:
    values = {
        "observation_id": "obs-1",
        "created_at_ms": 1000,
        "sensor_snapshot": {},
        "summary": ("test",),
    }
    values.update(overrides)
    return Observation(**values)


def _steps(action: str, **activation_overrides):
    return replace(
        decision_steps(
            {
                "proposal": packaged_activation("proposal", **activation_overrides),
                "action": packaged_activation("action", [action]),
            }
        ),
        observation=lambda context, perception: _observation(),
    )


def _run(
    action: str,
    *,
    zone: str | None,
    mode: str,
    frame_id: str = "frame-1",
    observation: Observation | None = None,
    **activation_overrides,
) -> DecisionCycleResult:
    steps = _steps(action, **activation_overrides)
    if observation is not None:
        steps = replace(steps, observation=lambda context, perception: observation)
    return DecisionCycle(steps).run(
        DecisionFrameContext(
            frame_id=frame_id,
            frame_index=1,
            timestamp_ms=1000,
            mode=mode,
            shared_memory={EVIDENCE_KEY: _records(zone)},
        )
    )


class PackagedCatalogTests(unittest.TestCase):
    def test_action_catalog_offers_hold_and_mode(self) -> None:
        self.assertEqual(sorted(step_plugins("action")), ["hold", "mode"])
        self.assertEqual(packaged_activation("action").plugins, ("hold",))
        self.assertEqual(packaged_activation("proposal").plugins, ("avoid_recent_obstruction",))


class HoldActionTests(unittest.TestCase):
    def test_nonzero_proposal_is_held_idle(self) -> None:
        result = _run("hold", zone="left", mode="autonomy", frame_id="frame_001")
        action = result.action
        self.assertEqual(result.proposal.status, "ok")
        self.assertEqual(result.plan.status, "selected")
        self.assertEqual(action.control.steering, 0.0)
        self.assertEqual(action.control.throttle, 0.0)
        self.assertEqual(action.control.reason, HOLD_IDLE_REASON)
        self.assertEqual(action.frame_id, "frame_001")
        self.assertEqual(action.status, "ok")
        self.assertEqual(action.authority.gate_id, "hold")
        self.assertFalse(action.authority.proposed_applied)
        assert action.authority.proposed is not None
        self.assertNotEqual(action.authority.proposed.steering, 0.0)
        self.assertEqual(action.authority.drive_mode_gate, "autonomy")

    def test_evaluator_metadata_is_stripped_from_a_live_capture(self) -> None:
        observation = _observation(
            observation_id="obs_live",
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
        )
        result = _run("hold", zone="left", mode="autonomy", observation=observation)
        self.assertEqual(result.proposal.status, "ok")
        self.assertEqual(result.action.status, "ok")
        self.assertEqual(result.control.reason, HOLD_IDLE_REASON)

    def test_empty_proposal_selection_holds_idle(self) -> None:
        result = _run("hold", zone="left", mode="autonomy", plugins=[])
        self.assertEqual(result.proposal.candidates, ())
        self.assertEqual(result.plan.status, "idle")
        self.assertEqual(result.action.status, "ok")
        self.assertEqual(result.control.reason, HOLD_IDLE_REASON)

    def test_unavailable_memory_plans_idle(self) -> None:
        result = _run("hold", zone=None, mode="autonomy")
        self.assertEqual(result.plan.status, "idle")
        self.assertEqual(result.control.reason, HOLD_IDLE_REASON)
        self.assertFalse(result.action.authority.proposed_applied)


class ModeActionTests(unittest.TestCase):
    def test_live_modes(self) -> None:
        self.assertEqual(sorted(LIVE_MODES), ["autonomy", "local"])

    def test_left_obstruction_drives_forward_and_away(self) -> None:
        action = _run("mode", zone="left", mode="local").action
        self.assertGreater(action.control.steering, 0.0)
        self.assertAlmostEqual(action.control.throttle, 0.60)
        self.assertEqual(action.control.reason, "steer_away_left_obstruction")
        self.assertEqual(action.authority.gate_id, "mode")
        self.assertTrue(action.authority.proposed_applied)
        self.assertTrue(action.authority.proposed_equals_authorized)

    def test_right_obstruction_drives_forward_and_away(self) -> None:
        action = _run("mode", zone="right", mode="local").action
        self.assertLess(action.control.steering, 0.0)
        self.assertAlmostEqual(action.control.throttle, 0.60)
        self.assertEqual(action.control.reason, "steer_away_right_obstruction")
        self.assertTrue(action.authority.proposed_applied)

    def test_clear_path_is_idle(self) -> None:
        action = _run("mode", zone=None, mode="local").action
        self.assertEqual(action.control.steering, 0.0)
        self.assertEqual(action.control.throttle, 0.0)
        self.assertEqual(action.control.reason, "no_selected_command")
        self.assertFalse(action.authority.proposed_applied)

    def test_manual_mode_is_idle_even_with_obstruction(self) -> None:
        action = _run("mode", zone="left", mode="user").action
        self.assertEqual(action.control.steering, 0.0)
        self.assertEqual(action.control.throttle, 0.0)
        self.assertEqual(action.control.reason, "autonomy-mode-required")
        self.assertIsNotNone(action.authority.proposed)
        self.assertFalse(action.authority.proposed_applied)
        self.assertEqual(action.authority.drive_mode_gate, "user")

    def test_donkey_pilot_part_forwards_live_command_in_local_mode(self) -> None:
        def remember(context, observation):  # noqa: ANN001 - test step
            del observation
            context.shared_memory[EVIDENCE_KEY] = _records(
                "left", frame_id=context.frame_id, updated_at_ms=context.timestamp_ms
            )
            return {"schema": "memory_report_v0", "plugins": []}

        part = AutonomyPilotPart(
            host=AutonomyCycleHost(steps=replace(_steps("mode"), memory=remember)),
            min_interval_s=0.0,
        )
        part.run(image_array=object(), mode="local")
        part.wait_for_cycle()
        steering, throttle, control, _generation, cycle = part.completed_outputs("local")

        self.assertGreater(steering, 0.0)
        self.assertAlmostEqual(throttle, 0.60)
        self.assertEqual(control["reason"], "steer_away_left_obstruction")
        self.assertTrue(cycle["action"]["authority"]["proposed_applied"])
        self.assertEqual(cycle["action"]["authority"]["gate_id"], "mode")


if __name__ == "__main__":
    unittest.main(verbosity=2)
