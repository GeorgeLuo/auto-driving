from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import numpy as np

from autonomy.decision import DecisionFrameContext
from autonomy.decision.observation import observation_from_perception
from autonomy.perception import PerceptionEvidenceBatch, build_perception_request
from lab.plugins.memory.multi_obstruction_tracks.plugin import MultiObstructionMemory
from lab.plugins.perception.floor_continuity.src.plugin import FloorContinuityPlugin
from lab.plugins.perception.floor_continuity_temporal.src.plugin import TemporalFloorContinuityPlugin
from tests.implementations.perception.test_plugins import _snapshot, _array_reading
from tests.implementations.perception.test_shared_memory_contract import mapper_for, plain, semantic
from tests.lab.perception.test_floor_continuity_temporal import _boundary
from tests.lab.perception.test_multi_obstruction_tracks import _region


def _without_timing(value):
    """Wall-clock and measured durations are not part of the memory contract."""
    payload = plain(asdict(value) if hasattr(value, "__dataclass_fields__") else value)
    if isinstance(payload, dict) and "summary" in payload:
        payload["summary"] = [
            re.sub(r"duration_ms=[0-9.]+", "duration_ms=<timing>", line)
            for line in payload["summary"]
        ]
    if isinstance(payload, dict):
        for key, item in payload.items():
            if key == "summary":
                continue
            payload[key] = _without_timing(item) if isinstance(item, (dict, list)) else item
    elif isinstance(payload, list):
        payload = [_without_timing(item) if isinstance(item, (dict, list)) else item for item in payload]
    return payload


class ExperimentSharedMemoryTests(unittest.TestCase):
    def test_temporal_floor_holds_then_expires_across_recreated_plugins(self):
        mapper = mapper_for("floor_continuity_temporal")
        mapper.plugins = (TemporalFloorContinuityPlugin(max_hold_frames=1),)
        memory = {}
        rgb = np.zeros((72, 96, 3), dtype=np.uint8)
        request = lambda state: build_perception_request(_snapshot(_array_reading(rgb)), memory=state)
        boundary = _boundary("left", (0.18, 0.42, 0.36, 0.52), 0.8)
        with patch.object(FloorContinuityPlugin, "perceive", return_value=PerceptionEvidenceBatch(things=(boundary,))):
            mapper.perceive(request(memory))
        with patch.object(FloorContinuityPlugin, "perceive", return_value=PerceptionEvidenceBatch()):
            copied = deepcopy(memory)
            held = mapper.perceive(request(memory))
            mapper.plugins = (TemporalFloorContinuityPlugin(max_hold_frames=1),)
            self.assertEqual(semantic(held), semantic(mapper.perceive(request(copied))))
            self.assertTrue(held.things[0].properties["held"])
            expired = mapper.perceive(request(memory))
            self.assertFalse(expired.things)
            mapper.reset(memory)
            self.assertEqual(memory, {})

    def test_composite_candidates_feed_shared_tracking_companion(self):
        root = Path(__file__).resolve().parents[3]
        for name in ("composite_box_fusion", "composite_box_fusion_object_separated"):
            with self.subTest(plugin=name):
                manifest = json.loads((root / "lab/plugins/perception" / name / "plugin.json").read_text())
                self.assertEqual(manifest["memory"]["implementation_id"], "multi_obstruction_tracks")
                mapper = mapper_for(name)
                memory = {}
                rgb = np.zeros((100, 100, 3), dtype=np.uint8)
                snapshot = _snapshot(_array_reading(rgb))
                region = _region("box", (0.1, 0.2, 0.3, 0.5))

                def step(current_mapper, current_memory):
                    perception = current_mapper.perceive(build_perception_request(snapshot, memory=current_memory))
                    observation = observation_from_perception(
                        observation_id="frame", sensor_snapshot=snapshot, perception=perception,
                        created_at_ms=10,
                    )
                    MultiObstructionMemory().update(DecisionFrameContext(
                        frame_id="frame", frame_index=0, timestamp_ms=10,
                        sensor_snapshot=snapshot, memory=current_memory,
                    ), observation)
                    return current_memory["decision.observation"]

                with patch.object(type(mapper.plugins[0]), "_detect_candidates", return_value=([region], rgb[:, :, 0], {})):
                    first = step(mapper, memory)
                    self.assertEqual(first.things[0]["kind"], "obstacle")
                    copied = deepcopy(memory)
                    reused = step(mapper, memory)
                    recreated = step(mapper_for(name), copied)
                self.assertEqual(_without_timing(reused), _without_timing(recreated))
                self.assertEqual(_without_timing(memory), _without_timing(copied))
