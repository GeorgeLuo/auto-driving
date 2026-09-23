from __future__ import annotations

from copy import deepcopy
import unittest

import numpy as np

from autonomy.decision import DecisionFrameContext, Observation
from autonomy.perception import PerceptionSignal
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from lab.plugins.memory.multi_obstruction_tracks.plugin import MultiObstructionMemory
from tests.lab.perception.test_multi_obstruction_tracks import _region


def step(plugin, memory, index, rgb, *, candidate=True):
    frame_id = f"frame-{index}"
    now = index * 100
    sensors = SensorSnapshot(
        read_id=frame_id,
        readings={FRONT_CAMERA_SENSOR_ID: SensorReading(
            sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera",
            captured_at_ms=now, value=rgb, metadata={"color_space": "RGB"},
        )},
        started_at_ms=now, completed_at_ms=now,
    )
    observation = Observation(
        observation_id=frame_id, created_at_ms=now, sensor_snapshot={},
        things=(_region("box", (0.2, 0.2, 0.7, 0.7)).to_dict(),) if candidate else (),
        signals=(PerceptionSignal("multi_obstruction_candidates", True, 1.0, {
            "tracking_config": {"max_missed_frames": 2}, "normalization": {},
        }).to_dict(),),
    )
    return plugin.update(DecisionFrameContext(
        frame_id=frame_id, frame_index=index, timestamp_ms=now,
        sensor_snapshot=sensors, memory=memory,
    ), observation)


class SharedTrackingMemoryTests(unittest.TestCase):
    def test_recreated_plugin_preserves_motion_evidence_and_next_identity(self):
        rgb = np.random.default_rng(1).integers(0, 256, (200, 200, 3), dtype=np.uint8)
        memory = {}
        plugin = MultiObstructionMemory()
        step(plugin, memory, 0, rgb)
        self.assertTrue(memory["multi_obstruction_tracks.history"][0]["points"])
        copied = deepcopy(memory)
        shifted = np.roll(rgb, 3, axis=1)
        retained = step(plugin, memory, 1, shifted, candidate=False)
        recreated = step(MultiObstructionMemory(), copied, 1, shifted, candidate=False)
        self.assertEqual(retained.to_dict(), recreated.to_dict())
        self.assertEqual(memory["decision.observation"], copied["decision.observation"])
        self.assertEqual(memory["multi_obstruction_tracks.history"], copied["multi_obstruction_tracks.history"])
        track = memory["decision.observation"].things[0]
        self.assertEqual(track["properties"]["track_event"], "predicted")
        self.assertGreater(track["location"]["bbox_xyxy_norm"][0], 0.205)
        # No active/lost identities remain, but allocation still comes from memory.
        memory["multi_obstruction_tracks.history"] = []
        step(MultiObstructionMemory(), memory, 2, shifted)
        self.assertEqual(memory["decision.observation"].things[0]["thing_id"], "obstruction_track_001")

    def test_clearing_or_resetting_memory_removes_all_prior_evidence(self):
        rgb = np.zeros((100, 100, 3), dtype=np.uint8)
        memory = {}
        plugin = MultiObstructionMemory()
        step(plugin, memory, 0, rgb)
        memory.clear()
        snapshot = step(plugin, memory, 1, rgb, candidate=False)
        self.assertFalse(snapshot.records)
        self.assertFalse(memory["decision.observation"].things)
        step(plugin, memory, 2, rgb)
        reset = plugin.reset()
        self.assertFalse(reset.records)
        self.assertEqual(set(memory), {"decision.snapshot"})
        self.assertEqual(plugin.snapshot(), reset)
        self.assertNotEqual(plugin.reset().epoch_id, reset.epoch_id)
        step(plugin, memory, 3, rgb)
        self.assertEqual(plugin.snapshot().epoch_id, memory["decision.snapshot"].epoch_id)
