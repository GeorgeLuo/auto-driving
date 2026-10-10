"""One workbench frame through the decision cycle, without runner state.

The runner owns phases, history, and locking; this module only turns a
replay frame and the active steps into a cycle result.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionCycle, DecisionSteps
from autonomy.decision_cycle.observation.perception_summary import observation_from_perception
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.interface import PerceptionText
from autonomy.decision_cycle.steps import decision_steps
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading

from .perception_runs import run_perception
from .step_hosting import plugin_report
from .workbench_contract import WORKBENCH_SEQUENCE_ID
from .workbench_source import ReplayFrame


def sensor_frame_for_replay_frame(frame: ReplayFrame) -> SensorFrame | None:
    if frame.absent or frame.image_path is None:
        return None
    reading = SensorReading(
        sensor_id=FRONT_CAMERA_SENSOR_ID,
        sensor_kind="camera",
        captured_at_ms=frame.timestamp_ms,
        path=str(frame.image_path),
        metadata={
            "source_id": frame.source_id,
            "frame_id": frame.frame_id,
            "sequence_index": frame.position,
        },
    )
    return SensorFrame(
        read_id=frame.frame_id,
        readings={FRONT_CAMERA_SENSOR_ID: reading},
        started_at_ms=frame.timestamp_ms,
        completed_at_ms=frame.timestamp_ms,
        request={
            "source": "workbench.image_replay.v1",
            "requested_sensors": [FRONT_CAMERA_SENSOR_ID],
        },
        metadata={
            "source_id": frame.source_id,
            "sequence_index": frame.position,
            "absence": False,
        },
    )


def workbench_decision_steps() -> dict[str, Any]:
    """The same built-in observation, plan, and action used by a vehicle host."""

    steps = decision_steps()
    return {"observation": steps.observation, "plan": steps.plan, "action": steps.action}


def decision_view(result: Any) -> dict[str, Any] | None:
    """The decision steps' records for the workbench timeline."""

    action = result.action
    if action is None:
        return None
    return {
        "frame_id": action.frame_id,
        "status": action.status,
        "reason": action.reason,
        "proposal": result.proposal.to_dict() if result.proposal is not None else None,
        "plan": result.plan.to_dict() if result.plan is not None else None,
        "authority": action.authority.to_dict(),
    }


@dataclass(frozen=True)
class FrameOutcome:
    result: Any
    decision: dict[str, Any] | None
    # Each step runner's plugin report after the frame, by step.
    plugin_reports: dict[str, dict[str, Any] | None]


def run_frame(
    frame: ReplayFrame,
    *,
    steps: Mapping[str, Any],
    shared_memory: dict[str, Any],
) -> FrameOutcome:
    """Run one frame through the step runners in ``steps``, keyed by step.

    Perception is required and is adapted to the replay frame; observation is
    built from it. Every other step runs its runner, and a missing step is
    skipped as the cycle skips it.
    """

    perception_step = steps["perception"]

    sensor_frame = sensor_frame_for_replay_frame(frame)
    recorded_context = frame.metadata.get("context", {})
    context = DecisionFrameContext(
        frame_id=frame.frame_id,
        frame_index=frame.frame_index,
        timestamp_ms=frame.timestamp_ms,
        sensor_frame=sensor_frame,
        mode=recorded_context.get("mode", "workbench_replay"),
        user_steering=recorded_context.get("user_steering", 0.0),
        user_throttle=recorded_context.get("user_throttle", 0.0),
        shared_memory=shared_memory,
        metadata={
            "source": WORKBENCH_SEQUENCE_ID,
            "source_id": frame.source_id,
            "sequence_index": frame.position,
        },
    )

    def perceive(current: DecisionFrameContext) -> PerceptionText | None:
        if perception_step is None:
            return None
        if frame.absent or current.sensor_frame is None:
            perception_step.reset(current.shared_memory)
            return None
        return run_perception(
            perception_step,
            current.sensor_frame,
            shared_memory=current.shared_memory,
            metadata={
                "source": WORKBENCH_SEQUENCE_ID,
                "source_id": frame.source_id,
                "sequence_index": frame.position,
            },
        )

    def observe(
        current: DecisionFrameContext,
        perception: PerceptionText | None,
    ) -> Observation:
        return observation_from_perception(
            observation_id=f"{frame.source_id}:{frame.frame_id}",
            sensor_frame=current.sensor_frame,
            perception=perception,
            metadata={
                "source": WORKBENCH_SEQUENCE_ID,
                "source_id": frame.source_id,
                "sequence_index": frame.position,
                "absence_reason": frame.absence_reason,
            },
            created_at_ms=frame.timestamp_ms,
        )

    result = DecisionCycle(
        DecisionSteps(
            perception=perceive,
            observation=steps.get("observation", observe),
            memory=steps.get("memory"),
            proposal=steps.get("proposal"),
            plan=steps.get("plan"),
            action=steps.get("action"),
        ),
    ).run(context)
    return FrameOutcome(
        result=result,
        decision=decision_view(result),
        plugin_reports={step: plugin_report(runner) for step, runner in steps.items()},
    )
