from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from autonomy.perception import (
    PerceptionComponentUnavailable,
    PerceptionPluginInput,
    PerceptionRequest,
)
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading


CAMERA_COMPONENT_KIND = "camera.rgb"


@dataclass(frozen=True)
class CameraFrame:
    """Normalized RGB component derived from one camera sensor reading."""

    sensor_id: str
    captured_at_ms: int
    rgb: np.ndarray = field(repr=False, compare=False)
    source_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.rgb, np.ndarray):
            raise TypeError("CameraFrame.rgb must be a numpy array")
        if self.rgb.dtype != np.uint8:
            raise TypeError("CameraFrame.rgb must use uint8 pixels")
        if self.rgb.ndim != 3 or self.rgb.shape[2] != 3:
            raise ValueError("CameraFrame.rgb must have shape (height, width, 3)")
        if self.rgb.shape[0] < 1 or self.rgb.shape[1] < 1:
            raise ValueError("CameraFrame.rgb must not be empty")

    @property
    def width_px(self) -> int:
        return int(self.rgb.shape[1])

    @property
    def height_px(self) -> int:
        return int(self.rgb.shape[0])

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "captured_at_ms": self.captured_at_ms,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "color_space": "RGB",
            "source_path": str(self.source_path) if self.source_path is not None else None,
            "metadata": self.metadata,
        }


def camera_component_id(sensor_id: str) -> str:
    return f"{CAMERA_COMPONENT_KIND}:{sensor_id}"


def camera_rgb_input(
    sensor_id: str,
    *,
    name: str = "frame",
) -> PerceptionPluginInput:
    return PerceptionPluginInput(
        name=name,
        component_id=camera_component_id(sensor_id),
        provider_spec="implementations.perception.components.camera:provide_camera_frame",
    )


FRONT_CAMERA_RGB_INPUT = camera_rgb_input(FRONT_CAMERA_SENSOR_ID)


def provide_camera_frame(
    request: PerceptionRequest,
    plugin_input: PerceptionPluginInput,
) -> CameraFrame:
    """Normalize one declared camera reading for framework injection."""

    prefix = f"{CAMERA_COMPONENT_KIND}:"
    if not plugin_input.component_id.startswith(prefix):
        raise ValueError(f"camera provider cannot resolve {plugin_input.component_id!r}")
    sensor_id = plugin_input.component_id.removeprefix(prefix)
    return _camera_frame_from_reading(request.sensor(sensor_id), sensor_id)


def _camera_frame_from_reading(
    reading: SensorReading | None,
    sensor_id: str,
) -> CameraFrame:
    if reading is None:
        raise PerceptionComponentUnavailable(f"sensor reading {sensor_id!r} is missing")
    if reading.sensor_kind != "camera":
        raise PerceptionComponentUnavailable(
            f"sensor reading {sensor_id!r} has kind {reading.sensor_kind!r}, expected 'camera'"
        )

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
        raise PerceptionComponentUnavailable(detail)

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
