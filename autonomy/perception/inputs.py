from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot

from .interface import CameraFrame, PerceptionRequest


def build_perception_request(
    snapshot: SensorSnapshot,
    *,
    output_dir: Path | None = None,
    previous_snapshot: SensorSnapshot | None = None,
    metadata: dict[str, Any] | None = None,
) -> PerceptionRequest:
    """Normalize camera readings once before any perception plugin runs."""

    camera_frames: dict[str, CameraFrame] = {}
    input_errors: dict[str, str] = {}

    for sensor_id, reading in snapshot.readings.items():
        if reading.sensor_kind != "camera":
            continue
        try:
            camera_frames[sensor_id] = _camera_frame_from_reading(reading)
        except (OSError, TypeError, ValueError) as exc:
            input_errors[sensor_id] = f"{type(exc).__name__}: {exc}"

    if FRONT_CAMERA_SENSOR_ID not in snapshot.readings:
        input_errors[FRONT_CAMERA_SENSOR_ID] = "sensor reading missing"

    return PerceptionRequest(
        snapshot=snapshot,
        camera_frames=camera_frames,
        input_errors=input_errors,
        output_dir=output_dir,
        previous_snapshot=previous_snapshot,
        metadata=dict(metadata or {}),
    )


def _camera_frame_from_reading(reading: SensorReading) -> CameraFrame:
    errors: list[str] = []
    rgb: np.ndarray | None = None
    source = "unknown"

    if reading.value is not None:
        try:
            rgb = _value_to_rgb(reading.value, color_space=reading.metadata.get("color_space"))
            source = "value"
        except (OSError, TypeError, ValueError) as exc:
            errors.append(f"value: {exc}")

    source_path = Path(reading.path).expanduser() if reading.path else None
    if rgb is None and source_path is not None:
        try:
            with Image.open(source_path) as image:
                rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
            source = "path"
        except (OSError, TypeError, ValueError) as exc:
            errors.append(f"path: {exc}")

    if rgb is None:
        detail = "; ".join(errors) if errors else "reading has neither value nor path"
        raise ValueError(detail)

    normalized = np.ascontiguousarray(rgb, dtype=np.uint8)
    normalized.setflags(write=False)
    return CameraFrame(
        sensor_id=reading.sensor_id,
        captured_at_ms=reading.captured_at_ms,
        rgb=normalized,
        source_path=source_path if source == "path" else None,
        metadata={**reading.metadata, "normalized_from": source, "color_space": "RGB"},
    )


def _value_to_rgb(value: Any, *, color_space: Any = None) -> np.ndarray:
    if isinstance(value, Image.Image):
        return np.asarray(value.convert("RGB"), dtype=np.uint8)
    if isinstance(value, (bytes, bytearray, memoryview)):
        with Image.open(BytesIO(bytes(value))) as image:
            return np.asarray(image.convert("RGB"), dtype=np.uint8)
    if not isinstance(value, np.ndarray):
        raise TypeError(f"unsupported camera value type {type(value).__name__}")

    array = value
    if array.ndim == 2:
        array = np.repeat(array[:, :, None], 3, axis=2)
    if array.ndim != 3 or array.shape[2] not in {3, 4}:
        raise ValueError(f"expected HxW, HxWx3, or HxWx4 array, got {array.shape}")
    if array.shape[2] == 4:
        array = array[:, :, :3]

    if np.issubdtype(array.dtype, np.floating):
        finite = np.nan_to_num(array, nan=0.0, posinf=255.0, neginf=0.0)
        if finite.size and float(finite.max()) <= 1.0:
            finite = finite * 255.0
        array = np.clip(finite, 0.0, 255.0).astype(np.uint8)
    else:
        array = np.clip(array, 0, 255).astype(np.uint8, copy=False)

    normalized_color_space = str(color_space or "RGB").upper()
    if normalized_color_space == "BGR":
        array = array[:, :, ::-1]
    elif normalized_color_space not in {"RGB", "RGBA"}:
        raise ValueError(f"unsupported camera color space {normalized_color_space!r}")
    return array
