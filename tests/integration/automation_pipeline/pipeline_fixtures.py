from __future__ import annotations
import json
import time
from pathlib import Path
from PIL import Image
from autonomy.perception import PERCEPTION_TEXT_SCHEMA, PerceptionText
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot


class _SlowMapper:
    def __init__(self) -> None:
        self.frame_ids: list[str] = []

    def perceive(self, request):
        self.frame_ids.append(request.snapshot.read_id)
        time.sleep(0.05)
        return PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id="test.slow-mapper",
            status="empty",
            lines=(f"schema={PERCEPTION_TEXT_SCHEMA}", "plugin=test.slow-mapper"),
            signals=(),
            things=(),
        )


class _FakeCar:
    def __init__(self, **_kwargs) -> None:
        self.capture_count = 0
        self.last_capture_shadow_reference: dict | None = None
        self.last_passive_capture: dict | None = None
        self.last_simulator_frame_index: int | None = None

    def prepare_for_external_control(self):
        raise AssertionError("observe-only automation must not take control")

    def stop(self):
        raise AssertionError("observe-only automation must not send idle control")

    def read_sensors(self, request):
        now_ms = int(time.time() * 1000)
        path = request.front_camera_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Simulate advancing Chase play_debug frameIndex values.
        simulator_frame_index = 100 + self.capture_count
        Image.new("RGB", (64, 48), (self.capture_count % 256, 40, 60)).save(path)
        self.capture_count += 1
        self.last_simulator_frame_index = simulator_frame_index
        self.last_passive_capture = {
            "status": "available",
            "environment": {
                "scenario_id": "fixture-current",
                "playback": {"isPlaying": True},
                "control_source": "builtin",
                "control_input": {"motion": "forward"},
            },
            "session_preservation": {
                "preserved": True,
                "changed_fields": [],
                "unknown_fields": [],
            },
            "mutation_attempted": False,
        }
        self.last_capture_shadow_reference = {
            "schema": "chase_shadow_reference_v1",
            "evaluator_only": True,
            "simulator_frame_index": simulator_frame_index,
            "simulation_epoch": "chase-run:test",
            "frame_id": f"chase_frame_{simulator_frame_index:06d}",
            "game_id": "chase",
            "scenario": "chaser-depth-obstacles",
            "chaser_control_source": "builtin",
        }
        reading = SensorReading(
            sensor_id=FRONT_CAMERA_SENSOR_ID,
            sensor_kind="camera",
            captured_at_ms=now_ms,
            path=str(path),
            metadata={
                "content_type": "image/png",
                "simulator_frame_index": simulator_frame_index,
                "simulation_epoch": "chase-run:test",
                "frame_index": simulator_frame_index,
                "frame_id": f"chase_frame_{simulator_frame_index:06d}",
            },
        )
        return SensorSnapshot(
            read_id=request.read_id,
            readings={FRONT_CAMERA_SENSOR_ID: reading},
            started_at_ms=now_ms,
            completed_at_ms=now_ms,
            request=request.to_dict(),
            metadata={
                "simulator_frame_index": simulator_frame_index,
                "simulation_epoch": "chase-run:test",
                "frame_id": f"chase_frame_{simulator_frame_index:06d}",
            },
        )


class _ExitedProcess:
    pid = 42424

    def poll(self):
        return 7


class _RunningProcess:
    pid = 43434

    def poll(self):
        return None


def _write_activations(bundle: dict[str, str]) -> None:
    perception_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    decision_path = Path(bundle["decision_runtime_dir"]) / "active.json"
    perception_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    perception_path.write_text(
        json.dumps(
            {
                "schema": "automa_perception_activation_v0",
                "controller_bundle": {"root_dir": bundle["root_dir"]},
                "perception": {
                    "algorithm": "test",
                    "mapper_spec": "test:SlowMapper",
                    "mapper_config": {},
                },
            }
        ),
        encoding="utf-8",
    )
    decision_path.write_text(
        json.dumps(
            {
                "schema": "automa_decision_activation_v0",
                "controller_bundle": {"root_dir": bundle["root_dir"]},
                "decision": {
                    "engine_id": "idle",
                    "engine_spec": "autonomy.runtime.engine:IdleAutonomyEngine",
                    "engine_config": {},
                },
            }
        ),
        encoding="utf-8",
    )
