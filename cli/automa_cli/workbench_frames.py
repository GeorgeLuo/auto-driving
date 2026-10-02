"""One workbench frame through the decision cycle, without runner state.

The runner owns phases, history, and locking; this module only turns a
replay frame and the active steps into a cycle result.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionCycle, DecisionSteps
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.observation.perception_summary import observation_from_perception
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.interface import PerceptionBackend, PerceptionText
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.decision_cycle.steps import decision_steps
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.decision_cycle.catalog import packaged_activation, selection_activation

from .perception_runs import run_perception
from .workbench_contract import WORKBENCH_SEQUENCE_ID
from .workbench_source import ReplayFrame


def snapshot_for_frame(frame: ReplayFrame) -> SensorSnapshot | None:
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
    return SensorSnapshot(
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


def default_mapper() -> PerceptionBackend:
    return PerceptionRunner.from_activation(selection_activation("perception"))


def default_memory_step() -> MemoryRunner:
    return MemoryRunner.from_activation(selection_activation("memory"))


def workbench_decision_steps() -> Any:
    """The packaged proposals, built-in plan, and hold action the workbench replays."""

    return decision_steps({"proposal": packaged_activation("proposal")})


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


def plugin_report(owner: Any) -> dict[str, Any] | None:
    """Copy a step's common plugin envelope, when that step publishes one."""

    report_for = getattr(owner, "plugin_report", None)
    if not callable(report_for):
        return None
    report = report_for()
    if not isinstance(report, dict):
        return None
    return copy.deepcopy(report)


@dataclass(frozen=True)
class FrameOutcome:
    result: Any
    decision: dict[str, Any] | None
    perception_plugin_report: dict[str, Any] | None
    memory_plugin_report: dict[str, Any] | None


def run_frame(
    frame: ReplayFrame,
    *,
    mapper: PerceptionBackend,
    memory_step: Any,
    steps: Any,
    shared_memory: dict[str, Any],
) -> FrameOutcome:
    """Run one frame through every step with the given active plugins."""

    snapshot = snapshot_for_frame(frame)
    context = DecisionFrameContext(
        frame_id=frame.frame_id,
        frame_index=frame.frame_index,
        timestamp_ms=frame.timestamp_ms,
        sensor_snapshot=snapshot,
        mode="workbench_replay",
        shared_memory=shared_memory,
        metadata={
            "source": WORKBENCH_SEQUENCE_ID,
            "source_id": frame.source_id,
            "sequence_index": frame.position,
        },
    )

    def perceive(current: DecisionFrameContext) -> PerceptionText | None:
        if frame.absent or current.sensor_snapshot is None:
            mapper.reset(current.shared_memory)
            return None
        return run_perception(
            mapper,
            current.sensor_snapshot,
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
            sensor_snapshot=current.sensor_snapshot,
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
            observation=observe,
            memory=memory_step,
            proposal=steps.proposal,
            plan=steps.plan,
            action=steps.action,
        ),
    ).run(context)
    decision_payload = decision_view(result)
    perception_plugin_report = plugin_report(mapper)
    memory_plugin_report = plugin_report(memory_step)

    return FrameOutcome(
        result=result,
        decision=decision_payload,
        perception_plugin_report=perception_plugin_report,
        memory_plugin_report=memory_plugin_report,
    )
