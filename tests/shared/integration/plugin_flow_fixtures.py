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
