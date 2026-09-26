"""Behavioral ownership checks across the shipped perception plugin catalog."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import re
import unittest
from unittest.mock import patch

import numpy as np

from autonomy.perception import build_perception_request
from autonomy.perception.mappers import PluginPerceptionMapper
from autonomy.vehicle import SensorSnapshot
from implementations.perception.catalog import PERCEPTION_PLUGIN_SPECS
from lab.plugins.perception.fastsam.src.plugin import FastSamRegionPlugin
from tests.implementations.perception.test_plugins import _snapshot, _array_reading


LAB_SPECS = {
    "floor_continuity_temporal": "lab.plugins.perception.floor_continuity_temporal.src.plugin:TemporalFloorContinuityPlugin",
    "composite_box_fusion": "lab.plugins.perception.composite_box_fusion.src.plugin:CompositeBoxFusionPlugin",
    "composite_box_fusion_object_separated": "lab.plugins.perception.composite_box_fusion_object_separated.src.plugin:CompositeBoxFusionPlugin",
    "classical_regions": "lab.plugins.perception.classical_regions.src.plugin:ClassicalRegionPlugin",
    "floor_continuity": "lab.plugins.perception.floor_continuity.src.plugin:FloorContinuityPlugin",
    "floor_continuity_capture": "lab.plugins.perception.floor_continuity_capture.src.plugin:CaptureFloorContinuityPlugin",
    "fastsam": "lab.plugins.perception.fastsam.src.plugin:FastSamRegionPlugin",
    "multi_obstruction_tracks": "lab.plugins.perception.multi_obstruction_tracks.src.plugin:MultiObstructionTracksPlugin",
}
SPECS = {**PERCEPTION_PLUGIN_SPECS, **LAB_SPECS}
CONFIGS = {
    "composite_box_fusion": {"issue_working_width": 320, "classical_working_width": 160, "jev_enabled": False},
    "composite_box_fusion_object_separated": {"issue_working_width": 320, "classical_working_width": 160, "jev_enabled": False},
    "motion_tracks": {"max_features": 30, "search_radius": 6, "min_group_size": 4},
    "fastsam": {"model_path": "unused-test-model.pt"},
}


def plain(value):
    if isinstance(value, np.ndarray):
        return {"dtype": str(value.dtype), "array": value.tolist()}
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


def semantic(result):
    payload = result.to_dict()
    for run in payload["plugin_runs"]:
        run.pop("duration_ms")
    payload["lines"] = [re.sub(r"duration_ms=[0-9.]+", "duration_ms=<timing>", line) for line in payload["lines"]]
    payload["text"] = "\n".join(payload["lines"])
    return plain(payload)


def mapper_for(plugin_id):
    return PluginPerceptionMapper(
        plugins=[plugin_id], plugin_specs=SPECS, plugin_configs=CONFIGS,
    )


class SharedMemoryContractTests(unittest.TestCase):
    def setUp(self):
        self.rgb = np.random.default_rng(7).integers(0, 256, (72, 96, 3), dtype=np.uint8)
        self.shifted = np.roll(self.rgb, 2, axis=1)

    def request(self, rgb, memory, output_dir=None):
        return build_perception_request(
            _snapshot(_array_reading(rgb), "frame"), memory=memory, output_dir=output_dir,
        )

    def test_lab_inventory_is_covered(self):
        root = Path(__file__).resolve().parents[3] / "lab/plugins/perception"
        specs = {json.loads(path.read_text())["plugin"]["entrypoint"] for path in root.glob("*/plugin.json")}
        self.assertEqual(specs, set(LAB_SPECS.values()))

    def test_catalog_recreation_clearing_and_interleaved_runs(self):
        # Model inference is an external dependency. Exercise the real FastSAM
        # adapter with deterministic masks while allowing the model to be cached.
        masks = np.zeros((1, 72, 96), dtype=bool)
        masks[:, 10:40, 20:60] = True
        model = SimpleNamespace(predict=lambda **kwargs: [SimpleNamespace(
            masks=SimpleNamespace(data=masks), boxes=SimpleNamespace(conf=np.array([0.8])), speed={},
        )])
        with TemporaryDirectory() as directory, patch.object(FastSamRegionPlugin, "_load_model", return_value=model):
            output_dir = Path(directory)
            for plugin_id in SPECS:
                with self.subTest(plugin=plugin_id):
                    mapper = mapper_for(plugin_id)
                    memory = {"unrelated": {"keep": 1}}
                    first = mapper.perceive(self.request(self.rgb, memory, output_dir))
                    self.assertNotIn(first.status, {"error", "unavailable"})
                    copied = deepcopy(memory)
                    # An unrelated run on the same instance must not affect A.
                    mapper.perceive(self.request(np.zeros_like(self.rgb), {}, output_dir))
                    second = mapper.perceive(self.request(self.shifted, memory, output_dir))
                    recreated = mapper_for(plugin_id).perceive(self.request(self.shifted, copied, output_dir))
                    self.assertEqual(semantic(second), semantic(recreated))
                    self.assertEqual(plain(memory), plain(copied))
                    memory.clear()
                    cleared = mapper.perceive(self.request(self.rgb, memory, output_dir))
                    fresh_memory = {}
                    fresh = mapper_for(plugin_id).perceive(self.request(self.rgb, fresh_memory, output_dir))
                    self.assertEqual(semantic(cleared), semantic(fresh))
                    self.assertEqual(plain(memory), plain(fresh_memory))

    def test_temporal_plugins_require_memory_and_reset_only_their_namespace(self):
        for plugin_id in ("motion_tracks", "obstruction_tracks", "floor_continuity_temporal"):
            with self.subTest(plugin=plugin_id):
                mapper = mapper_for(plugin_id)
                self.assertEqual(mapper.perceive(self.request(self.rgb, None)).status, "error")
                memory = {"other.plugin.history": 42}
                mapper.perceive(self.request(self.rgb, memory))
                self.assertGreater(len(memory), 1)
                missing = SensorSnapshot(read_id="absent", readings={}, started_at_ms=2, completed_at_ms=2)
                self.assertEqual(mapper.perceive(build_perception_request(missing, memory=memory)).status, "unavailable")
                self.assertEqual(memory, {"other.plugin.history": 42})
                restarted = mapper.perceive(self.request(self.rgb, memory))
                self.assertEqual(restarted.status, "warming_up" if plugin_id == "motion_tracks" else "ok")
                mapper.reset(memory)
                self.assertEqual(memory, {"other.plugin.history": 42})

    def test_failed_motion_step_does_not_commit_partial_history(self):
        mapper = mapper_for("motion_tracks")
        memory = {}
        mapper.perceive(self.request(self.rgb, memory))
        before = plain(memory)
        with patch.object(mapper.plugins[0], "_analyze", side_effect=RuntimeError("failed analysis")):
            result = mapper.perceive(self.request(self.shifted, memory))
        self.assertEqual(result.status, "error")
        self.assertEqual(plain(memory), before)
