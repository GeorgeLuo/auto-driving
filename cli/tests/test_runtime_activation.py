from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from autonomy.decision import DecisionFrameContext, DecisionStages
from autonomy.perception import ActivatedPerceptionStage, read_perception_activation
from autonomy.runtime import (
    AutonomyManager,
    apply_decision_activation,
    read_decision_activation,
)
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from cli.automa_cli.perception import CURRENT_MAPPER_SPEC, PERCEPTION_PLUGIN_SPECS
from implementations.runtime.donkeycar import AutonomyPilotPart


class RuntimeActivationTests(unittest.TestCase):
    def test_perception_activation_requires_an_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            activation_path = Path(tmp) / "active.json"
            activation_path.write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must be a JSON object"):
                read_perception_activation(activation_path)

    def test_perception_activation_runs_on_in_memory_camera_without_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            activation_path = Path(tmp) / "active.json"
            activation_path.write_text(
                json.dumps(
                    {
                        "schema": "automa_perception_activation_v0",
                        "perception": {
                            "algorithm": "test-observer",
                            "mapper_spec": CURRENT_MAPPER_SPEC,
                            "mapper_config": {
                                "plugins": ["frame"],
                                "plugin_specs": {"frame": PERCEPTION_PLUGIN_SPECS["frame"]},
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            stage = ActivatedPerceptionStage(read_perception_activation(activation_path))
            snapshot = SensorSnapshot(
                read_id="onboard-frame",
                readings={
                    FRONT_CAMERA_SENSOR_ID: SensorReading(
                        sensor_id=FRONT_CAMERA_SENSOR_ID,
                        sensor_kind="camera",
                        captured_at_ms=10,
                        value=np.zeros((24, 32, 3), dtype=np.uint8),
                    )
                },
                started_at_ms=10,
                completed_at_ms=10,
            )

            result = stage(
                DecisionFrameContext(
                    frame_id="onboard-frame",
                    frame_index=0,
                    timestamp_ms=10,
                    sensor_snapshot=snapshot,
                )
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.artifacts, {})
        stage_status = stage.status()
        self.assertEqual(stage_status["algorithm"], "test-observer")
        self.assertEqual(stage_status["last_status"], "ok")
        self.assertEqual(stage_status["last_frame_index"], 0)
        self.assertEqual(stage_status["last_thing_count"], 1)
        self.assertGreaterEqual(stage_status["last_duration_ms"], 0.0)
        self.assertEqual(
            [run["plugin_id"] for run in stage_status["last_plugin_runs"]],
            ["frame-observation-v0"],
        )

        manager = AutonomyManager()
        manager.register_status_provider("perception", stage.status)
        self.assertEqual(
            manager.status()["components"]["perception"]["algorithm"],
            "test-observer",
        )

        part = AutonomyPilotPart(
            host=AutonomyCycleHost(stages=DecisionStages(perceive=stage))
        )
        _steering, _throttle, _control, _engine, cycle = part.run(
            image_array=np.zeros((24, 32, 3), dtype=np.uint8),
            mode="local",
        )
        self.assertEqual(cycle["perception"]["status"], "ok")
        self.assertEqual(cycle["observation"]["perception_schema"], "perception_text_v1")

    def test_activation_loads_and_applies_the_declared_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            activation_path = Path(tmp) / "active.json"
            activation_path.write_text(
                json.dumps(
                    {
                        "schema": "automa_decision_activation_v0",
                        "decision": {
                            "engine_id": "idle",
                            "engine_spec": "autonomy.runtime.engine:IdleAutonomyEngine",
                            "engine_config": {},
                        },
                    }
                ),
                encoding="utf-8",
            )

            activation = read_decision_activation(activation_path)
            manager = AutonomyManager()
            status = apply_decision_activation(manager, activation)

        self.assertEqual(activation.engine_id, "idle")
        self.assertEqual(status["engine"], "autonomy.runtime.engine:IdleAutonomyEngine")

    def test_activation_rejects_an_unknown_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            activation_path = Path(tmp) / "active.json"
            activation_path.write_text(
                json.dumps({"schema": "old_schema", "decision": {}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unsupported schema"):
                read_decision_activation(activation_path)

    def test_activation_file_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                read_decision_activation(Path(tmp) / "active.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
