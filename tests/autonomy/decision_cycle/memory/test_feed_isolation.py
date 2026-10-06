from __future__ import annotations

import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading


class _FeedProbe:
    plugin_id = "feed_probe"

    def __init__(self) -> None:
        self.seen: list[DecisionFrameContext] = []

    def update(self, context, observation) -> None:
        self.seen.append(context)

    def reset(self, shared_memory) -> None:
        pass


def _sensor_frame() -> SensorFrame:
    return SensorFrame(
        read_id="frame_0",
        readings={
            FRONT_CAMERA_SENSOR_ID: SensorReading(
                sensor_id=FRONT_CAMERA_SENSOR_ID,
                sensor_kind="camera",
                captured_at_ms=100,
                value=[[0]],
                metadata={},
            )
        },
        started_at_ms=100,
        completed_at_ms=100,
    )


class MemoryFeedIsolationTests(unittest.TestCase):
    def test_plugins_get_the_context_without_the_sensor_frame(self) -> None:
        probe = _FeedProbe()
        shared_memory: dict = {}
        context = DecisionFrameContext(
            "frame_0", 0, 100, sensor_frame=_sensor_frame(), shared_memory=shared_memory
        )

        MemoryRunner.from_plugins({"feed_probe": probe}).update(context, None)

        (seen,) = probe.seen
        self.assertIsNone(seen.sensor_frame)
        self.assertIs(seen.shared_memory, shared_memory)
        self.assertEqual((seen.frame_id, seen.frame_index, seen.timestamp_ms), ("frame_0", 0, 100))

    def test_the_hosts_context_keeps_its_sensor_frame(self) -> None:
        sensors = _sensor_frame()
        context = DecisionFrameContext("frame_0", 0, 100, sensor_frame=sensors, shared_memory={})

        MemoryRunner.from_plugins({"feed_probe": _FeedProbe()}).update(context, None)

        self.assertIs(context.sensor_frame, sensors)


if __name__ == "__main__":
    unittest.main()
