from __future__ import annotations

import time
from pathlib import Path

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from tests.shared.cli.workbench_fixtures import (
    ImageReplayRunner,
    _wait_until,
    get_json,
    post_action,
    serve_workbench,
)


def frame(root, index):
    now = int(time.time() * 1000)
    reading = SensorReading(
        sensor_id=FRONT_CAMERA_SENSOR_ID,
        sensor_kind="camera",
        captured_at_ms=now,
        path=str(next(root.glob("*.png"))),
        metadata={"content_type": "image/png"},
    )
    sensor = SensorFrame(
        read_id=f"frame-{index}",
        readings={FRONT_CAMERA_SENSOR_ID: reading},
        started_at_ms=now,
        completed_at_ms=now,
    )
    return DecisionFrameContext(f"frame-{index}", index, now, sensor_frame=sensor)


def replay(test, recording: Path):
    runner = ImageReplayRunner(recording, cadence_ms=30000, loop=True)
    base = serve_workbench(test, runner)
    response = post_action(base, {"action": "start"})["state"]
    _wait_until(lambda: get_json(base, "/api/state")["current_frame"] is not None)
    post_action(base, {"action": "seek", "run_id": response["run_id"], "position": 0})
    return base, response["run_id"]


def candidate(state, plugin_id):
    return next(
        item
        for item in state["steps"]["decision"]["proposal"]["candidates"]
        if item["plugin_id"] == plugin_id
    )


def write_plugin(file: Path, step: str, plugin_id: str, revision: int) -> None:
    """Self-contained plugins with runtime dependencies across the three steps."""
    if step == "perception":
        source = (
            "from dataclasses import replace\n"
            "from implementations.decision_cycle.perception.plugins.frame.plugin import FrameObservationPlugin\n"
            "class Prototype(FrameObservationPlugin):\n"
            f"    plugin_id = {plugin_id!r}\n"
            "    def perceive(self, inputs):\n"
            f"        inputs.shared_memory['prototype.perception'] = {revision}\n"
            "        result = super().perceive(inputs)\n"
            f"        return replace(result, measurements={{**result.measurements, 'revision': {revision}}})\n"
        )
    elif step == "memory":
        source = (
            "class Prototype:\n"
            f"    plugin_id = {plugin_id!r}\n"
            "    def update(self, context, observation):\n"
            "        memory = context.shared_memory\n"
            "        prior = memory.get('prototype.memory', {})\n"
            f"        memory['prototype.memory'] = {{'revision': {revision}, 'perception': memory['prototype.perception'], 'count': prior.get('count', 0) + 1}}\n"
            "    def reset(self, shared_memory):\n"
            "        shared_memory.pop('prototype.memory', None)\n"
            "    def status(self, shared_memory):\n"
            "        return dict((shared_memory or {}).get('prototype.memory', {}))\n"
        )
    else:
        source = (
            "from autonomy.decision_cycle.proposal.values import ActionProposal\n"
            "class Prototype:\n"
            f"    plugin_id = {plugin_id!r}\n"
            "    def propose(self, source, shared_memory):\n"
            "        memory = dict(shared_memory.get('prototype.memory', {}))\n"
            "        return ActionProposal(plugin_id=self.plugin_id, frame_id=source.frame_id,\n"
            f"            lifecycle='inactive', freshness='none', confidence=0.0, reason='prototype revision {revision}',\n"
            f"            command=None, metadata={{'revision': {revision}, 'memory': memory}})\n"
        )
    file.write_text(source)
