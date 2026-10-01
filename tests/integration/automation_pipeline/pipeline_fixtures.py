from __future__ import annotations
import json
import time
from pathlib import Path
from PIL import Image
from unittest.mock import patch
from autonomy.decision_cycle.activation import step_activation
from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.perception.interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionText,
)
from cli.automa_cli import automation as automation_module
from implementations.decision_cycle.catalog import packaged_activation, perception_algorithm_activation
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot


class _SlowMapper:
    """A perception runner stand-in that records frames and perceives slowly."""

    plugins = ()

    def __init__(self) -> None:
        self.frame_ids: list[str] = []

    def __call__(self, context):
        return self.perceive(build_perception_request(context.sensor_snapshot))

    def reset(self, shared_memory=None) -> None:
        del shared_memory

    def status(self) -> dict:
        return {"step": "perception", "frames": len(self.frame_ids)}

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
        self.last_capture_chaser_reference: dict | None = None
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
        self.last_capture_chaser_reference = {
            "schema": "chaser_reference_v1",
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


def _write_activations(
    bundle: dict[str, str],
    *,
    algorithm: str = "lightweight_observer",
    plugins: list[str] | None = None,
) -> None:
    """Stage perception and the packaged proposals; plan and action run their built-ins."""

    packaged = perception_algorithm_activation(algorithm)
    activation = step_activation(
        "perception",
        packaged.plugins if plugins is None else plugins,
        packaged.plugin_specs,
        packaged.plugin_configs,
        metadata={**packaged.metadata, "controller_bundle": {"root_dir": bundle["root_dir"]}},
    )
    perception_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    perception_path.parent.mkdir(parents=True, exist_ok=True)
    perception_path.write_text(json.dumps(activation.to_payload()), encoding="utf-8")
    proposal_path = Path(bundle["proposal_runtime_dir"]) / "active.json"
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text(json.dumps(packaged_activation("proposal").to_payload()), encoding="utf-8")


def staged_runners(*, perception=None, wrap=None):
    """Patch the worker's step loading: substitute ``perception`` and/or ``wrap`` loaded runners."""

    load = automation_module.load_staged_runner

    def load_runner(activation):
        if activation.step == "perception" and perception is not None:
            return perception
        runner = load(activation)
        if wrap is not None:
            wrap(activation.step, getattr(runner, "runner", runner))
        return runner

    return patch("cli.automa_cli.automation.load_staged_runner", side_effect=load_runner)
