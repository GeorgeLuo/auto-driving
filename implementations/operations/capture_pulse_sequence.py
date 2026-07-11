from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from autonomy.perception.core import observe_frame
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, CarInterface, SensorReadRequest, VehiclePulse


@dataclass(frozen=True)
class CapturePulseStep:
    """One labeled capture -> pulse -> capture operation."""

    label: str
    pulse: VehiclePulse


def safe_label_suffix(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value).strip("_")


def run_capture_pulse_sequence(
    *,
    car: CarInterface,
    steps: Iterable[CapturePulseStep],
    frames_dir: Path,
    frame_endpoint: str = "/frame.jpg",
    image_extension: str = "jpg",
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Run capture-before, pulse, capture-after for each step.

    This is intentionally unaware of startup-check scoring. It only exercises a
    vehicle through the black-box `CarInterface` and records raw captures.
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for index, step in enumerate(steps):
        label = safe_label_suffix(step.label)
        before_snapshot = car.read_sensors(
            SensorReadRequest(
                output_dir=frames_dir,
                read_id=f"{index:02d}_{label}_before",
                requested_sensors=(FRONT_CAMERA_SENSOR_ID,),
                front_camera_endpoint=frame_endpoint,
                image_extension=image_extension,
            ),
        )
        before_reading = before_snapshot.readings[FRONT_CAMERA_SENSOR_ID]
        if before_reading.path is None:
            raise RuntimeError(f"sensor {FRONT_CAMERA_SENSOR_ID!r} did not return an image path")
        before_path = Path(before_reading.path)
        before_capture = before_reading.metadata
        before_observation = observe_frame(before_path)
        command: dict[str, Any] | None = None

        if dry_run:
            time.sleep(step.pulse.duration_s + step.pulse.settle_s)
        else:
            command = car.execute_pulse(step.pulse)

        after_snapshot = car.read_sensors(
            SensorReadRequest(
                output_dir=frames_dir,
                read_id=f"{index:02d}_{label}_after",
                requested_sensors=(FRONT_CAMERA_SENSOR_ID,),
                front_camera_endpoint=frame_endpoint,
                image_extension=image_extension,
            ),
        )
        after_reading = after_snapshot.readings[FRONT_CAMERA_SENSOR_ID]
        if after_reading.path is None:
            raise RuntimeError(f"sensor {FRONT_CAMERA_SENSOR_ID!r} did not return an image path")
        after_path = Path(after_reading.path)
        after_capture = after_reading.metadata
        after_observation = observe_frame(after_path, previous_path=before_path)

        results.append(
            {
                "index": index,
                "label": step.label,
                "pulse": step.pulse.to_dict(),
                "dry_run": dry_run,
                "before_sensor_snapshot": before_snapshot.to_dict(),
                "after_sensor_snapshot": after_snapshot.to_dict(),
                "before_capture": before_capture,
                "after_capture": after_capture,
                "before_observation": before_observation,
                "after_observation": after_observation,
                "command": command,
                "before_path": str(before_path),
                "after_path": str(after_path),
            }
        )

    return results
