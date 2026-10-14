from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.steps import decision_steps
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from cli.automa_cli.runtime_view import RuntimeViewServer
from implementations.runtime.chase_sim import create_host as create_chase_host
from implementations.runtime.picar import create_host as create_picar_host
from implementations.vehicle.chase_sim import ChaseSimCar
from tests.shared.cli.invoke import invoke_automa
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


@contextmanager
def plugin_host(root, vehicle="chase", activations=None):
    """A vehicle host in this process, with its catalog served at the yielded URL."""
    steps = decision_steps(activations)
    host = (create_chase_host(ChaseSimCar(), steps=steps) if vehicle == "chase"
            else create_picar_host(steps=steps))
    host.follow_activations(activations or {}, root / "runtime")
    view = RuntimeViewServer(vehicle_id=vehicle, automation_dir=root / "automation",
                             port=0, plugin_catalog=host.plugin_catalog).start()
    try:
        yield host, view.url
    finally:
        view.stop()
        host.close()


def upload(base, file, step, plugin_id, *, arm=False, check=True):
    command = ["vehicles", "plugins", "upload", "--url", base, "--file", str(file),
               "--step", step, "--plugin-id", plugin_id, "--entrypoint", "prototype:Prototype", "--json"]
    if arm:
        command.append("--arm")
    result = invoke_automa(*command, check=check)
    return result, json.loads(result.stdout)


def catalog(base):
    return json.loads(invoke_automa("vehicles", "plugins", "status", "--url", base, "--json").stdout)


def arm_step(base, step, *ids):
    command = ["vehicles", "plugins", "arm", "--url", base, "--step", step, "--json"]
    for plugin_id in ids:
        command.extend(["--plugin", plugin_id])
    return json.loads(invoke_automa(*command).stdout)


def selected(state, phase, step):
    return state["arming"][phase][step]


def proposal(result, plugin_id):
    return next(item for item in result.proposal.candidates if item.plugin_id == plugin_id)


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
    """Self-contained plugins with runtime dependencies, one per step."""
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
    elif step == "proposal":
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
    else:
        # One-plugin steps reuse the packaged plugin under the prototype's id.
        base = {
            "observation": ("autonomy.decision_cycle.observation.perception_summary", "PerceptionSummary"),
            "plan": ("autonomy.decision_cycle.plan.highest_confidence", "HighestConfidencePlan"),
            "action": ("autonomy.decision_cycle.action.selected", "SelectedAction"),
        }[step]
        source = (
            f"from {base[0]} import {base[1]}\n"
            f"class Prototype({base[1]}):\n"
            f"    plugin_id = {plugin_id!r}\n"
            f"    revision = {revision}\n"
        )
    file.write_text(source)
