from __future__ import annotations
import json
import io
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
import numpy as np

from PIL import Image
from unittest.mock import patch
from autonomy.decision_cycle.activation import step_activation
from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.perception.interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionText,
)
from implementations.decision_cycle.catalog import packaged_activation, preset_activation
# Capture threads keep the host type while staged runners swap implementations.
from implementations.vehicle.chase_sim import ChaseCameraCapture
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from cli.automa_cli.releases.paths import controller_bundle_paths

from tests.support.runtime_fixtures import write_host_fixture

HOST_NAME = "chase-sim-chaser"


class _SlowMapper:
    """A perception runner stand-in that records frames and perceives slowly."""

    plugins = ()

    def __init__(self) -> None:
        self.frame_ids: list[str] = []

    def __call__(self, context):
        return self.perceive(build_perception_request(context.sensor_frame))

    def reset(self, shared_memory=None) -> None:
        del shared_memory

    def status(self) -> dict:
        return {"step": "perception", "frames": len(self.frame_ids)}

    def perceive(self, request):
        self.frame_ids.append(request.sensor_frame.read_id)
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
    def __init__(self, *, simulator_frame_stride: int = 1, **_kwargs) -> None:
        self.capture_count = 0
        self.streams = []
        self.auto_emit = True
        self.simulator_frame_stride = simulator_frame_stride
        self.last_capture_chaser_reference: dict | None = None
        self.last_passive_capture: dict | None = None

    def prepare_for_external_control(self):
        raise AssertionError("observe-only automation must not take control")

    def stop(self):
        raise AssertionError("observe-only automation must not send idle control")

    def read_sensors(self, request):
        frame = self._camera_frame()
        path = request.front_camera_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        reading = frame.readings[FRONT_CAMERA_SENSOR_ID]
        Image.fromarray(reading.value).save(path)
        return replace(frame, read_id=request.read_id, request=request.to_dict(), readings={
            FRONT_CAMERA_SENSOR_ID: replace(reading, path=str(path), value=None),
        })

    def camera_stream(self):
        stream = _PushStream(self)
        self.streams.append(stream)
        return stream

    def _camera_frame(self):
        now_ms = int(time.time() * 1000)
        # Simulate advancing Chase play_debug frameIndex values.
        simulator_frame_index = 100 + self.simulator_frame_stride * self.capture_count
        image = np.array(Image.new("RGB", (64, 48), (self.capture_count % 256, 40, 60)))
        self.capture_count += 1
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
            "schema": "chaser_reference",
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
            value=image,
            metadata={
                "content_type": "image/png",
                "simulator_frame_index": simulator_frame_index,
                "simulation_epoch": "chase-run:test",
                "frame_index": simulator_frame_index,
                "frame_id": f"chase_frame_{simulator_frame_index:06d}",
            },
        )
        return SensorFrame(
            read_id=f"capture-{self.capture_count}",
            readings={FRONT_CAMERA_SENSOR_ID: reading},
            started_at_ms=now_ms,
            completed_at_ms=now_ms,
            metadata={
                "simulator_frame_index": simulator_frame_index,
                "simulation_epoch": "chase-run:test",
                "frame_id": f"chase_frame_{simulator_frame_index:06d}",
            },
        )


class _PushStream:
    def __init__(self, car):
        self.car = car
        self.consumer = None
        self.auto_emit = car.auto_emit
        self.closed = threading.Event()
        self._lock = threading.Lock()
    def __enter__(self):
        return self
    def __exit__(self, *_exc):
        self.close()
    def set_consumer(self, consumer):
        self.consumer = consumer
    def check_progress(self):
        if self.auto_emit:
            self.emit()
    def emit(self):
        with self._lock:
            if self.closed.is_set() or self.consumer is None:
                return
            frame = self.car._camera_frame()
            reading = frame.readings[FRONT_CAMERA_SENSOR_ID]
            output = io.BytesIO()
            Image.fromarray(reading.value).save(output, "JPEG")
            self.consumer(ChaseCameraCapture(
                frame.metadata["frame_id"], frame.metadata["simulator_frame_index"],
                frame.metadata["simulation_epoch"], reading.captured_at_ms,
                lambda: reading.value, output.getvalue(),
            ))
    def close(self):
        self.closed.set()


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
    preset: str = "lightweight_observer",
    plugins: list[str] | None = None,
) -> None:
    """Stage perception and the packaged proposals. Plan has no built-in and stays unstaged."""

    root = Path(bundle["root_dir"]).parent
    write_host_fixture(root.parent, root.name)
    packaged = preset_activation("perception", preset)
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

    from autonomy.runtime.plugin_loader import load_runner as load_installed_or_bundle

    def load_runner(activation, *, source=None):
        if activation.step == "perception" and perception is not None:
            perception.activation = activation
            perception.plugin_ids = tuple(activation.plugins)
            return perception
        runner = load_installed_or_bundle(activation, source=source)
        if wrap is not None:
            wrap(activation.step, getattr(runner, "runner", runner))
        return runner

    return patch("autonomy.decision_cycle.steps.load_runner", side_effect=load_runner)


@contextmanager
def chase_runtime(runtime_root: Path, *, car=None, host_name: str = HOST_NAME):
    """Serve the vehicle's staged runtime from an in-process Chase host.

    The host writes the record a started Chase host writes, so the
    CLI (in process or as a subprocess under ``runtime_root``) finds it by
    ``base_url``. Enter it inside ``staged_runners``: the host loads its steps
    when constructed.
    """

    import os

    from implementations.runtime.chase_sim.service import (
        HOST_RECORD_SCHEMA, ChaseRuntimeHost, write_host_record,
    )
    from cli.automa_cli.hosts.local import HOST_RECORD

    write_host_fixture(runtime_root, host_name)
    bundle = controller_bundle_paths(runtime_root / host_name)
    with ExitStack() as stack:
        stack.enter_context(patch("cli.automa_cli.hosts.staging.RUNTIME_ROOT", runtime_root))
        stack.enter_context(patch("cli.automa_cli.releases.paths.RUNTIME_ROOT", runtime_root))
        host = stack.enter_context(ChaseRuntimeHost(
            car=_FakeCar() if car is None else car,
            runtime_dir=Path(bundle["runtime_dir"]), host_name=host_name,
        ))
        record = Path(bundle["runtime_dir"]) / "automation" / HOST_RECORD
        write_host_record(record, {
            "schema": HOST_RECORD_SCHEMA, "host_name": host_name,
            "pid": os.getpid(), "base_url": host.base_url,
        })
        stack.callback(record.unlink, missing_ok=True)
        # Starting a host builds it from a release; this one is already serving.
        stack.enter_context(patch("cli.automa_cli.hosts.runs.automation.runtime_base_url", return_value=host.base_url))
        yield host
